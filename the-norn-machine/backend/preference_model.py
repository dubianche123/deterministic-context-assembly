"""Trainable, personal pairwise preference ranking using only the standard library.

No personality labels or coordinates enter this model. A selected card is only
preferred relative to an unselected card displayed in that same round. Scores
are additive ranking logits, not probabilities of a person's characteristics.
"""

from copy import deepcopy
from functools import wraps
import hashlib
import json
import math
from pathlib import Path
import re

if __package__:
    from . import numeric
else:
    import numeric


MODEL_VERSION = "norn.preference.linear-bt.v2"
LEGACY_MODEL_VERSION = "norn.preference.linear-bt.v1"
CHECKPOINT_SCHEMA_VERSION = "norn.preference.checkpoint.v1"
FEATURE_SCHEMA_VERSION = "norn.features.v1"
DEFAULT_CONFIG = {"epochs": 40, "learning_rate": 0.8, "l2": 0.02,
                  "max_pairs_per_round": 32, "max_pairs": 4096}
MAX_FEATURES = 2048
MAX_CARDS = 512
MAX_JOURNALS = 64
MAX_TOTAL_EVENTS = 100_000
MAX_OBSERVATIONS = 1024
_CATALOG_CACHE = None


class _VerifiedCatalog(dict):
    """Internal immutable-by-convention snapshot; public loads stay detached."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def feature_label(name):
    """One human-readable label mapping for prompts, reports and the interface."""
    if name.startswith("trait."):
        return "画面特质「" + name[6:] + "」"
    if name.startswith("motif."):
        return "画面意象「" + name[6:] + "」"
    match = re.fullmatch(r"pixel\.rgb\.(red|green|blue)\.bin_([0-3])", name)
    if match:
        channel = {"red": "红", "green": "绿", "blue": "蓝"}[match[1]]
        band = ("低", "较低", "较高", "高")[int(match[2])]
        return channel + "色通道" + band + "强度占比"
    match = re.fullmatch(r"pixel\.(saturation|luma)\.bin_([0-5])", name)
    if match:
        band = ("极低", "低", "偏低", "偏高", "高", "极高")[int(match[2])]
        return ("饱和度" if match[1] == "saturation" else "明亮度") + band + "区间占比"
    match = re.fullmatch(r"pixel\.(saturation|luma)\.grid_([0-3])_([0-3])", name)
    if match:
        return f"画面第{int(match[2]) + 1}行第{int(match[3]) + 1}列分区的" + ("饱和度" if match[1] == "saturation" else "明亮度")
    match = re.fullmatch(r"pixel\.edge\.orientation_([0-7])", name)
    if match:
        lower = int(match[1]) * 22.5
        return f"明暗变化朝向约{lower:g}°至{lower + 22.5:g}°的边缘占比"
    # External fixture catalogs can name their own descriptors. Never invent a
    # psychological meaning for an unknown numerical feature.
    return "图像描述特征「" + str(name) + "」"


def _catalog_snapshot(catalog=None):
    """Validate and detach the feature authority, including its content hash."""
    global _CATALOG_CACHE
    if isinstance(catalog, _VerifiedCatalog):
        return catalog
    cache_key = None
    if catalog is None:
        path = Path(__file__).parent / "config" / "preference_features.json"
        stat = path.stat()
        cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
        if _CATALOG_CACHE is not None and _CATALOG_CACHE[0] == cache_key:
            return _CATALOG_CACHE[1]
        with path.open(encoding="utf-8") as stream:
            catalog = json.load(stream)
    catalog = deepcopy(catalog)
    if type(catalog) is not dict or catalog.get("schema_version") != FEATURE_SCHEMA_VERSION:
        raise ValueError("Unsupported preference feature schema")
    names, cards = catalog.get("feature_names"), catalog.get("cards")
    if (type(names) is not list or not 1 <= len(names) <= MAX_FEATURES
            or any(type(name) is not str or not name or len(name) > 200 for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("Invalid preference feature names")
    forbidden = ("coords", "mbti", "personality_type", "card_id", "group:", "scene:")
    if any(any(part in name.lower() for part in forbidden) or name in ("type", "group", "E_I", "S_N", "T_F", "J_P") for name in names):
        raise ValueError("Personality labels, card IDs and unique scenes are not preference features")
    if type(cards) is not dict or not 2 <= len(cards) <= MAX_CARDS:
        raise ValueError("Feature catalog needs 2 to 512 cards")
    for card_id, values in cards.items():
        if (type(card_id) is not str or not card_id or type(values) is not list
                or len(values) != len(names) or any(not _finite(value) for value in values)):
            raise ValueError("Invalid preference feature vector")
    version = _hash({key: value for key, value in catalog.items() if key != "feature_version"})
    if catalog.get("feature_version", version) != version:
        raise ValueError("Preference feature content hash mismatch")
    catalog["feature_version"] = version
    catalog = _VerifiedCatalog(catalog)
    if cache_key is not None:
        _CATALOG_CACHE = (cache_key, catalog)
    return catalog


def load_catalog(catalog=None):
    """Return a detached public catalog; unchanged files use a validated cache."""
    return deepcopy(dict(_catalog_snapshot(catalog)))


def _vectors(catalog):
    contract = numeric.current_contract() or numeric.NATIVE_CONTRACT
    if contract in getattr(catalog, "_vector_cache", {}):
        return catalog._vector_cache[contract]
    names, raw = catalog["feature_names"], catalog["cards"]
    if isinstance(catalog.get("normalization"), dict):
        # The shipped generator already scales continuous features, preserves
        # sparse semantic zeros, balances groups and normalizes whole vectors.
        for values in raw.values():
            norm = numeric.sqrt(math.fsum(value * value for value in values))
            if norm and not math.isclose(norm, 1.0, abs_tol=1e-8):
                raise ValueError("Precomputed preference vectors must have unit L2 norm")
        transform = {"method": "catalog_precomputed", "feature_version": catalog["feature_version"]}
        vectors = {card: [float(value) for value in values] for card, values in raw.items()}
    else:
        # Small external/fixture catalogs can supply raw continuous features.
        # These statistics depend only on descriptors, never preference labels.
        means = [math.fsum(row[index] for row in raw.values()) / len(raw) for index in range(len(names))]
        scales = [numeric.sqrt(math.fsum((row[index] - means[index]) ** 2 for row in raw.values()) / len(raw)) or 1.0
                  for index in range(len(names))]
        vectors = {}
        for card, row in raw.items():
            values = [(value - mean) / scale for value, mean, scale in zip(row, means, scales)]
            norm = numeric.sqrt(math.fsum(value * value for value in values))
            vectors[card] = [value / norm if norm else 0.0 for value in values]
        transform = {"method": "catalog_zscore_then_l2", "means": means, "scales": scales}
    sparse = {card: [(index, value) for index, value in enumerate(values) if value != 0.0]
              for card, values in vectors.items()}
    if isinstance(catalog, _VerifiedCatalog):
        if not hasattr(catalog, "_vector_cache"):
            catalog._vector_cache = {}
        catalog._vector_cache[contract] = (sparse, transform)
    return sparse, transform


def _config(config):
    supplied = config or {}
    if type(supplied) is not dict or set(supplied) - set(DEFAULT_CONFIG):
        raise ValueError("Unknown preference training configuration")
    values = {**DEFAULT_CONFIG, **supplied}
    for key, upper in (("epochs", 80), ("max_pairs_per_round", 64), ("max_pairs", 8192)):
        if type(values[key]) is not int or not 1 <= values[key] <= upper:
            raise ValueError(f"Invalid preference {key}")
    for key, low, high in (("learning_rate", 0.0001, 4.0), ("l2", 0.000001, 10.0)):
        if not _finite(values[key]) or not low <= values[key] <= high:
            raise ValueError(f"Invalid preference {key}")
    return values


_JOURNAL_FIELDS = {
    "norn.events.v1": {"round_started": {"round", "card_ids"}, "card_selected": {"round", "card_id"},
                       "card_deselected": {"round", "card_id"}, "round_completed": {"round", "duration_ms"},
                       "session_completed": set()},
    "norn.events.v2": {"round_started": {"round", "card_ids"},
                       "round_ready": {"round", "ready_offset_ms", "unloaded_card_ids"},
                       "card_selected": {"round", "card_id", "t_ms"},
                       "card_deselected": {"round", "card_id", "t_ms"},
                       "round_completed": {"round", "duration_ms", "hidden_ms", "seen_card_ids"},
                       "session_completed": set()},
    # v3 adds raw behaviour (layout, input, hover) this model does not read.
    "norn.events.v3": {"round_started": {"round", "card_ids"},
                       "round_ready": {"round", "ready_offset_ms", "unloaded_card_ids", "layout"},
                       "card_selected": {"round", "card_id", "t_ms", "input"},
                       "card_deselected": {"round", "card_id", "t_ms", "input"},
                       "round_completed": {"round", "duration_ms", "hidden_ms", "seen_card_ids", "hover"},
                       "session_completed": set()},
}
# Choice-time layouts are raw behaviour, like the initial layout in v3.
# They do not change pairwise observations or weights.
_JOURNAL_FIELDS["norn.events.v4"] = {
    **_JOURNAL_FIELDS["norn.events.v3"],
    "card_selected": {"round", "card_id", "t_ms", "input", "layout"},
    "card_deselected": {"round", "card_id", "t_ms", "input", "layout"},
}
# v5 display roles say why a card was shown; they do not change observations either.
_JOURNAL_FIELDS["norn.events.v5"] = {
    **_JOURNAL_FIELDS["norn.events.v4"],
    "round_started": {"round", "card_ids", "display"},
}


def _journal_observations(journal, cards, include_open):
    if (type(journal) is not dict or set(journal) != {"schema_version", "events"}
            or type(journal["schema_version"]) is not str or journal["schema_version"] not in _JOURNAL_FIELDS):
        raise ValueError("Preference training requires an event journal")
    events = journal["events"]
    if type(events) is not list or len(events) > 10_000:
        raise ValueError("Invalid preference event count")
    source_hash = _hash(journal)
    active, opened, rows, seen = {}, None, [], set()
    rounds, sealed = 0, False
    fields = _JOURNAL_FIELDS[journal["schema_version"]]
    attention = journal["schema_version"] != "norn.events.v1"

    def subset(value, nullable=False):
        if value is None and nullable:
            return None
        if (type(value) is not list or any(type(card) is not str or card not in opened["displayed"] for card in value)
                or len(set(value)) != len(value)):
            raise ValueError("Invalid preference visibility evidence")
        return value

    def snapshot(provisional, closing_id, visible=None):
        # Only cards that actually reached the screen can be compared. Without
        # visibility evidence (v1, or a browser without the observer), every
        # displayed card keeps its historical role.
        compared = opened["displayed"] if visible is None else [card for card in opened["displayed"] if card in visible]
        positives = [card for card in compared if card in active]
        negatives = [card for card in compared if card not in active]
        if not positives or not negatives:
            return  # No-choice/all-choice rounds do not supply relative labels.
        rows.append({"observation_id": source_hash + ":round:" + str(rounds), "source_hash": source_hash,
                     "round": rounds, "displayed": list(opened["displayed"]),
                     "selected": positives, "unselected": negatives,
                     "selection_event_ids": {card: active[card] for card in positives},
                     "display_event_id": opened["event_id"], "closing_event_id": closing_id,
                     "provisional": provisional})

    for sequence, event in enumerate(events, 1):
        if type(event) is not dict or sealed:
            raise ValueError("Invalid or sealed preference journal")
        kind = event.get("type")
        if kind not in fields or set(event) != {"event_id", "sequence", "type"} | fields[kind]:
            raise ValueError("Unexpected preference event fields")
        event_id = event["event_id"]
        if (type(event_id) is not str or not 1 <= len(event_id) <= 64
                or any(ord(char) < 33 or ord(char) > 126 for char in event_id)
                or event_id in seen or type(event["sequence"]) is not int or event["sequence"] != sequence):
            raise ValueError("Invalid preference event identity or sequence")
        seen.add(event_id)
        if kind == "round_started":
            displayed = event["card_ids"]
            if (opened is not None or type(event["round"]) is not int or event["round"] != rounds + 1
                    or event["round"] > 256 or type(displayed) is not list or not 1 <= len(displayed) <= 256
                    or any(type(card) is not str or card not in cards for card in displayed)
                    or len(set(displayed)) != len(displayed)):
                raise ValueError("Invalid displayed-card evidence")
            rounds += 1
            opened = {"displayed": list(displayed), "event_id": event_id, "ready": None, "last_ms": 0}
        elif kind == "session_completed":
            if opened is not None or not rounds:
                raise ValueError("Preference journal closed before its round")
            sealed = True
        else:
            if opened is None or type(event["round"]) is not int or event["round"] != rounds:
                raise ValueError("Preference event outside its displayed round")
            if kind == "round_ready":
                offset = event["ready_offset_ms"]
                if opened["ready"] is not None or type(offset) is not int or not 0 <= offset <= 86_400_000:
                    raise ValueError("Invalid preference round readiness")
                subset(event["unloaded_card_ids"])
                opened["ready"] = offset
            elif kind == "round_completed":
                duration = event["duration_ms"]
                if type(duration) is not int or not 0 <= duration <= 86_400_000:
                    raise ValueError("Invalid preference round duration")
                visible = None
                if attention:
                    window = duration - (opened["ready"] or 0)
                    hidden = event["hidden_ms"]
                    if window < opened["last_ms"] or type(hidden) is not int or not 0 <= hidden <= window:
                        raise ValueError("Invalid preference round timing")
                    visible = subset(event["seen_card_ids"], nullable=True)
                snapshot(False, event_id, None if visible is None else set(visible))
                opened = None
            else:
                card = event["card_id"]
                if type(card) is not str or card not in opened["displayed"]:
                    raise ValueError("Preference label lacks display evidence")
                if attention:
                    moment = event["t_ms"]
                    if (opened["ready"] is None or type(moment) is not int
                            or not opened["last_ms"] <= moment <= 86_400_000 - opened["ready"]):
                        raise ValueError("Invalid preference choice timing")
                    opened["last_ms"] = moment
                if kind == "card_selected":
                    if card in active or len(active) >= 256:
                        raise ValueError("Duplicate or excessive active preference choice")
                    active[card] = event_id
                else:
                    if card not in active:
                        raise ValueError("Cannot withdraw an unselected preference card")
                    del active[card]
    if not sealed and not include_open:
        raise ValueError("Preference training requires completed journals")
    if opened is not None and include_open:
        # An open round has no visibility summary yet; it stays provisional.
        snapshot(True, None)
    return source_hash, rows


def observations_from_journals(journals, catalog=None, include_open=False):
    """Admit completed rounds using the actual active state at each round end."""
    catalog = _catalog_snapshot(catalog)
    if type(journals) is not list or len(journals) > MAX_JOURNALS or type(include_open) is not bool:
        raise ValueError("Provide at most 64 journals")
    if sum(len(journal["events"]) for journal in journals if isinstance(journal, dict) and isinstance(journal.get("events"), list)) > MAX_TOTAL_EVENTS:
        raise ValueError("Preference source event budget exceeded")
    rows, hashes, duplicates = [], [], 0
    for journal in journals:
        source_hash, observations = _journal_observations(journal, catalog["cards"], include_open)
        if source_hash in hashes:
            duplicates += 1
            continue
        hashes.append(source_hash)
        rows.extend(observations)
    if len(rows) > MAX_OBSERVATIONS:
        raise ValueError("Preference observation budget exceeded")
    return {"observations": rows, "source_hashes": hashes, "duplicates_ignored": duplicates}


def _pairs(rows, config):
    per_round = min(config["max_pairs_per_round"], max(1, config["max_pairs"] // max(1, len(rows))))
    pairs, possible = [], 0
    for row in rows:
        positives, negatives = row["selected"], row["unselected"]
        total = len(positives) * len(negatives)
        possible += total
        # A deterministic modular permutation samples without allocating the
        # Cartesian product. Every pair has a true same-round source.
        # Sampling depends only on this round's revealed preference evidence.
        # A later click, a closing clock or a new source hash cannot change the
        # previously committed round's training pairs or leak holdout labels.
        sampling_basis = {"round": row["round"], "display_event_id": row["display_event_id"],
                          "displayed": sorted(row["displayed"]), "selected": sorted(positives),
                          "unselected": sorted(negatives)}
        seed = int(_hash(sampling_basis)[7:23], 16)
        step = seed % total or 1
        while math.gcd(step, total) != 1:
            step = (step + 1) % total or 1
        for index in range(min(total, per_round)):
            flat = (seed + index * step) % total
            pairs.append((positives[flat // len(negatives)], negatives[flat % len(negatives)], row["observation_id"]))
    return pairs[:config["max_pairs"]], possible


def _sigmoid(value):
    if value >= 0:
        return 1.0 / (1.0 + numeric.exp(-value))
    exp = numeric.exp(value)
    return exp / (1.0 + exp)


def _score(weights, vector):
    return math.fsum(weights[index] * value for index, value in vector)


def _fit(pairs, vectors, size, config):
    weights = [0.0] * size
    if not pairs:
        return weights
    involved = sorted({card for positive, negative, _ in pairs for card in (positive, negative)})
    for epoch in range(config["epochs"]):
        scores = {card: _score(weights, vectors[card]) for card in involved}
        card_gradient = {card: 0.0 for card in involved}
        for positive, negative, _ in pairs:
            error = _sigmoid(scores[positive] - scores[negative]) - 1.0
            card_gradient[positive] += error
            card_gradient[negative] -= error
        gradient = [0.0] * size
        for card, factor in card_gradient.items():
            for index, value in vectors[card]:
                gradient[index] += factor * value / len(pairs)
        rate = config["learning_rate"] / numeric.sqrt(1.0 + epoch / 10.0)
        weights = [weight - rate * (derivative + config["l2"] * weight)
                   for weight, derivative in zip(weights, gradient)]
    return weights


def _metrics(weights, pairs, vectors):
    if not pairs:
        return {"pairwise_accuracy": None, "log_loss": None}
    margins = [_score(weights, vectors[positive]) - _score(weights, vectors[negative]) for positive, negative, _ in pairs]
    return {"pairwise_accuracy": math.fsum(1.0 if value > 1e-12 else 0.5 if abs(value) <= 1e-12 else 0.0 for value in margins) / len(margins),
            "log_loss": math.fsum(max(-value, 0) + numeric.log1p(numeric.exp(-abs(value))) for value in margins) / len(margins)}


def _independent_group_count(rows):
    """Connected displayed-card rounds count as one correlated evidence group."""
    parents, owner = list(range(len(rows))), {}
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    for index, row in enumerate(rows):
        for card in row["displayed"]:
            if card in owner:
                parents[root(index)] = root(owner[card])
            else:
                owner[card] = index
    return len({root(index) for index in range(len(rows))})


def _validation(rows, config, vectors, size):
    baseline = {"pairwise_accuracy": 0.5, "log_loss": numeric.log(2.0)}
    result = {"status": "insufficient", "reason": "fewer_than_five_labeled_rounds", "method": "chronological_holdout_20_percent_purged_card_overlap",
              "pairwise_accuracy": None, "log_loss": None, "random_baseline": baseline,
              "train_observation_count": 0, "holdout_observation_count": 0,
              "train_pair_count": 0, "holdout_pair_count": 0, "purged_observation_count": 0,
              "card_overlap_count": 0, "train_independent_group_count": 0, "holdout_independent_group_count": 0,
              "train_observation_ids": [], "holdout_observation_ids": [], "beats_random": False,
              "claim": "Measures held-out relative choices only; no personality accuracy or population validation."}
    completed = [row for row in rows if not row["provisional"]]
    if len(completed) < 5:
        return result
    cutoff = len(completed) - max(1, math.ceil(len(completed) * 0.2))
    holdout = completed[cutoff:]
    holdout_cards = {card for row in holdout for card in row["displayed"]}
    # Purge earlier rounds sharing ANY displayed card, including weak negatives.
    # Repeated displays cannot masquerade as independent held-out evidence.
    train_rows = [row for row in completed[:cutoff] if not holdout_cards.intersection(row["displayed"])]
    result.update(train_observation_count=len(train_rows), holdout_observation_count=len(holdout),
                  purged_observation_count=cutoff - len(train_rows),
                  train_independent_group_count=_independent_group_count(train_rows),
                  holdout_independent_group_count=_independent_group_count(holdout),
                  train_observation_ids=[row["observation_id"] for row in train_rows],
                  holdout_observation_ids=[row["observation_id"] for row in holdout])
    if not train_rows:
        result["reason"] = "repeated_card_displays_leave_no_independent_training_rounds"
        return result
    train_pairs, _ = _pairs(train_rows, config)
    test_pairs, _ = _pairs(holdout, config)
    result.update(train_pair_count=len(train_pairs), holdout_pair_count=len(test_pairs))
    if not train_pairs or not test_pairs:
        result["reason"] = "no_independent_pair_labels"
        return result
    weights = _fit(train_pairs, vectors, size, config)
    metrics = _metrics(weights, test_pairs, vectors)
    enough = result["train_independent_group_count"] >= 3 and result["holdout_independent_group_count"] >= 2
    result.update(status="measured" if enough else "insufficient",
                  reason=None if enough else "fewer_than_three_independent_training_or_two_holdout_rounds",
                  beats_random=enough and metrics["pairwise_accuracy"] > 0.5 and metrics["log_loss"] < numeric.log(2.0), **metrics)
    return result


def train(journals, catalog=None, config=None, include_open=False, *, model_version=None):
    """Fit the full personal model; fit holdout metrics with separate weights."""
    if model_version is None:
        model_version = LEGACY_MODEL_VERSION if numeric.current_contract() == numeric.NATIVE_CONTRACT else MODEL_VERSION
    contract = _model_contract(model_version)
    with numeric.use(contract):
        return _train(journals, catalog, config, include_open, model_version)


def _model_contract(model_version):
    if model_version == MODEL_VERSION:
        return numeric.PORTABLE_CONTRACT
    if model_version == LEGACY_MODEL_VERSION:
        return numeric.NATIVE_CONTRACT
    raise ValueError("Unsupported preference checkpoint")


def _checkpoint_contract(function):
    @wraps(function)
    def wrapped(checkpoint, *args, **kwargs):
        if type(checkpoint) is not dict:
            raise ValueError("Unsupported preference checkpoint")
        with numeric.use(_model_contract(checkpoint.get("model_version"))):
            return function(checkpoint, *args, **kwargs)
    return wrapped


def _train(journals, catalog, config, include_open, model_version):
    catalog, config = _catalog_snapshot(catalog), _config(config)
    admitted = observations_from_journals(journals, catalog, include_open)
    rows = admitted["observations"]
    vectors, transform = _vectors(catalog)
    pairs, possible = _pairs(rows, config)
    weights = _fit(pairs, vectors, len(catalog["feature_names"]), config)
    basis = {"source_hashes": admitted["source_hashes"], "observations": rows,
             "feature_version": catalog["feature_version"], "config": config, "include_open": include_open}
    checkpoint = {"schema_version": CHECKPOINT_SCHEMA_VERSION, "model_version": model_version,
        **({"numerical_contract": numeric.PORTABLE_CONTRACT} if model_version == MODEL_VERSION else {}),
        "feature_version": catalog["feature_version"], "feature_names": list(catalog["feature_names"]),
        "feature_count": len(catalog["feature_names"]), "weights": weights, "feature_transform": transform,
        "source_hashes": admitted["source_hashes"], "training_hash": _hash(basis), "config": config,
        "training": {"observation_count": len(rows), "pair_count": len(pairs), "possible_pair_count": possible,
                     "omitted_pair_count": possible - len(pairs), "source_count": len(admitted["source_hashes"]),
                     "duplicates_ignored": admitted["duplicates_ignored"], "include_open": include_open,
                     "provisional": any(row["provisional"] for row in rows)},
        "validation": _validation(rows, config, vectors, len(catalog["feature_names"])),
        "semantics": "L2-regularized pairwise logistic ranking; same-round unselected cards are weak relative negatives, not dislikes or personality labels."}
    checkpoint["checkpoint_hash"] = _hash(checkpoint)
    return deepcopy(checkpoint)


@_checkpoint_contract
def verify_checkpoint(checkpoint, catalog=None, journals=None):
    """Check finite parameters, feature identity and optional exact source replay.

    Hash consistency is not authenticity. With source journals supplied, the
    entire deterministic fit is recomputed and compared, including validation.
    """
    catalog = _catalog_snapshot(catalog)
    if (type(checkpoint) is not dict or checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
            or checkpoint.get("model_version") not in (MODEL_VERSION, LEGACY_MODEL_VERSION)):
        raise ValueError("Unsupported preference checkpoint")
    if checkpoint["model_version"] == MODEL_VERSION and checkpoint.get("numerical_contract") != numeric.PORTABLE_CONTRACT:
        raise ValueError("Unsupported preference numerical contract")
    expected = _hash({key: value for key, value in checkpoint.items() if key != "checkpoint_hash"})
    if checkpoint.get("checkpoint_hash") != expected:
        raise ValueError("Preference checkpoint hash mismatch")
    if (checkpoint.get("feature_version") != catalog["feature_version"]
            or checkpoint.get("feature_names") != catalog["feature_names"]
            or checkpoint.get("feature_count") != len(catalog["feature_names"])):
        raise ValueError("Preference checkpoint feature version differs from this runtime")
    weights = checkpoint.get("weights")
    if type(weights) is not list or len(weights) != len(catalog["feature_names"]) or any(not _finite(value) for value in weights):
        raise ValueError("Invalid preference weights")
    _config(checkpoint.get("config"))
    if checkpoint.get("feature_transform") != _vectors(catalog)[1]:
        raise ValueError("Preference normalization differs from this catalog")
    if journals is not None:
        rebuilt = train(journals, catalog, checkpoint["config"], checkpoint["training"]["include_open"],
                        model_version=checkpoint["model_version"])
        if _canonical(rebuilt) != _canonical(checkpoint):
            raise ValueError("Preference training sources or fitted parameters differ")
    return True


@_checkpoint_contract
def predict_scores(checkpoint, card_ids=None, catalog=None):
    """Return lightweight authoritative logits for a map or a ranking preview."""
    catalog = _catalog_snapshot(catalog)
    verify_checkpoint(checkpoint, catalog)
    vectors, _ = _vectors(catalog)
    cards = sorted(vectors) if card_ids is None else list(card_ids)
    if len(cards) != len(set(cards)) or any(card not in vectors for card in cards):
        raise ValueError("Unknown or duplicate card for preference ranking")
    scored = [{"card_id": card, "score": _score(checkpoint["weights"], vectors[card])} for card in cards]
    return sorted(scored, key=lambda item: (-item["score"], item["card_id"]))


@_checkpoint_contract
def rank(checkpoint, card_ids=None, top_k=None, catalog=None):
    catalog = _catalog_snapshot(catalog)
    scored = predict_scores(checkpoint, card_ids=card_ids, catalog=catalog)
    vectors, _ = _vectors(catalog)
    if top_k is not None and (type(top_k) is not int or top_k < 0):
        raise ValueError("top_k must be a nonnegative integer")
    if top_k is not None:
        scored = scored[:top_k]
    predictions = []
    for item in scored:
        card, score = item["card_id"], item["score"]
        contributions = [{"name": catalog["feature_names"][index], "value": value,
                          "label": feature_label(catalog["feature_names"][index]),
                          "weight": checkpoint["weights"][index], "contribution": value * checkpoint["weights"][index]}
                         for index, value in vectors[card]]
        predictions.append({"card_id": card, "score": score, "contributions": contributions,
                            "reconstructed_score": math.fsum(item["contribution"] for item in contributions),
                            "score_kind": "relative_preference_logit"})
    return predictions


@_checkpoint_contract
def summarize(checkpoint, catalog=None, top_k=8):
    catalog = _catalog_snapshot(catalog)
    verify_checkpoint(checkpoint, catalog)
    training = checkpoint["training"]
    trained = training["pair_count"] > 0
    status = "trained" if trained else "no_observations"
    features = [{"name": name, "label": feature_label(name), "weight": weight}
                for name, weight in zip(checkpoint["feature_names"], checkpoint["weights"]) if abs(weight) > 1e-12]
    features.sort(key=lambda item: (-abs(item["weight"]), item["name"]))
    return {"status": status, "trained": trained, **deepcopy(training),
            "feature_count": checkpoint["feature_count"], "top_features": features[:10] if trained else [],
            "validation": deepcopy(checkpoint["validation"]),
            "recommendations": rank(checkpoint, top_k=top_k, catalog=catalog) if trained else [],
            "model_version": checkpoint["model_version"], "feature_version": checkpoint["feature_version"],
            **({"numerical_contract": checkpoint["numerical_contract"]} if "numerical_contract" in checkpoint else {}),
            "training_hash": checkpoint["training_hash"], "checkpoint_hash": checkpoint["checkpoint_hash"],
            "config": deepcopy(checkpoint["config"]), "score_semantics": "Relative preference ranking score; not personality probability."}
