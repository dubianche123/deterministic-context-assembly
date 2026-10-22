"""Frozen-prefix, side-by-side fast-thinker experiments; no production mutations.

This compares prediction systems, independently of the prose renderer. Remote
decision services are opt-in. Ordinary use never reads a credential or sends a request.
"""

from copy import deepcopy
import base64
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import sys
import time

from .core import engine_module, web_root
from .events import content_hash, replay_events

SCHEMA_VERSION = "norn.fast-comparison.v1"
INPUT_VERSION = "norn.fast-comparison.input.v1"
PROBABILITY_FLOOR = 1e-12
STOP = "__STOP__"
LABELS = {"heuristic": "Fixed rules", "ml": "Original personal ML", "ml_policy": "Guarded personal ML", "decision": "OpenAI Decisions",
          "stop_only": "Stop-only reference"}


def _steps(rows, *, first_only=False):
    """The existing target: final kept cards in first-click order, then stop."""
    result = []
    for row in rows:
        remaining = list(row["shown"])
        for target in [*row["picks"], STOP]:
            # A forced stop after choosing every card conveys no information.
            if remaining:
                result.append({"id": f"decision_{len(result) + 1:04d}", "round": row["round"],
                               "candidate_ids": list(remaining), "target": target, "predictions": {}})
                if first_only:
                    break
            if target != STOP:
                remaining.remove(target)
    return result


def prepare_experiment(journal, *, train_rounds=None, max_decisions=128):
    """Validate the full journal, then detach training facts before any fitting."""
    replay_events(journal)
    model = engine_module("choice_model")
    rows = model.observations(journal)
    if len(rows) < 5:
        raise ValueError("Comparison needs at least five completed rounds")
    cutoff = len(rows) - max(1, math.ceil(len(rows) * 0.2)) if train_rounds is None else train_rounds
    if type(cutoff) is not int or not 1 <= cutoff < len(rows):
        raise ValueError("train_rounds must leave at least one training and one test round")
    if type(max_decisions) is not int or not 1 <= max_decisions <= 512:
        raise ValueError("max_decisions must be between 1 and 512")
    closes = [i for i, event in enumerate(journal["events"]) if event["type"] == "round_completed"]
    prefix = {"schema_version": journal["schema_version"],
              "events": deepcopy(journal["events"][:closes[cutoff - 1] + 1])}
    replay_events(prefix, require_complete=False)
    # One independent first-kept-or-stop question per held-out round. Sending
    # all shrinking option sets in one batch could reveal later test choices
    # to a provider that does not guarantee cross-question isolation.
    decisions = _steps(rows[cutoff:], first_only=True)
    if not decisions:
        raise ValueError("The held-out rounds have no observable choice sets")
    if len(decisions) > max_decisions:
        raise ValueError("Held-out decisions exceed max_decisions; choose a smaller explicit journal or raise the bound")
    return prefix, rows[:cutoff], decisions, len(rows)


def build_decision_request(prefix, rows, decisions, *, input_mode="numeric"):
    """Never send test targets, timings, full profiles, or later personal state.

    Per-step remaining options condition the same sequential-choice task as the
    local MNL. Card identifiers are opaque hashes to avoid sequential-ID cues.
    """
    if input_mode not in ("numeric", "semantic"):
        raise ValueError("Decision input_mode must be numeric or semantic")
    model = engine_module("choice_model")
    catalog = model.load_catalog()
    metadata = engine_module("fast_thinker")._load_cards()
    prefix_hash = content_hash(prefix)

    def alias(card):
        return "item_" + content_hash({"prefix": prefix_hash, "card": card}).split(":")[1][:16]

    def describe(card):
        values = {"card_features": dict(zip(model.FEATURE_NAMES, catalog.features[catalog.index[card]]))}
        if input_mode == "semantic":
            values.update(scene=metadata[card]["scene"], motifs=list(metadata[card]["motifs"]))
        return values

    state = {"schema_version": INPUT_VERSION, "feature_names": list(model.FEATURE_NAMES),
             "past_completed_rounds": [
                 {"options": {alias(card): describe(card) for card in row["shown"]},
                  "kept_in_order": [alias(card) for card in row["picks"]], "then_stopped": True}
                 for row in rows],
             "scope": "Infer this visitor's relative picture preference only from these past rounds. "
                      "Weights are not updated with test outcomes. Predict a final-kept sequential choice, not a raw mouse click."}
    questions, mappings = {}, {}
    for decision in decisions:
        mapping = {alias(card): card for card in decision["candidate_ids"]}
        mapping[STOP] = STOP
        questions[decision["id"]] = {
            "type": "choice",
            "instructions": "Given only the past rounds, which visible picture will this visitor keep first, "
                            "or will they keep no picture in this round? Treat STOP as a real possible choice. "
                            "Use feature patterns from past choices; do not infer a decision from option identifiers.",
            "criteria": {**{alias(card): json.dumps(describe(card), ensure_ascii=False, sort_keys=True)
                             for card in decision["candidate_ids"]},
                         STOP: "Keep no further picture in this round."}}
        mappings[decision["id"]] = mapping
    return state, questions, mappings


