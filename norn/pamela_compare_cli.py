"""Explicit, frozen-prefix PAMELA comparisons and saved-report validation.

The public experience never calls this remote lane. Local evaluation uses only
the event facts, not a saved profile, and keeps the training prefix frozen for
all held-out rounds. A supplied Jev client makes at most one bounded batch.
"""

import base64
from copy import deepcopy
import json
import math

from .core import engine_module, web_root
from .events import canonical_json, content_hash
from .pamela_comparison import (SCHEMA_VERSION, SCOPE, PROBABILITY_FLOOR, add_cumulative,
                                options_hash, prediction, round_metadata)

STOP = "__STOP__"
PROBABILITY_TOLERANCE = 1e-6


def _prepare(journal, deck, train_rounds=None, max_decisions=128):
    from .research_inspection import validated
    replay = validated(journal, deck)
    journal = replay["journal"]
    rows = engine_module("research_deck").observations(journal)
    if len(rows) < 5:
        raise ValueError("PAMELA comparison needs at least five completed rounds")
    cutoff = len(rows) - max(1, math.ceil(len(rows) * 0.2)) if train_rounds is None else train_rounds
    if type(cutoff) is not int or not 1 <= cutoff < len(rows):
        raise ValueError("train_rounds must leave at least one training and one test round")
    if type(max_decisions) is not int or not 1 <= max_decisions <= 512:
        raise ValueError("max_decisions must be between 1 and 512")
    if len(rows) - cutoff > max_decisions:
        raise ValueError("Held-out rounds exceed max_decisions")
    closes = [i for i, event in enumerate(journal["events"]) if event["type"] == "round_completed"]
    prefix = {"schema_version": journal["schema_version"],
              "events": deepcopy(journal["events"][:closes[cutoff - 1] + 1])}
    return replay, rows, prefix, cutoff


def build_pamela_request(prefix, rows, cases, deck, *, input_mode="numeric"):
    """Only training choices and target-independent held-out option sets leave.

    The existing decision-adapter wire protocol includes STOP. Its result is
    mathematically conditioned on choosing a card before local scoring; this
    report neither evaluates nor invents STOP probabilities for local lanes.
    """
    if input_mode not in ("numeric", "semantic"):
        raise ValueError("Decision input mode must be numeric or semantic")
    prefix_hash = content_hash(prefix)

    def alias(card):
        return "item_" + content_hash({"prefix": prefix_hash, "card": card}).split(":")[1][:16]

    def describe(card):
        value = {"card_features": dict(zip(["reference_utility"] + [f"content_{i}" for i in range(1, 9)],
                                           deck.x2[card]))}
        if input_mode == "semantic":
            value.update(scene=deck.cards[card]["scene"], motifs=list(deck.cards[card]["motifs"]))
        return value

    def option_descriptions(cards):
        # Screen order is not a local v6 feature. Sort opaque aliases rather
        # than accidentally handing only Jev a placement cue in dictionary order.
        return {alias(card): describe(card) for card in sorted(cards, key=alias)}

    state = {"schema_version": "norn.pamela-comparison.input.v1", "feature_version": deck.version,
             "feature_names": ["reference_utility"] + [f"content_{i}" for i in range(1, 9)],
             "past_completed_rounds": [
                 {"options": option_descriptions(row["shown"]),
                  "kept_in_order": [alias(card) for card in row["picks"]], "then_stopped": True}
                 for row in rows],
             "scope": "Infer picture preferences only from these past rounds. This state is frozen. "
                      "No held-out choices, reading, timing, or saved personal profile is supplied. "
                      "The report evaluates the first finally-kept card conditional on any card being kept; "
                      "STOP is included only for the adapter's existing wire protocol. "
                      "Options are sorted by opaque identifier; their order does not expose screen placement."}
    questions, mappings = {}, {}
    for case in cases:
        if len(case["candidate_ids"]) < 2:
            continue
        question_id = f"round_{case['round']:04d}"
        mapping = {alias(card): card for card in case["candidate_ids"]}
        mapping[STOP] = STOP
        questions[question_id] = {
            "type": "choice",
            "instructions": "Given only the frozen past, which visible picture will the visitor keep first, "
                            "or will they keep none? Return probabilities for every picture and STOP. "
                            "The evaluator will condition on choosing a picture; later actual choices are hidden. "
                            "Identifiers are arbitrary and carry no preference information.",
            "criteria": {**{key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                             for key, value in option_descriptions(case["candidate_ids"]).items()},
                         STOP: "Keep no picture in this round."}}
        mappings[question_id] = mapping
    return state, questions, mappings


