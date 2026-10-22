"""Emit complete deterministic outputs for exact native/Pyodide comparison.

This uses synthetic choices and mock text only. Compare the ``checks`` objects
in the two JSON files exactly; ``tolerance_checks`` (native libm against the
NumPy prototype) and ``trajectory_timing`` are compared by tolerance, and
elapsed times and environment are diagnostic.
"""

import argparse
import json
from pathlib import Path
import platform
import sys
import time


def edge_journal(total, edge):
    events = []
    def add(kind, **fields):
        events.append({"sequence": len(events) + 1, "event_id": "portable-" + str(len(events) + 1),
                       "type": kind, **fields})
    for number in range(1, total + 1):
        ids = [f"NORN_{(number - 1) * 3 + index:04}" for index in (1, 2, 3)]
        add("round_started", round=number, card_ids=ids)
        if number in (1, edge, total):
            add("card_selected", round=number, card_id=ids[0])
        add("round_completed", round=number, duration_ms=1000 + number * 131)
    add("session_completed")
    return {"schema_version": "norn.events.v1", "events": events}


def timing_journal(total):
    """4-6 cards per round cycling through the deck, as the page draws them."""
    events, active, cursor = [], set(), 0
    def add(kind, **fields):
        events.append({"sequence": len(events) + 1, "event_id": "timing-" + str(len(events) + 1),
                       "type": kind, **fields})
    for number in range(1, total + 1):
        ids = [f"NORN_{(cursor + index) % 256 + 1:04}" for index in range(4 + number % 3)]
        cursor += len(ids)
        add("round_started", round=number, card_ids=ids)
        for index, card in enumerate(ids):
            if (number + index) % 3 == 0 and card not in active:
                active.add(card)
                add("card_selected", round=number, card_id=card)
        add("round_completed", round=number, duration_ms=1500 + number * 17)
    add("session_completed")
    return {"schema_version": "norn.events.v1", "events": events}


def choice_model_demo(repo, engine_module):
    """Largest deviation from the NumPy prototype's posterior path, per contract."""
    model, numeric = engine_module("choice_model"), engine_module("numeric")
    demo = json.loads((repo / "tests/fixtures/choice-model-demo.json").read_text(encoding="utf-8"))
    result = {}
    for contract in (numeric.NATIVE_CONTRACT, numeric.PORTABLE_CONTRACT):
        with numeric.use(contract):
            states = model.path(model.observations(demo["journal"]))
            deviation = 0.0
            for mine, reference in zip(states, demo["states"]):
                covariance = mine["covariance"]
                pairs = list(zip(mine["theta"], reference["theta"]))
                pairs += [(covariance[index][index], value) for index, value in enumerate(reference["cov_diag"])]
                pairs += [(covariance[row][column], reference["cov4"][row][column])
                          for row in range(4) for column in range(4)]
                deviation = max([deviation] + [abs(actual - expected) for actual, expected in pairs])
            catalog = model.load_catalog()
            scores = model.card_scores(states[-1]["theta"])
            deviation = max([deviation] + [abs(scores[catalog.index[card]] - expected)
                                           for card, expected in demo["final_scores"].items()])
            result[contract] = {"states": len(states), "expected_states": len(demo["states"]),
                                "max_abs_deviation": deviation,
                                "top_equal": model.top_cards(scores, catalog) == demo["states"][-1]["top"]}
    return result


