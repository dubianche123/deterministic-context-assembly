#!/usr/bin/env python3
"""Verify X-Ray's pure prediction/replay interfaces, locally or on the public site.

Only GET /api/health and POST /api/trajectory are allowed. This command never
requests a reading, dialogue, or Jev prediction. Supplied offline predictions
are replayed, not regenerated. Reports contain hashes/counts and transport
metadata, never cookies, journal contents, pictures, or recorded choices.
"""

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from norn.core import engine_module
from norn.events import content_hash
from norn.research_inspection import validated
from scripts.verify_cloudflare import Client, VerificationError, require


SCHEMA_VERSION = "norn.xray-comparison-acceptance.v1"
MAX_INPUT_BYTES = 8 * 1024 * 1024
DISPLAY_TOLERANCE = 1e-9
ALLOWED_ROUTES = {("GET", "/api/health"), ("POST", "/api/trajectory")}


class ReadOnlyClient(Client):
    """Fail closed if an acceptance step ever attempts a generation route."""

    def request(self, method, path, body=None, **kwargs):
        require((method, path) in ALLOWED_ROUTES, "Verifier may only use health and trajectory")
        return super().request(method, path, body, **kwargs)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _no_constant(value):
    raise ValueError("Nonfinite JSON numbers are not allowed")


def read_document(path):
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_INPUT_BYTES + 1)
    require(len(raw) <= MAX_INPUT_BYTES, "Input exceeds the 8 MiB limit")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_no_constant)
    require(type(value) is dict, "Input must contain a JSON object")
    return value


def _close(actual, expected):
    return (actual is None if expected is None else type(actual) in (int, float)
            and math.isfinite(actual) and math.isclose(actual, expected,
                                                     rel_tol=DISPLAY_TOLERANCE,
                                                     abs_tol=DISPLAY_TOLERANCE))


def _numbers_match(actual, expected):
    return type(actual) is list and len(actual) == len(expected) and all(
        _close(a, b) for a, b in zip(actual, expected))


def _probabilities(scores):
    if not scores:
        return []
    masses = [math.exp(value - max(scores)) for value in scores]
    return [mass / sum(masses) for mass in masses]


def _facts(journal):
    """Independent event-only audit of completed sets, targets and count snapshots."""
    rows, shown, order, active, ready, start, number = [], [], [], set(), None, None, 0
    for position, event in enumerate(journal["events"]):
        kind = event["type"]
        if kind == "round_started":
            shown, order, ready, start = list(event["card_ids"]), [], None, position
            number = event["round"]
        elif kind == "round_ready":
            ready = event
        elif kind == "card_selected":
            active.add(event["card_id"])
            order.append(event["card_id"])
        elif kind == "card_deselected":
            active.discard(event["card_id"])
        elif kind == "round_completed":
            visible = event.get("seen_card_ids")
            options = shown if visible is None else [card for card in shown if card in visible]
            picks = list(dict.fromkeys(card for card in order if card in active and card in options))
            layout = ready.get("layout") if ready is not None else None
            rows.append({"round": number, "candidate_ids": list(options), "picks": picks,
                "target": picks[0] if picks else None,
                "prefix_hash": content_hash({"schema_version": journal["schema_version"],
                                             "events": journal["events"][:start]}),
                "display": {"planned_count": len(shown),
                    "ready_loaded_count": len(shown) - len(ready["unloaded_card_ids"]) if ready is not None else None,
                    "ever_seen_count": len(visible) if visible is not None else None,
                    "ready_columns": len({slot[1] for slot in layout}) if layout is not None else None,
                    "eligible_count": len(options),
                    "eligibility_source": "cumulative_seen" if visible is not None else "planned_fallback"},
                "planned_ids": list(shown)})
    return rows