def _probability_prediction(probabilities, candidates, target, scored):
    """Remote scores are conditional probabilities, not claimed latent utilities."""
    values = list(probabilities)
    highest = max(values) if values else None
    ties = values.count(highest) if values else 0
    eligible = scored and target in candidates and len(candidates) >= 2
    index = candidates.index(target) if eligible else None
    return {"status": "ok", "score_kind": "conditional_probability", "scores": values,
            "probabilities": values, "ranks": [1 + sum(other > value for other in values) for value in values],
            "order": [candidates[i] for i in sorted(range(len(candidates)), key=lambda i: (-values[i], candidates[i]))],
            "target_nll": -math.log(max(values[index], PROBABILITY_FLOOR)) if eligible else None,
            "top1_credit": (1 / ties if values[index] == highest else 0.0) if eligible else None,
            "zero_probability_target": values[index] == 0 if eligible else False}


def _cumulative(cases):
    summary = add_cumulative(cases)
    total, count = 0.0, 0
    for case in cases:
        lane = case["lanes"]["jev"]
        if case["scored"] and lane["status"] == "ok":
            count += 1
            total += lane["target_nll"]
        case["cumulative"].update(jev_n=count, jev_nll=total / count if count else None)
    return {**summary, "jev_n": count, "jev_nll": total / count if count else None}