def _summary(decisions, name, catalog):
    losses, card_losses, within_losses, briers, correct, elapsed = [], [], [], [], [], []
    zeros, undefined_cards = 0, 0
    for decision in decisions:
        value = decision["predictions"].get(name, {})
        if value.get("status") != "ok":
            continue
        probabilities, target = value["probabilities"], decision["target"]
        probability = probabilities[target]
        zeros += probability == 0
        losses.append(-math.log(max(probability, PROBABILITY_FLOOR)))
        briers.append(math.fsum((p - (option == target)) ** 2 for option, p in probabilities.items()))
        highest = max(probabilities.values())
        ties = [key for key, p in probabilities.items() if p == highest]
        correct.append(1 / len(ties) if target in ties else 0)
        elapsed.append(value.get("elapsed_ms") or 0)
        if target != STOP:
            mass = math.fsum(probabilities[card] for card in decision["candidate_ids"])
            if mass:
                card_losses.append(-math.log(max(probability / mass, PROBABILITY_FLOOR)))
            else:
                undefined_cards += 1
            same_type = [card for card in decision["candidate_ids"]
                         if catalog.types[catalog.index[card]] == catalog.types[catalog.index[target]]]
            mass = math.fsum(probabilities[card] for card in same_type)
            if len(same_type) >= 2 and mass:
                within_losses.append(-math.log(max(probability / mass, PROBABILITY_FLOOR)))
    mean = lambda values: math.fsum(values) / len(values) if values else None
    return {"all_nll": mean(losses), "cards_nll": mean(card_losses), "within_type_nll": mean(within_losses),
            "brier": mean(briers), "top1_accuracy": mean(correct), "decision_count": len(losses),
            "cards_count": len(card_losses), "within_type_count": len(within_losses),
            "undefined_cards_count": undefined_cards, "zero_probability_targets": zeros,
            "coverage": len(losses) / len(decisions), "provider_calls": 0, "elapsed_ms": math.fsum(elapsed)}