def verify(url, journal, comparison_report, *, client_factory=ReadOnlyClient):
    """Run bounded, zero-model HTTP checks; do not verify a saved fitted profile."""
    research = engine_module("research_deck")
    deck = research.load_deck(research.deck_of(journal))
    replay = validated(journal, deck, require_complete=False)
    journal = replay["journal"]
    facts = _facts(journal)
    client = client_factory(url, retries=0, timeout=90)
    checks = {}

    def check(name, condition):
        require(condition, name)
        checks[name] = True

    health, _ = client.ok("GET", "/api/health")
    check("health_ready", health.get("status") == "ok")
    path, _ = client.ok("POST", "/api/trajectory", {"journal": journal})
    check("v6_trajectory_contract", path.get("schema_version") == "norn.research-trajectory.v1"
          and path.get("deck") == deck.label and path.get("events_hash") == replay["events_hash"])
    states, comparison = path.get("states", []), path.get("comparison", {})
    check("one_state_per_completed_round", len(states) == len(facts) + 1)
    check("prequential_card_conditional_contract", comparison.get("schema_version") == "norn.pamela-comparison.v1"
          and comparison.get("mode") == "prequential"
          and comparison.get("scope") == "card_conditional_first_finally_kept"
          and comparison.get("events_hash") == replay["events_hash"] and comparison.get("deck") == deck.label)
    cases = comparison.get("rounds", [])
    check("one_comparison_per_completed_round", len(cases) == len(facts))
    check("zero_model_dispatch_in_trajectory", path.get("provider_calls") == comparison.get("provider_calls") == 0)
    check("jev_not_run_by_default", comparison.get("lanes", {}).get("jev", {}).get("status") == "not_run")
    running, scored, fallback, active_prefix = {"baseline": 0.0, "learning": 0.0}, 0, 0, 0
    common_options = prefix_identity = target_identity = counts = predictions = metrics = True
    unseen_recommendations = True
    shown = set()
    for index, (case, fact) in enumerate(zip(cases, facts)):
        before, after = states[index], states[index + 1]
        options, target = fact["candidate_ids"], fact["target"]
        common_options &= (case.get("candidate_ids") == options
                           and case.get("options_hash") == content_hash({"deck": deck.label,
                               "candidate_ids": options, "scope": comparison["scope"]}))
        prefix_identity &= (case.get("cutoff_rounds") == index and case.get("round") == fact["round"]
                            and case.get("prefix_hash") == fact["prefix_hash"]
                            and case.get("layer_before") == before.get("layer"))
        target_identity &= case.get("target") == target and case.get("picks") == fact["picks"]
        counts &= case.get("display") == fact["display"]
        is_scored = target is not None and len(options) >= 2
        metrics &= (case.get("scored") is is_scored
                    and case.get("skipped_reason") == ("no_pick" if target is None else
                                                        "no_alternative" if not is_scored else None))
        for name in ("baseline", "learning"):
            weights = [1.0] + [0.0] * 8 if name == "baseline" else before["weights"]
            scores = [sum(w * v for w, v in zip(weights, deck.x2[card])) for card in options]
            probabilities = _probabilities(scores)
            lane = case.get("lanes", {}).get(name, {})
            ranks = [1 + sum(other > score for other in scores) for score in scores]
            order = sorted(options, key=lambda card: (-scores[options.index(card)], card))
            predictions &= (lane.get("status") == "ok" and _numbers_match(lane.get("scores"), scores)
                            and _numbers_match(lane.get("probabilities"), probabilities)
                            and lane.get("ranks") == ranks and lane.get("order") == order)
            loss = -math.log(max(probabilities[options.index(target)], 1e-12)) if is_scored else None
            metrics &= _close(lane.get("target_nll"), loss)
            if is_scored:
                running[name] += loss
        if is_scored:
            scored += 1
        for name in running:
            metrics &= _close(case.get("cumulative", {}).get(f"{name}_nll"),
                              running[name] / scored if scored else None)
        metrics &= case.get("cumulative", {}).get("n") == scored
        predictions &= case.get("lanes", {}).get("jev", {}) == {"status": "not_run"}
        if before.get("layer") == "population":
            fallback += 1
            predictions &= (case.get("same_predictions") is True and
                            case["lanes"]["baseline"]["probabilities"] == case["lanes"]["learning"]["probabilities"])
        else:
            active_prefix += 1
        shown.update(fact["planned_ids"])
        unseen_recommendations &= not bool(set(after.get("unseen_top", [])) & shown)
    check("same_recorded_candidate_set_for_both_lanes", common_options)
    check("each_round_uses_r_minus_one_prefix", prefix_identity)
    check("actual_first_finally_kept_target_and_all_picks", target_identity)
    check("count_and_layout_snapshots_match_events", counts)
    check("utilities_probabilities_and_ranks_match_prefix_weights", predictions)
    check("unscored_rounds_excluded_from_loss_denominator", metrics)
    check("recommendations_exclude_previously_displayed_cards", unseen_recommendations)
    check("summary_uses_only_scorable_rounds", comparison.get("summary", {}).get("n") == scored
          and all(_close(comparison.get("summary", {}).get(f"{name}_nll"),
                         total / scored if scored else None) for name, total in running.items()))
    imported, _ = client.ok("POST", "/api/trajectory", {"journal": journal, "comparison_report": comparison_report})
    saved, verification = imported.get("offline_comparison", {}), imported.get("offline_comparison_verification", {})
    check("whole_frozen_report_preserved", saved == comparison_report and saved.get("mode") == "frozen_prefix")
    check("report_binding_verified_without_provider_authentication",
          verification.get("record_binding_verified") is True
          and verification.get("report_content_hash_verified") is True
          and verification.get("provider_call_authenticated") is False)
    check("offline_import_keeps_prequential_view_separate", imported.get("comparison") == comparison)
    check("zero_model_dispatch_on_saved_import", imported.get("provider_calls") == 0)
    corrupt = deepcopy(comparison_report)
    corrupt["report_hash"] = "sha256:" + "0" * 64
    status, _, _ = client.request("POST", "/api/trajectory", {"journal": journal, "comparison_report": corrupt})
    check("tampered_report_hash_rejected", status == 400)
    stale = deepcopy(comparison_report)
    stale["training"]["prefix_hash"] = "sha256:" + "0" * 64
    for case in stale["rounds"]:
        case["prefix_hash"] = stale["training"]["prefix_hash"]
    stale["report_hash"] = content_hash({key: value for key, value in stale.items() if key != "report_hash"})
    status, _, _ = client.request("POST", "/api/trajectory", {"journal": journal, "comparison_report": stale})
    check("self_rehashed_wrong_prefix_rejected", status == 400)
    status, _, _ = client.request("POST", "/api/trajectory", {"journal": journal,
                                                             "comparison_report": comparison_report,
                                                             "allow_remote": True})
    check("unknown_request_fields_rejected", status == 400)
    check("only_pure_endpoints_used", all((call["method"], call["route"]) in ALLOWED_ROUTES for call in client.calls))
    return {"schema_version": SCHEMA_VERSION, "target_origin": client.origin, "checks": checks,
            "events_hash": replay["events_hash"], "deck": deck.label,
            "comparison_report_hash": comparison_report.get("report_hash"),
            "counts": {"completed_rounds": len(facts), "scored_rounds": scored,
                       "reference_fallback_prefixes": fallback, "personalized_prefixes": active_prefix,
                       "offline_training_rounds": saved["training"]["cutoff"]},
            "model_calls": 0, "jev_calls": 0, "collection_writes": 0, "http_calls": client.calls,
            "display_number_comparison_tolerance": DISPLAY_TOLERANCE,
            "scope": "Engineering replay only. Whole-round cumulative visibility may define the options; "
                     "not prospective click accuracy or a causal recommendation result. Display-number tolerance "
                     "does not replace strict profile hash verification. Imported report hashes do not authenticate Jev calls."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--comparison-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new JSON report file")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("--output must be a new file")
    try:
        document = read_document(args.journal)
        journal = document.get("journal") if document.get("schema_version") == "norn.research-replay.v1" else document
        require(type(journal) is dict, "Input must contain an event journal")
        saved = read_document(args.comparison_report)
        report = verify(args.url, journal, saved)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.write("\n")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"X-Ray verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"checks": len(report["checks"]), "model_calls": 0, "jev_calls": 0,
                      "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