def compare_pamela(journal, *, train_rounds=None, decision_client=None, input_mode="numeric",
                   max_decisions=128, provenance="user_journal", include_images=False):
    """Freeze one prefix; evaluate all remaining rounds without updating it."""
    if provenance not in ("fixture", "user_journal"):
        raise ValueError("Unknown comparison provenance")
    research, numeric = engine_module("research_deck"), engine_module("numeric")
    deck = research.load_deck(research.deck_of(journal))
    replay, rows, prefix, cutoff = _prepare(journal, deck, train_rounds, max_decisions)
    if input_mode not in ("numeric", "semantic"):
        raise ValueError("Decision input mode must be numeric or semantic")
    if decision_client is not None and getattr(decision_client, "provider", "typesafe") != "typesafe":
        raise ValueError("PAMELA's third lane is Typesafe Jev; select typesafe or keep this comparison offline")
    with numeric.use(numeric.NATIVE_CONTRACT):
        state = research.session(rows[:cutoff], deck)
        weights, layer = state.effective(), state.layer()
    prefix_hash = content_hash(prefix)
    metadata = round_metadata(replay["journal"])
    cases = []
    for row in rows[cutoff:]:
        candidates, picks = list(row["shown"]), list(row["picks"])
        target = picks[0] if picks else None
        scored = target is not None and len(candidates) >= 2
        display = {key: metadata[row["round"]][key] for key in (
            "planned_count", "ready_loaded_count", "ever_seen_count", "ready_columns", "eligibility_source")}
        display["eligible_count"] = len(candidates)
        baseline = prediction([1.0] + [0.0] * 8, candidates, deck, target, scored=scored)
        learned = prediction(weights, candidates, deck, target, scored=scored)
        cases.append({"round": row["round"], "cutoff_rounds": cutoff, "prefix_hash": prefix_hash,
                      "options_hash": options_hash(candidates, deck), "candidate_ids": candidates,
                      "picks": picks, "target": target, "scored": scored,
                      "skipped_reason": "no_pick" if target is None else
                                        "no_alternative" if not scored else None,
                      "layer_before": layer, "same_predictions": baseline["probabilities"] == learned["probabilities"],
                      "display": display, "lanes": {"baseline": baseline, "learning": learned,
                                                     "jev": {"status": "not_run"}}})
    report = {"schema_version": SCHEMA_VERSION, "mode": "frozen_prefix", "scope": SCOPE,
              "events_hash": replay["events_hash"], "deck": deck.label, "feature_version": deck.version,
              "numerical_contract": numeric.NATIVE_CONTRACT,
              "training": {"cutoff": cutoff, "rounds": cutoff, "total_rounds": len(rows),
                           "prefix_hash": prefix_hash, "state_frozen": True, "effective_weights": list(weights)},
              "provenance": {"kind": provenance, "saved_profile_used": False, "saved_profile_verified": False,
                             "note": "Event facts are validated; any stored profile is deliberately unused, not verified. "
                                     "A saved result's hash does not authenticate a remote provider call."},
              "lanes": {"baseline": {"status": "ok", "algorithm": "population_utility"},
                        "learning": {"status": "ok", "algorithm": research.ALGORITHM_VERSION, "layer": layer},
                        "jev": {"status": "not_run", "reason": "offline_opt_in_only", "provider_calls": 0,
                                "input_mode": input_mode, "provider": "typesafe"}},
              "rounds": cases, "provider_calls": 0, "probability_floor": PROBABILITY_FLOOR,
              "attribution": deepcopy(deck.document["source"]),
              "semantics": "One training prefix is frozen for all held-out rounds; this is not the rolling "
                           "online comparison. Probabilities condition on choosing a card, exclude STOP, and score "
                           "the first finally-kept card. Empty choices and fewer than two eligible cards are "
                           "unscored. Eligibility may use whole-round cumulative visibility. These are descriptive "
                           "single-journal results, not a human-accuracy or display-policy claim. Semantic Jev "
                           "input additionally supplies scene/motif descriptions; numeric input shares nine features."}
    if decision_client is not None:
        from .fast_thinkers import validate_probabilities
        from .jev import JevError
        state_input, questions, mappings = build_pamela_request(prefix, rows[:cutoff], cases, deck,
                                                               input_mode=input_mode)
        remote = report["lanes"]["jev"]
        remote.update(requested_model=decision_client.model,
                      request_hash=content_hash({"state": state_input, "questions": questions}),
                      blind_card_identifiers=True, wire_stop_conditioned_out=True)
        before = decision_client.calls
        if questions and any(case["scored"] for case in cases):
            try:
                response = decision_client.decide(state_input, questions)
                if response.get("model") != decision_client.model or set(response["answers"]) != set(questions):
                    raise ValueError("Remote answer set differs from questions")
                predictions = {}
                for case in cases:
                    question_id = f"round_{case['round']:04d}"
                    if question_id not in mappings:
                        continue
                    answer = response["answers"][question_id]
                    raw = {mappings[question_id][key]: value for key, value in answer["probabilities"].items()}
                    raw = validate_probabilities(raw, case["candidate_ids"])
                    mass = math.fsum(raw[card] for card in case["candidate_ids"])
                    if mass <= 0:
                        raise ValueError("Remote assigned no probability to any card")
                    probabilities = [raw[card] / mass for card in case["candidate_ids"]]
                    predictions[case["round"]] = _probability_prediction(
                        probabilities, case["candidate_ids"], case["target"], case["scored"])
                for case in cases:
                    case["lanes"]["jev"] = predictions.get(case["round"], {"status": "not_run"})
                remote.update(status="ok", model=response["model"], usage=response.get("usage", {}),
                              batch_elapsed_ms=response.get("elapsed_ms"),
                              provider_calls=decision_client.calls - before)
                remote.pop("reason", None)
            except (JevError, ValueError, KeyError, TypeError):
                remote.update(status="failed", reason="invalid_or_unavailable_decision_result",
                              provider_calls=decision_client.calls - before)
                for case in cases:
                    case["lanes"]["jev"] = {"status": "failed"}
        else:
            remote["reason"] = "no_scorable_test_rounds"
        report["provider_calls"] = remote["provider_calls"]
    report["summary"] = _cumulative(cases)
    if include_images:
        root = web_root() / "assets" / "decks" / deck.name
        report["cards"] = {card: {"thumbnail": "data:image/webp;base64," + base64.b64encode(
            (root / f"{card}.webp").read_bytes()).decode("ascii")}
            for card in sorted({card for case in cases for card in case["candidate_ids"]})}
    report["report_hash"] = content_hash(report)
    return report


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _equal_float(actual, expected):
    return actual is None if expected is None else _finite_number(actual) and math.isclose(
        actual, expected, rel_tol=1e-9, abs_tol=1e-9)