def run(repo):
    sys.path.insert(0, str(repo))
    from norn.benchmark import default_journal
    from norn.core import engine_module
    from norn.events import content_hash
    from norn.profile import build_event_profile
    from norn.replay import replay_document
    from norn.service import NornService
    checks, timings = {}, {}
    def record(name, function):
        started = time.perf_counter()
        checks[name] = function()
        timings[name] = round((time.perf_counter() - started) * 1000, 3)
    record("small_events", lambda: replay_document(json.loads((repo / "tests/fixtures/replay-session.json").read_text())))
    record("legacy_profile1", lambda: replay_document(json.loads((repo / "tests/fixtures/legacy-profile-v1.json").read_text())))
    v3 = "norn.scoring.v3"  # No longer the default; pinned so these keep their 0.7.0 meaning.
    record("24_round_profile3", lambda: build_event_profile(default_journal(), algorithm_version=v3))
    record("59_round_profile3", lambda: build_event_profile(edge_journal(59, 25), algorithm_version=v3))
    record("75_round_profile3", lambda: build_event_profile(edge_journal(75, 19), algorithm_version=v3))
    record("24_round_profile4_default", lambda: build_event_profile(default_journal()))
    for total in (20, 80, 256):
        record(f"{total}_round_profile4", lambda: build_event_profile(timing_journal(total)))
    def session():
        service = NornService()
        analysis = service.analyze({"journal": default_journal(), "request_id": "probe-reading"})
        assert service.analyze({"journal": default_journal(), "request_id": "probe-reading"}) == analysis
        key = analysis["session_id"]
        first = service.inspect_session(key)
        for number in range(1, 4):
            response = service.dialogue({"session_id": key, "user_message": f"为什么，追问{number}？",
                                         "turn_id": "probe-turn-" + str(number)})
            assert response["status"] == "ok"
        last = service.inspect_session(key)
        assert last["profile"] == first["profile"]
        return {"profile_hash": first["profile_hash"], "context_hash": first["context"]["context_hash"],
                "first_prompt_hash": content_hash(first["calls"][0]["prompt"]),
                "last_prompt_hash": content_hash(last["calls"][-1]["prompt"]),
                "turn_count": response["turn_count"], "ended": response["ended"],
                "call_count": len(last["calls"]), "provider_calls": sum(row["provider_calls"] for row in last["calls"])}
    record("mock_reading_and_three_followups", session)
    def pressure():
        events, active = [], set()
        ids = [f"NORN_{number:04}" for number in range(1, 17)]
        def add(kind, **fields):
            events.append({"sequence": len(events) + 1, "event_id": "pressure-" + str(len(events) + 1),
                           "type": kind, **fields})
        for number in range(1, 257):
            add("round_started", round=number, card_ids=ids)
            selected = set(ids[:8] if number % 2 else ids[8:])
            for card in sorted(active - selected):
                add("card_deselected", round=number, card_id=card)
            for card in sorted(selected - active):
                add("card_selected", round=number, card_id=card)
            active = selected
            add("round_completed", round=number, duration_ms=1000)
        add("session_completed")
        model = engine_module("preference_model")
        checkpoint = model.train([{"schema_version": "norn.events.v1", "events": events}])
        return {"event_count": len(events), "feature_count": checkpoint["feature_count"],
                "pair_count": checkpoint["training"]["pair_count"], "checkpoint": checkpoint,
                "scores": model.predict_scores(checkpoint)}
    record("256_round_4096_pair_pressure", pressure)
    # Native-libm results: within tolerance of the prototype, not compared exactly.
    started = time.perf_counter()
    tolerance = {"choice_model_demo": choice_model_demo(repo, engine_module)}
    timings["choice_model_demo"] = round((time.perf_counter() - started) * 1000, 3)
    # What POST /api/trajectory runs, plus serialising its reply; a display
    # projection in native libm, so its hash is informational only.
    trajectory = {}
    for total in (20, 80, 256):
        source = timing_journal(total)
        started = time.perf_counter()
        body = json.dumps(NornService().trajectory({"journal": source}), ensure_ascii=False, allow_nan=False)
        trajectory[f"{total}_rounds"] = {"elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                                         "event_count": len(source["events"]), "reply_bytes": len(body.encode()),
                                         "reply_hash": content_hash(json.loads(body))}
    return {"schema_version": "norn.numeric-runtime-outputs.v1", "python": sys.version,
            "platform": platform.platform(), "checks": checks, "tolerance_checks": tolerance,
            "trajectory_timing": trajectory, "elapsed_ms": timings,
            "scope": "Local runtime only; not deployed Worker CPU or total memory measurements."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    output = run(arguments.repo)
    arguments.output.write_text(json.dumps(output, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps({"python": output["python"], "elapsed_ms": output["elapsed_ms"]}, ensure_ascii=False))