def compare_journal(journal, *, train_rounds=None, decision_client=None, input_mode="numeric",
                    max_decisions=128, provenance="user_journal", include_images=False):
    research = engine_module("research_deck")
    if research.deck_of(journal):
        from .pamela_compare_cli import compare_pamela
        return compare_pamela(journal, train_rounds=train_rounds, decision_client=decision_client,
                              input_mode=input_mode, max_decisions=max_decisions,
                              provenance=provenance, include_images=include_images)
    from .fast_thinkers import PROBABILITY_TOLERANCE, prepare_local, prepare_policy, validate_probabilities
    if provenance not in ("fixture", "user_journal"):
        raise ValueError("Unknown comparison provenance")
    if input_mode not in ("numeric", "semantic"):
        raise ValueError("Decision input_mode must be numeric or semantic")
    prefix, rows, decisions, total = prepare_experiment(
        journal, train_rounds=train_rounds, max_decisions=max_decisions)
    numeric, model = engine_module("numeric"), engine_module("choice_model")
    catalog = model.load_catalog()
    report = {"schema_version": SCHEMA_VERSION,
              "provenance": {"kind": provenance, "note": "Automated fixture; no human result or winner claim."
                             if provenance == "fixture" else "One supplied journal; descriptive results, no population claim."},
              "events_hash": content_hash(journal), "numerical_contract": numeric.PORTABLE_CONTRACT,
              "probability_sum_tolerance": PROBABILITY_TOLERANCE,
              "training": {"rounds": len(rows), "total_rounds": total, "prefix_hash": content_hash(prefix),
                           "state_frozen": True, "test_rounds": total - len(rows),
                           "target_scope": "first_kept_or_stop_per_round"},
              "models": {}, "decisions": decisions,
              "limits": ["The scored target is each test round's first finally-kept card, or STOP if none; not a raw click.",
                         "Training uses all sequential kept choices plus STOP; these first-choice NLLs are not the full sequential evaluator's metric.",
                         "Batch questions never include target-dependent shrinking option sets from the same test round.",
                         "Personal state is frozen at the training cutoff; held-out choices do not update it.",
                         "Original ML is retained; the guarded policy uses only prequential training-prefix content evidence to select its fallback.",
                         "Previously inspected fixtures are development regressions, not new independent validation.",
                         "Single-journal differences are descriptive; human visitor-level evaluation is still required.",
                         "NLL uses a 1e-12 floor; zero-probability targets are reported separately.",
                         "Remote confidence is provider metadata, not measured personal accuracy.",
                         "Semantic remote input adds scene/motif information beyond the current local 12-feature ML.",
                         "No new-material scores: the frozen unlabeled probe cannot invent four-axis labels."]}
    with numeric.use(numeric.PORTABLE_CONTRACT):
        predictors = prepare_local(prefix, rows)
        predictors["ml_policy"] = prepare_policy(prefix, rows)
        for name, predictor in predictors.items():
            report["models"][name] = {"label": LABELS[name], "status": "ok", "metadata": predictor.metadata,
                                      "prepare_ms": predictor.prepare_ms}
            for decision in decisions:
                started = time.perf_counter()
                probabilities = predictor.predict(decision["candidate_ids"])
                validate_probabilities(probabilities, decision["candidate_ids"])
                decision["predictions"][name] = {"status": "ok", "probabilities": probabilities,
                                                 "elapsed_ms": (time.perf_counter() - started) * 1000}
    remote = {"label": getattr(decision_client, "label", LABELS["decision"]), "status": "unavailable",
              "reason": "not_enabled", "prepare_ms": 0,
              "metadata": {"input_mode": input_mode, "adapter_version": INPUT_VERSION,
                           "feature_version": catalog.feature_version,
                           "provider": getattr(decision_client, "provider", "openai")}}
    report["models"]["decision"] = remote
    if decision_client is not None:
        from .jev import JevError
        from .openai_decisions import OpenAIDecisionsError
        started = time.perf_counter()
        state, questions, mappings = build_decision_request(prefix, rows, decisions, input_mode=input_mode)
        remote["prepare_ms"] = (time.perf_counter() - started) * 1000
        remote["metadata"].update(request_hash=content_hash({"state": state, "questions": questions}),
                                  requested_model=decision_client.model, blind_card_identifiers=True)
        calls_before = decision_client.calls
        try:
            response = decision_client.decide(state, questions)
            remote.update(status="ok", model=response["model"], usage=response["usage"],
                          batch_elapsed_ms=response["elapsed_ms"], provider_calls=response["provider_calls"])
            remote.pop("reason", None)
            if "observed_model" in response:
                remote["metadata"]["observed_model"] = response["observed_model"]
            for decision in decisions:
                answer = response["answers"][decision["id"]]
                probabilities = {mappings[decision["id"]][option]: p for option, p in answer["probabilities"].items()}
                validate_probabilities(probabilities, decision["candidate_ids"])
                decision["predictions"]["decision"] = {"status": "ok", "probabilities": probabilities,
                                                        "confidence": answer["confidence"], "elapsed_ms": None,
                                                        "latency_scope": "one shared batch"}
        except (JevError, OpenAIDecisionsError, ValueError) as exc:
            remote.update(status="failed", reason=getattr(exc, "code", "invalid_probabilities"),
                          provider_calls=decision_client.calls - calls_before)
            for decision in decisions:
                decision["predictions"].pop("decision", None)
    for name, system in report["models"].items():
        system["summary"] = _summary(decisions, name, catalog)
        if name == "decision":
            system["summary"].update(provider_calls=system.get("provider_calls", 0),
                                      elapsed_ms=system.get("batch_elapsed_ms", 0), latency_scope="whole batch")
    baseline = report["models"]["stop_only"]["summary"]["all_nll"]
    for system in report["models"].values():
        value = system["summary"]["all_nll"]
        system["summary"]["gain_over_stop_only_nats"] = baseline - value if value is not None else None
    if include_images:
        root = web_root()
        report["cards"] = {card: {"thumbnail": "data:image/webp;base64," + base64.b64encode(
            (root / "assets" / "cards" / f"{card}.webp").read_bytes()).decode("ascii")}
            for card in sorted({card for decision in decisions for card in decision["candidate_ids"]})}
    return report