def validate_saved_report(report, journal, deck):
    """Validate a detached display report, without trusting its provider provenance.

    Event, representation, cutoff and candidate identities are strict hashes.
    Probabilities and their derived metrics must be internally consistent.
    This is not a sealed-profile verifier and never relaxes profile replay.
    The self-authored report hash cannot prove that Jev was actually called.
    """
    if type(report) is str:
        if len(report.encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError("Offline comparison exceeds the 8 MiB limit")

        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("Duplicate field in offline comparison")
                value[key] = item
            return value

        def constant(value):
            raise ValueError("Nonfinite number in offline comparison")

        # Browser import forwards the original text. JSON.parse/stringify
        # would erase the distinction between Python's 1.0 and 1 and destroy
        # a valid canonical content hash even before this verifier sees it.
        try:
            report = json.loads(report, object_pairs_hook=unique, parse_constant=constant)
        except (UnicodeError, RecursionError) as exc:
            raise ValueError("Invalid offline comparison JSON") from exc
    if type(report) is not dict:
        raise ValueError("Offline comparison must be an object")
    canonical_json(report)
    required = {"schema_version", "mode", "scope", "events_hash", "deck", "feature_version", "numerical_contract",
                "training", "provenance", "lanes", "rounds", "provider_calls", "probability_floor", "attribution",
                "semantics", "summary", "report_hash"}
    if not required <= set(report) or set(report) - required - {"cards"}:
        raise ValueError("Offline comparison has missing or unknown fields")
    if report.get("schema_version") != SCHEMA_VERSION or report.get("mode") != "frozen_prefix" or report.get("scope") != SCOPE:
        raise ValueError("Unsupported offline comparison contract")
    if report.get("report_hash") != content_hash({key: value for key, value in report.items() if key != "report_hash"}):
        raise ValueError("Offline comparison hash mismatch")
    training = report.get("training")
    if (type(training) is not dict or set(training) != {"cutoff", "rounds", "total_rounds", "prefix_hash",
                                                      "state_frozen", "effective_weights"}
            or training.get("state_frozen") is not True
            or type(training.get("effective_weights")) is not list or len(training["effective_weights"]) != 9
            or not all(_finite_number(value) for value in training["effective_weights"])):
        raise ValueError("Offline comparison must name a frozen training prefix")
    replay, rows, prefix, cutoff = _prepare(journal, deck, training.get("cutoff"), 512)
    prefix_hash = content_hash(prefix)
    if (report.get("events_hash") != replay["events_hash"] or report.get("deck") != deck.label
            or report.get("feature_version") != deck.version or training.get("prefix_hash") != prefix_hash
            or training.get("rounds") != cutoff or training.get("total_rounds") != len(rows)):
        raise ValueError("Offline comparison belongs to another record, deck, or prefix")
    if (type(report.get("lanes")) is not dict or set(report["lanes"]) != {"baseline", "learning", "jev"}
            or any(type(value) is not dict for value in report["lanes"].values())):
        raise ValueError("Offline comparison must contain exactly three named lanes")
    if any(report["lanes"][name].get("status") != "ok" for name in ("baseline", "learning")):
        raise ValueError("Offline local lanes must be available")
    if set(report["lanes"]["baseline"]) != {"status", "algorithm"} or set(report["lanes"]["learning"]) != {"status", "algorithm", "layer"}:
        raise ValueError("Offline local model metadata has unknown fields")
    layer = report["lanes"]["learning"]["layer"]
    if (report["lanes"]["baseline"]["algorithm"] != "population_utility"
            or report["lanes"]["learning"]["algorithm"] != engine_module("research_deck").ALGORITHM_VERSION
            or layer not in ("population", "sensitivity", "personal")
            or layer == "population" and training["effective_weights"] != [1.0] + [0.0] * 8
            or layer == "sensitivity" and any(training["effective_weights"][1:])):
        raise ValueError("Offline local model or effective layer is invalid")
    remote_fields = {"status", "reason", "provider_calls", "input_mode", "provider", "requested_model", "request_hash",
                     "blind_card_identifiers", "wire_stop_conditioned_out", "model", "usage", "batch_elapsed_ms"}
    if set(report["lanes"]["jev"]) - remote_fields:
        raise ValueError("Offline Jev metadata has unknown fields")
    if report["lanes"]["jev"].get("status") not in ("not_run", "failed", "ok"):
        raise ValueError("Invalid saved Jev status")
    if (type(report.get("provider_calls")) is not int or not 0 <= report["provider_calls"] <= 1
            or report["provider_calls"] != report["lanes"]["jev"].get("provider_calls")
            or report.get("probability_floor") != PROBABILITY_FLOOR
            or report.get("numerical_contract") != engine_module("numeric").NATIVE_CONTRACT
            or report["lanes"]["jev"].get("input_mode") not in ("numeric", "semantic")
            or report["lanes"]["jev"].get("provider") != "typesafe"):
        raise ValueError("Offline model contract or call count is invalid")
    provenance = report.get("provenance")
    if type(provenance) is not dict or set(provenance) != {"kind", "saved_profile_used", "saved_profile_verified", "note"}:
        raise ValueError("Offline provenance has unknown fields")
    if provenance["saved_profile_used"] is not False or provenance["saved_profile_verified"] is not False:
        raise ValueError("A comparison cannot claim verification or use of a stored profile")
    if provenance["kind"] not in ("fixture", "user_journal") or type(provenance["note"]) is not str:
        raise ValueError("Offline provenance kind is invalid")
    if report.get("attribution") != deck.document["source"] or type(report.get("semantics")) is not str:
        raise ValueError("Offline deck attribution or semantics is invalid")
    if "cards" in report:
        if type(report["cards"]) is not dict:
            raise ValueError("Offline card thumbnails must be an object")
        for card, value in report["cards"].items():
            if (card not in deck.cards or type(value) is not dict or set(value) != {"thumbnail"}
                    or type(value["thumbnail"]) is not str or not value["thumbnail"].startswith("data:image/webp;base64,")):
                raise ValueError("Offline thumbnails must be known local WebP data")
    cases = report.get("rounds")
    if type(cases) is not list or len(cases) != len(rows) - cutoff:
        raise ValueError("Offline comparison held-out rounds differ")
    metadata = round_metadata(replay["journal"])
    for case, row in zip(cases, rows[cutoff:]):
        candidates, picks = list(row["shown"]), list(row["picks"])
        target = picks[0] if picks else None
        scored = target is not None and len(candidates) >= 2
        skipped = "no_pick" if target is None else "no_alternative" if not scored else None
        display = {key: metadata[row["round"]][key] for key in (
            "planned_count", "ready_loaded_count", "ever_seen_count", "ready_columns", "eligibility_source")}
        display["eligible_count"] = len(candidates)
        case_fields = {"round", "cutoff_rounds", "prefix_hash", "options_hash", "candidate_ids", "picks", "target",
                       "scored", "skipped_reason", "layer_before", "same_predictions", "display", "lanes", "cumulative"}
        if (type(case) is not dict or set(case) != case_fields or case.get("round") != row["round"] or case.get("cutoff_rounds") != cutoff
                or case.get("prefix_hash") != prefix_hash or case.get("options_hash") != options_hash(candidates, deck)
                or case.get("candidate_ids") != candidates or case.get("picks") != picks
                or case.get("target") != target or case.get("scored") is not scored
                or case.get("skipped_reason") != skipped or case.get("display") != display
                or case.get("layer_before") != layer
                or type(case.get("lanes")) is not dict or set(case["lanes"]) != {"baseline", "learning", "jev"}):
            raise ValueError("Offline comparison round, candidates, or target differs from the record")
        for name, lane in case["lanes"].items():
            if type(lane) is not dict:
                raise ValueError("Invalid saved prediction lane")
            if name == "jev" and lane.get("status") in ("not_run", "failed"):
                if set(lane) != {"status"}:
                    raise ValueError("An unrun Jev lane cannot contain substituted predictions")
                remote_status = report["lanes"]["jev"]["status"]
                if remote_status != lane["status"] and not (remote_status == "ok" and len(candidates) < 2 and lane["status"] == "not_run"):
                    raise ValueError("Saved Jev availability is inconsistent")
                continue
            if lane.get("status") != "ok":
                raise ValueError("Local saved lanes must contain predictions")
            if name == "jev" and report["lanes"]["jev"]["status"] != "ok":
                raise ValueError("Unrun Jev cannot claim measured predictions")
            lane_fields = {"status", "scores", "probabilities", "ranks", "order", "target_nll", "top1_credit", "zero_probability_target"}
            if name == "jev":
                lane_fields |= {"score_kind"}
            if set(lane) != lane_fields:
                raise ValueError("Saved prediction has missing or unknown fields")
            scores, probabilities = lane.get("scores"), lane.get("probabilities")
            if (type(scores) is not list or type(probabilities) is not list or len(scores) != len(candidates)
                    or len(probabilities) != len(candidates) or not all(_finite_number(v) for v in scores)
                    or any(not _finite_number(p) or not 0 <= p <= 1 for p in probabilities)
                    or probabilities and abs(math.fsum(probabilities) - 1.0) > PROBABILITY_TOLERANCE):
                raise ValueError("Saved probabilities must be finite, aligned, and normalized")
            ranks = [1 + sum(other > score for other in scores) for score in scores]
            order = [candidates[i] for i in sorted(range(len(candidates)), key=lambda i: (-scores[i], candidates[i]))]
            if lane.get("ranks") != ranks or lane.get("order") != order:
                raise ValueError("Saved prediction ranking is inconsistent")
            if probabilities:
                if name == "jev":
                    if lane.get("score_kind") != "conditional_probability" or scores != probabilities:
                        raise ValueError("Saved Jev scores must identify their conditional probability meaning")
                else:
                    masses = [math.exp(score - max(scores)) for score in scores]
                    expected = [mass / sum(masses) for mass in masses]
                    if any(abs(p - q) > PROBABILITY_TOLERANCE for p, q in zip(probabilities, expected)):
                        raise ValueError("Saved local probabilities differ from their utilities")
            if name in ("baseline", "learning"):
                weights = [1.0] + [0.0] * 8 if name == "baseline" else training["effective_weights"]
                expected = prediction(weights, candidates, deck, target, scored=scored)
                if any(not _equal_float(actual, wanted) for actual, wanted in zip(scores, expected["scores"])):
                    raise ValueError("Saved local utilities differ from their declared effective weights")
            index = candidates.index(target) if scored else None
            nll = -math.log(max(probabilities[index], PROBABILITY_FLOOR)) if scored else None
            credit = (1 / scores.count(max(scores)) if scores[index] == max(scores) else 0.0) if scored else None
            if (not _equal_float(lane.get("target_nll"), nll) or not _equal_float(lane.get("top1_credit"), credit)
                    or lane.get("zero_probability_target") is not (probabilities[index] == 0 if scored else False)):
                raise ValueError("Saved metrics do not match the probabilities and target")
        same = case["lanes"]["baseline"]["probabilities"] == case["lanes"]["learning"]["probabilities"]
        if case.get("same_predictions") is not same:
            raise ValueError("Saved equality marker is inconsistent")
    checked = deepcopy(report)
    expected_summary = _cumulative(checked["rounds"])
    if type(report.get("summary")) is not dict or set(report["summary"]) != set(expected_summary) or any(
            not _equal_float(report["summary"][key], value) for key, value in expected_summary.items()):
        raise ValueError("Saved cumulative summary is inconsistent")
    for original, rebuilt in zip(cases, checked["rounds"]):
        if type(original.get("cumulative")) is not dict or set(original["cumulative"]) != set(rebuilt["cumulative"]) or any(
                not _equal_float(original["cumulative"][key], value) for key, value in rebuilt["cumulative"].items()):
            raise ValueError("Saved cumulative curve is inconsistent")
    return deepcopy(report)


def render_pamela_comparison(report, *, language="zh"):
    """Standalone bilingual viewer of the same saved, frozen-prefix artifact."""
    if report.get("schema_version") != SCHEMA_VERSION or report.get("mode") != "frozen_prefix":
        raise ValueError("PAMELA viewer requires a frozen-prefix comparison")
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for source, target in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                           ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        payload = payload.replace(source, target)
    return _VIEWER.replace("__INITIAL_LANGUAGE__", "en" if str(language).lower().startswith("en") else "zh").replace("__REPORT_DATA__", payload)


_VIEWER = r'''<!doctype html><html lang="__INITIAL_LANGUAGE__"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Norn · PAMELA prediction comparison</title><style>
:root{color-scheme:dark;--bg:#101119;--fg:#e6e3db;--muted:#a3a3ad;--line:#34343d;--baseline:#aab1bc;--learning:#d4b45c;--jev:#b49be3}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}main{max-width:1100px;margin:auto;padding:24px}header{display:flex;align-items:center;justify-content:space-between;gap:16px}button{font:inherit;color:var(--fg);background:transparent;border:1px solid var(--line);border-radius:6px;padding:8px 14px;cursor:pointer}button:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--learning);outline-offset:4px}h1{font-size:28px;line-height:1.25;margin:24px 0 12px}h2{font-size:17px;margin:0 0 8px}p{margin:8px 0}.muted{color:var(--muted)}.context{padding:12px 0;border-block:1px solid var(--line);margin:20px 0}.slider{display:flex;align-items:center;gap:16px;margin:18px 0}.slider input{flex:1;min-width:0;accent-color:var(--learning)}.slider output{white-space:nowrap;font-variant-numeric:tabular-nums}.pictures{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:10px;margin:12px 0 24px}.picture{min-width:0;position:relative}.picture img{display:block;width:100%;height:140px;object-fit:cover;background:var(--line);border-radius:6px}.picture.picked img{outline:2px solid var(--learning);outline-offset:2px}.picture small{display:block;font-size:11px;color:var(--muted);overflow-wrap:anywhere;margin-top:5px}.picture b{position:absolute;top:5px;right:5px;background:#101119d9;padding:0 6px;border-radius:4px;font-size:12px}.lanes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;margin:20px 0}.lane{min-width:0;border-top:2px solid var(--line);padding-top:12px}.lane[data-lane=baseline]{border-color:var(--baseline)}.lane[data-lane=learning]{border-color:var(--learning)}.lane[data-lane=jev]{border-color:var(--jev)}.metric{font-size:24px;line-height:1.2;font-variant-numeric:tabular-nums}.barrow{display:grid;grid-template-columns:minmax(64px,90px) 1fr auto;gap:8px;align-items:center;margin:8px 0;font-size:12px}.barrow .track{background:#ffffff0a;height:7px}.fill{height:100%;background:var(--baseline)}[data-lane=learning] .fill{background:var(--learning)}[data-lane=jev] .fill{background:var(--jev)}.barrow.picked{font-weight:600}.curve{width:100%;height:180px;display:block}.legend{display:flex;flex-wrap:wrap;gap:20px;font-size:12px}.legend span:before{content:"";display:inline-block;width:15px;height:3px;margin-right:7px;vertical-align:middle;background:var(--baseline)}.legend .learning:before{background:var(--learning)}.legend .jev:before{background:var(--jev)}details{margin-top:22px;border-top:1px solid var(--line);padding-top:12px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.scope{font-size:13px;color:var(--muted)}a{color:var(--fg)}@media(max-width:700px){main{padding:18px}.lanes{grid-template-columns:1fr;gap:18px}.pictures{grid-template-columns:repeat(3,minmax(0,1fr))}.picture img{height:110px}.metric{font-size:22px}h1{font-size:24px}}
</style></head><body><main><header><b>NORN / PAM∃LA</b><button id="language" type="button"></button></header>
<h1 id="title"></h1><p id="intro" class="muted"></p><p id="context" class="context"></p>
<section><div class="slider"><input id="step" type="range" min="0" max="0" value="0"><output id="counter"></output></div><h2 id="round"></h2><p id="target" class="muted"></p><div id="pictures" class="pictures"></div></section>
<section id="lanes" class="lanes"></section><section><h2 id="curve-title"></h2><div id="legend" class="legend"></div><svg id="curve" class="curve" role="img"></svg><p id="scope" class="scope"></p></section>
<details><summary id="technical"></summary><p id="provenance" class="scope"></p><pre id="json"></pre></details>
<p class="scope"><a href="https://huggingface.co/datasets/bethgelab/PAMELA">PAM∃LA · CC BY 4.0</a></p></main>
<script id="report" type="application/json">__REPORT_DATA__</script><script>
(()=>{'use strict';const report=JSON.parse(document.getElementById('report').textContent);let lang='__INITIAL_LANGUAGE__';const $=id=>document.getElementById(id),names=['baseline','learning','jev'];
const words={zh:{title:'同一段经历，三路预测',intro:'先冻结前几轮的学习状态，再回看后面的真实选择。Jev 仅在显式运行后才有结果。',frozen:n=>`训练冻结在第 ${n} 轮 · 后续选择不更新模型`,round:n=>`留出第 ${n} 轮`,target:id=>`本轮第一个最终保留的画面：${id}`,none:'本轮没有可评分的图片选择；不把它计成零损失。',picked:'已选',baseline:'参考基线',learning:'当前学习模型',jev:'Jev · 离线',nll:'累计条件预测损失 · 越低越好',perRound:'本轮条件预测损失',notRun:'本次未计算；没有补入替代分数。',failed:'运行失败；没有补入替代分数。',curve:'随着留出轮次积累',scope:'概率以“如果选一张”为条件；不包括“都不选”。金框表示实际保留。空选择及没有比较对象的轮次不评分。这个回看比较可能使用整轮累计可见集合，不能当成实时推荐效果。',technical:'技术细节与原始 JSON',provenance:'此报告只使用已校验的事件事实，不验证或使用保存的画像。自签哈希只用于内容与记录绑定，不能证明 Jev 确实被调用。'},en:{title:'One history. Three predictions.',intro:'Freeze the learned state on the early rounds, then inspect later choices. Jev has results only after an explicit run.',frozen:n=>`Training frozen at round ${n} · Later choices never update the model`,round:n=>`Held-out round ${n}`,target:id=>`First finally-kept picture: ${id}`,none:'No scorable picture choice this round; it is not counted as zero loss.',picked:'Kept',baseline:'Reference baseline',learning:'Current learning model',jev:'Jev · Offline',nll:'Cumulative conditional loss · Lower is better',perRound:'This round’s conditional loss',notRun:'Not run. No substitute scores were filled in.',failed:'Failed. No substitute scores were filled in.',curve:'As held-out rounds accumulate',scope:'Probabilities condition on choosing one picture; “none” is not scored. Gold outlines mark actual keeps. Empty choices and rounds without alternatives are unscored. Eligibility may use whole-round cumulative visibility, so this is not a prospective recommendation test.',technical:'Technical details and raw JSON',provenance:'Only validated event facts are used. A saved profile is neither verified nor used. A self-authored hash binds content to the record; it cannot prove Jev was actually called.'}};
function node(tag,text,className){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n}function fmt(x){return typeof x==='number'?x.toFixed(3):'—'}function draw(){const t=words[lang],index=Number($('step').value),row=report.rounds[index];document.documentElement.lang=lang;document.title='Norn · '+t.title;$('language').textContent=lang==='zh'?'English':'中文';$('title').textContent=t.title;$('intro').textContent=t.intro;$('context').textContent=t.frozen(report.training.cutoff);$('step').max=Math.max(0,report.rounds.length-1);$('step').setAttribute('aria-label',lang==='zh'?'留出轮次':'Held-out round');$('counter').textContent=`${index+1} / ${report.rounds.length}`;$('round').textContent=row?t.round(row.round):'—';$('target').textContent=row&&row.scored?t.target(row.target):t.none;$('pictures').replaceChildren();$('lanes').replaceChildren();if(row){row.candidate_ids.forEach(id=>{const box=node('div',undefined,'picture'+(row.picks.includes(id)?' picked':'')),image=node('img');const url=report.cards?.[id]?.thumbnail;if(typeof url==='string'&&url.startsWith('data:image/webp;base64,'))image.src=url;image.alt=id;box.append(image,node('small',id));if(row.picks.includes(id))box.append(node('b',t.picked));$('pictures').append(box)});names.forEach(name=>{const lane=row.lanes[name],box=node('article',undefined,'lane');box.dataset.lane=name;box.append(node('h2',t[name]));if(lane.status!=='ok'){box.append(node('p',lane.status==='failed'?t.failed:t.notRun,'muted'));}else{box.append(node('p',fmt(row.cumulative[name+'_nll']),'metric'),node('p',t.nll,'scope'),node('p',`${t.perRound} ${fmt(lane.target_nll)}`,'scope'));lane.order.forEach(id=>{const i=row.candidate_ids.indexOf(id),line=node('div',undefined,'barrow'+(row.picks.includes(id)?' picked':'')),track=node('div',undefined,'track'),fill=node('div',undefined,'fill');fill.style.width=`${lane.probabilities[i]*100}%`;track.append(fill);line.append(node('span',id),track,node('span',`${Math.round(lane.probabilities[i]*100)}%`));box.append(line)})}$('lanes').append(box)})}$('curve-title').textContent=t.curve;$('scope').textContent=t.scope;$('technical').textContent=t.technical;$('provenance').textContent=t.provenance;$('json').textContent=JSON.stringify(report,null,2);$('legend').replaceChildren(...names.filter(n=>report.lanes[n].status==='ok').map(n=>node('span',t[n],n)));curve(index,t)}
function curve(index,t){const svg=$('curve'),ns='http://www.w3.org/2000/svg',w=svg.clientWidth||600,h=180,pad=28;svg.replaceChildren();svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.setAttribute('aria-label',t.nll);const values=report.rounds.flatMap(r=>names.map(n=>r.cumulative[n+'_nll'])).filter(Number.isFinite),max=Math.max(1,...values)*1.1,count=report.rounds.length;const make=(tag,attrs,text)=>{const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text!==undefined)n.textContent=text;svg.append(n);return n};const x=i=>pad+i*(w-2*pad)/Math.max(1,count-1),y=v=>h-pad-v*(h-2*pad)/max;make('path',{d:`M${pad},${pad}V${h-pad}H${w-pad}`,fill:'none',stroke:'var(--line)'});[0,max].forEach(v=>make('text',{x:2,y:y(v)+4,fill:'var(--muted)','font-size':11},v.toFixed(1)));names.forEach(name=>{let d='';report.rounds.forEach((r,i)=>{const v=r.cumulative[name+'_nll'];if(Number.isFinite(v))d+=(d?'L':'M')+x(i)+','+y(v)});if(d)make('path',{d,fill:'none',stroke:`var(--${name})`,'stroke-width':2});const v=report.rounds[index]?.cumulative[name+'_nll'];if(Number.isFinite(v))make('circle',{cx:x(index),cy:y(v),r:4,fill:`var(--${name})`})});[0,count-1].filter((v,i,a)=>a.indexOf(v)===i).forEach(i=>make('text',{x:x(i),y:h-6,fill:'var(--muted)','font-size':11,'text-anchor':i?'end':'start'},String(report.rounds[i]?.round||'')))}
$('language').addEventListener('click',()=>{lang=lang==='zh'?'en':'zh';draw()});$('step').addEventListener('input',draw);window.addEventListener('resize',draw);draw();})();
</script></body></html>'''