def add_compare_arguments(parser):
    parser.add_argument("path", nargs="?", type=Path, help="Explicit JSON journal or replay export; no automatic archive reads")
    parser.add_argument("--fixture", action="store_true", help="Use the labeled synthetic 24-round fixture")
    parser.add_argument("--train-rounds", type=int, help="Freeze after this many rounds (default: first 80%%)")
    parser.add_argument("--output", type=Path, help="Write a NEW comparison JSON file")
    parser.add_argument("--html", type=Path, help="Write a NEW standalone interactive HTML report")
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    parser.add_argument("--decision-provider", choices=("none", "openai", "typesafe"), default="none",
                        help="Explicitly send prefix/candidate features to this decision API (default: offline)")
    parser.add_argument("--decision-model", choices=("gpt-6-luna", "jev-1.13.0", "jev-1.14.0"))
    parser.add_argument("--decision-input", choices=("numeric", "semantic"), default="numeric")
    parser.add_argument("--max-decision-calls", type=int, choices=(1,), default=1, help="One batch, no automatic retries")
    parser.add_argument("--max-decisions", type=int, default=128)


def compare_main(args):
    try:
        if bool(args.path) == bool(args.fixture):
            raise ValueError("Choose exactly one explicit journal path or --fixture")
        if args.output and args.html and args.output.resolve() == args.html.resolve():
            raise ValueError("JSON and HTML output paths must differ")
        for path in (args.output, args.html):
            if path and path.exists():
                raise ValueError("Comparison output already exists; use a new path")
            if path and not path.parent.is_dir():
                raise ValueError("Comparison output parent directory does not exist")
        if args.fixture:
            from .benchmark import default_journal
            journal = default_journal()
        else:
            from .replay import replay_document
            from .server import MAX_REQUEST_BYTES, _no_duplicates, _no_constant
            with args.path.open("rb") as handle:
                raw = handle.read(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("Comparison input exceeds 8 MiB")
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
            if type(document) is not dict:
                raise ValueError("Comparison input must be a JSON object")
            research = engine_module("research_deck")
            # Preference evaluation consumes validated event facts, not a
            # saved native-libm profile. Strict `norn replay` remains unchanged.
            source = document.get("journal") if document.get("schema_version") == "norn.research-replay.v1" else document
            if research.deck_of(source):
                from .research_inspection import validated
                journal = validated(source, research.load_deck(research.deck_of(source)))["journal"]
            else:
                journal = replay_document(document)["journal"]
        client = None
        if args.decision_provider != "none":
            if args.decision_provider == "openai":
                from .openai_decisions import OpenAIDecisionsClient as Client
                key_name, default_model = "OPENAI_API_KEY", "gpt-6-luna"
            else:
                from .jev import JevClient as Client
                key_name, default_model = "TYPESAFE_API_KEY", "jev-1.13.0"
            key = os.environ.get(key_name, "")
            if not key:
                raise ValueError(f"{args.decision_provider} requires {key_name}; offline comparison needs no key")
            client = Client(key, model=args.decision_model or default_model, max_calls=args.max_decision_calls)
        elif args.decision_model:
            raise ValueError("--decision-model requires an explicit --decision-provider")
        # Reserve both destinations before a potentially paid dispatch; merely
        # checking exists() does not catch permissions or creation races.
        with ExitStack() as outputs:
            json_output = outputs.enter_context(args.output.open("x", encoding="utf-8")) if args.output else None
            html_output = outputs.enter_context(args.html.open("x", encoding="utf-8")) if args.html else None
            report = compare_journal(journal, train_rounds=args.train_rounds, decision_client=client,
                                     input_mode=args.decision_input, max_decisions=args.max_decisions,
                                     provenance="fixture" if args.fixture else "user_journal",
                                     include_images=bool(args.html))
            raw = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
            if json_output:
                json_output.write(raw)
            if html_output:
                if report.get("schema_version") == "norn.pamela-comparison.v1":
                    from .pamela_compare_cli import render_pamela_comparison as render_comparison
                else:
                    from .fast_compare_html import render_comparison
                html_output.write(render_comparison(report, language=args.language))
        if not args.output and not args.html:
            print(raw, end="")
        else:
            if report.get("schema_version") == "norn.pamela-comparison.v1":
                for name in ("baseline", "learning", "jev"):
                    value = report["summary"].get(name + "_nll")
                    print(f"{name}: {report['lanes'][name]['status']}; card-conditional NLL={value if value is not None else '—'}")
                print("Prose generation calls: 0. Frozen-prefix descriptive comparison; STOP is not scored.")
            else:
                for name in ("heuristic", "ml", "ml_policy", "decision"):
                    system = report["models"][name]
                    value = system["summary"]["all_nll"]
                    print(f"{system['label']}: {system['status']}; NLL={value if value is not None else '—'}")
                print("Prose generation calls: 0. Fixture differences are not human accuracy.")
        remote = report["lanes"]["jev"] if "lanes" in report else report["models"]["decision"]
        return 0 if remote["status"] != "failed" else 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Comparison failed: {exc}", file=sys.stderr)
        return 2
