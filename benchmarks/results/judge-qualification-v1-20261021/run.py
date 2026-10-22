"""Frozen, judge-only qualification; preparation is offline, execution requires --live.

Run from any directory. No readings are regenerated and no historical artifact
is changed. Credentials are read only from the explicitly selected private file.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts import prose_eval as prose
from scripts.eval_state import RunState
from scripts.eval_transport import EvalTransport

HERE = Path(__file__).resolve().parent
JUDGES = ("cn:mimo-v2.6-pro", "cn:hy4-preview")
NATURAL = (("cn:glm-5.3-flash", 0), ("cn:glm-5.3-flash", 4),
           ("cn:deepseek-v4.1-flash", 3), ("cn:deepseek-v4.1-flash", 4))


def write_new(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def prepare():
    source = ROOT / "benchmarks/results/prose-eval-v2.json"
    previous = json.loads(source.read_text())
    cases = []
    for model, fixture in NATURAL:
        versions = [next(row for row in previous["sessions"] if row["model"] == model
                         and row["fixture"] == fixture and row["prompt_version"] == version)
                    for version in (prose.PROMPT_VERSION, prose.PROMPT_V5)]
        assert all(row["turns"][0]["status"] == "ok" for row in versions)
        low, high, seed = prose.FIXTURES[fixture]
        journal = prose.simulate_records(1, visits=(1, 1), rounds=(low, high), seed=seed)[0]["journal"]
        block = prose.build_session_context(prose.export_replay(journal)["profile"], prose.PROMPT_V5)["block"]
        scenes = sorted({row["scene"] for row in block["evidence"]["selections"]})
        assert all(row["facts"] == scenes and row["rounds"] == block["total_rounds"] for row in versions)
        cases.append({"id": f"natural-{model.split(':')[1]}-fixture-{fixture}", "kind": "natural",
                      "generator": model, "fixture": fixture, "mode": versions[0]["mode"],
                      "journal": journal, "facts": prose.facts_for(journal),
                      "facts_source": "reconstructed deterministic journal; scenes and rounds checked against saved sessions",
                      "scenes": scenes, "texts": {"v4": versions[0]["turns"][0]["text"],
                                                   "v5": versions[1]["turns"][0]["text"]}})
    control_facts = cases[0]["facts"]
    scenes = cases[0]["scenes"]
    good = (f"你这次留下了「{scenes[0]}」和「{scenes[1]}」。它们是这一次选择中真实出现的画面。"
            "也许某些细节让你愿意多看一眼，也可能只是此刻的兴趣。你可以留意自己被哪一处吸引，"
            "也可以觉得暂时没有答案。这些选择只能提供这一次的线索，还不足以确定你的性格或未来。")
    bad = ("你已经反复选中了「火星基地的金色穹顶」和「深海潜水艇的红色驾驶舱」。这证明你天生就是"
           "不需要任何人的孤独领导者，童年一定遭遇过长期遗弃，你将来必定离开家人并成为商业巨头。"
           "这些画面清楚揭示了你终身不变的真实性格和已经注定的人生轨迹，不存在其他解释。")
    assert "火星基地" not in control_facts and "潜水艇" not in control_facts
    cases.append({"id": "control-grounded-vs-fabricated", "kind": "grounding_control",
                  "facts": control_facts, "texts": {"good": good, "bad": bad},
                  "expected": "good wins; grounded good > bad; overclaim good false and bad true"})
    cases.append({"id": "control-identical", "kind": "identical_control",
                  "facts": control_facts, "texts": {"left": good, "right": good},
                  "expected": "tie; all scores equal by side; overclaim false by both sides"})
    protocol = {"schema": "norn.judge-qualification.protocol.v1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": "benchmarks/results/prose-eval-v2.json", "judges": list(JUDGES),
                "judge_system": prose.JUDGE_SYSTEM, "cases": cases,
                "settings": {"max_tokens": 6000, "reasoning_effort": None, "temperature": 0,
                             "timeout_seconds": 180, "concurrency": 6, "attempts": 1,
                             "max_requests": 24, "model_fallback": False},
                "acceptance": {"valid_stopped_outputs_per_model": 12,
                               "grounding_control_passes": 2, "identical_control_passes": 2,
                               "natural_winner_consistency_min": 3, "natural_hard_reversals_max": 0},
                "limits": ["Small qualification gate, not a ranking or proof of judge quality.",
                           "Four natural pairs have three unique reconstructed journals and no human gold.",
                           "Coverage is sparse/dense/overflow and unbalanced between generators.",
                           "Scenes/rounds validation does not reconstruct historical per-round counts exactly.",
                           "Combined fabricated/overclaim control cannot isolate subtle hallucination from overclaiming.",
                           "Identical inputs are independent repeated requests, not a meaningful order swap.",
                           "Natural judging is relative to the supplied facts, which omit original visual statistics.",
                           "Model agreement is not correctness; prices and monetary cost are unknown."]}
    write_new(HERE / "protocol.json", protocol)
    return protocol


def summarize(rows, protocol):
    output = {}
    for model in protocol["judges"]:
        group = [row for row in rows if row["model"] == model]
        valid = [row for row in group if row["valid"]]
        control_kinds = sorted({case["kind"] for case in protocol["cases"] if case["kind"] != "natural"})
        control = {kind: sum(row.get("control_pass") is True for row in group if row["kind"] == kind)
                   for kind in control_kinds}
        comparisons = []
        for case in protocol["cases"]:
            if case["kind"] != "natural":
                continue
            pair = sorted([row for row in group if row["case"] == case["id"]], key=lambda row: row["order"])
            complete = len(pair) == 2 and all(row["valid"] for row in pair)
            winners = [row["normalized_winner"] for row in pair] if complete else []
            hard = complete and set(winners) == {"v4", "v5"}
            delta = {metric: {side: abs(pair[0]["normalized_scores"][metric][side] - pair[1]["normalized_scores"][metric][side])
                              for side in ("v4", "v5")} for metric in ("grounded", "resonance", "clarity")} if complete else None
            overclaim_drift = [side for side in ("v4", "v5") if complete and
                               pair[0]["normalized_scores"]["overclaim"][side] != pair[1]["normalized_scores"]["overclaim"][side]]
            comparisons.append({"case": case["id"], "complete": complete, "winners": winners,
                                "winner_consistent": complete and len(set(winners)) == 1,
                                "hard_reversal": hard, "score_absolute_drift": delta,
                                "overclaim_drift": overclaim_drift})
        consistent = sum(row["winner_consistent"] for row in comparisons)
        reversals = sum(row["hard_reversal"] for row in comparisons)
        accepted = (len(valid) == protocol["acceptance"]["valid_stopped_outputs_per_model"]
                    and all(value == 2 for value in control.values()) and consistent >= 3 and reversals == 0)
        durations = [sum(attempt["elapsed_ms"] for attempt in row["attempts"]) / 1000 for row in group]
        output[model] = {"requests": len(group), "valid_stopped": len(valid),
                         "strict_json": sum(row["strict_json"] for row in group),
                         "controls": control, "natural_consistent": consistent,
                         "natural_hard_reversals": reversals, "natural_pairs": comparisons,
                         "latency_seconds_median": statistics.median(durations) if durations else None,
                         "latency_seconds_max": max(durations) if durations else None,
                         "passes_predeclared_gate": accepted}
    return output


def live(protocol, key, resume):
    output = HERE / "results.json"
    # A frozen prompt variant applies only inside this experiment process.
    prose.JUDGE_SYSTEM = protocol["judge_system"]
    settings = protocol["settings"]
    transport = EvalTransport("http://127.0.0.1:17863/v1", key, concurrency=settings["concurrency"],
                              attempts=settings["attempts"], timeout=settings["timeout_seconds"],
                              max_requests=settings["max_requests"])
    started = time.monotonic()
    with RunState(output, protocol, resume=resume) as state:
        if output.exists():
            raise RuntimeError("Final report already exists; do not redispatch")

        def run(job):
            model, case, order = job
            scope = f"qualification-v1:{model}:{case['id']}:{order}"
            cached = state.get("qualification_result", scope)
            if cached is not None:
                return {**{key: value for key, value in cached.items()
                           if key not in ("kind", "cache_key", "case_kind")},
                        "kind": cached["case_kind"]}
            labels = list(case["texts"])
            if order:
                labels.reverse()
            sides = dict(zip(("A", "B"), labels))
            first, second = (case["texts"][sides[side]] for side in ("A", "B"))
            args = SimpleNamespace(judge=[model], state=state, transport=transport,
                                   cancelled=threading.Event(), judge_reasoning=None,
                                   judge_max_tokens=settings["max_tokens"], timeout=settings["timeout_seconds"])
            verdict = prose.judge(args, key, case["facts"], first, second, scope=scope)
            prompt = f"事实：\n{case['facts']}\n\n解读 A：\n{first}\n\n解读 B：\n{second}"
            messages = [{"role": "system", "content": prose.JUDGE_SYSTEM}, {"role": "user", "content": prompt}]
            request_key = prose.digest({"scope": scope, "model": model, "messages": messages,
                                        "reasoning": None, "max_tokens": settings["max_tokens"]})
            raw = state.get("judge_request", request_key)
            text = raw.get("text") if raw else None
            try:
                strict = prose.valid_verdict(json.loads(text))
            except (ValueError, TypeError):
                strict = False
            valid = prose.valid_verdict(verdict) and bool(raw) and raw.get("finish") == "stop"
            attempts = [item for call in verdict.get("calls", []) for item in call.get("attempts", [])]
            row = {"model": model, "case": case["id"], "kind": case["kind"], "order": order,
                   "side_labels": sides, "valid": valid, "strict_json": strict,
                   "raw_text": text, "finish_reason": raw.get("finish") if raw else None,
                   "verdict": verdict, "attempts": attempts,
                   "response_models": sorted({item["response_model"] for item in attempts if item.get("response_model")})}
            if valid:
                better = verdict["better"]
                row["normalized_winner"] = "tie" if better == "tie" else sides[better]
                scores = {metric: {sides[side]: verdict[metric][side] for side in ("A", "B")}
                          for metric in ("grounded", "resonance", "clarity", "overclaim")}
                row["normalized_scores"] = scores
                if case["kind"] == "grounding_control":
                    row["control_pass"] = (row["normalized_winner"] == "good"
                        and scores["grounded"]["good"] > scores["grounded"]["bad"]
                        and scores["overclaim"]["good"] is False and scores["overclaim"]["bad"] is True)
                elif case["kind"] == "identical_control":
                    row["control_pass"] = (better == "tie" and all(verdict[metric]["A"] == verdict[metric]["B"]
                        for metric in ("grounded", "resonance", "clarity"))
                        and all(verdict["overclaim"][side] is False for side in ("A", "B")))
                elif case["kind"] == "subtle_grounding_control":
                    row["control_pass"] = (scores["grounded"]["good"] > scores["grounded"]["bad"]
                        and all(scores["overclaim"][side] is False for side in ("good", "bad")))
                    row["preferred_grounded_text"] = row["normalized_winner"] == "good"
            state.put("qualification_result", scope,
                      {**{key: value for key, value in row.items() if key != "kind"},
                       "case_kind": row["kind"]})
            print(json.dumps({"model": model, "case": case["id"], "order": order,
                              "valid": valid, "finish": row["finish_reason"],
                              "winner": row.get("normalized_winner"), "control_pass": row.get("control_pass")}), flush=True)
            return row

        # Interleave judges and cases; these are independently dispatched even for identical controls.
        jobs = [(model, case, order) for case in protocol["cases"] for order in (0, 1) for model in protocol["judges"]]
        rows = []
        skipped = []
        if settings.get("gate_on_first_case"):
            rows.append(run(jobs[0]))
            if not rows[0]["valid"]:
                skipped = jobs[1:]
            jobs = [] if skipped else jobs[1:]
        with ThreadPoolExecutor(max_workers=6) as executor:
            rows += [future.result() for future in as_completed([executor.submit(run, job) for job in jobs])]
        rows.sort(key=lambda row: (row["model"], row["case"], row["order"]))
        attempts = [attempt for row in rows for attempt in row["attempts"] if attempt.get("dispatched")]
        totals = {"requests": len(attempts), "retries": sum(attempt.get("attempt", 1) > 1 for attempt in attempts),
                  "http_status_counts": dict(Counter(str(attempt.get("http_status")) for attempt in attempts)),
                  "usage": {field: sum(attempt.get("usage", {}).get(field, 0) for attempt in attempts)
                            for field in ("prompt_tokens", "completion_tokens", "total_tokens")},
                  "reasoning_tokens": sum(attempt.get("usage", {}).get("completion_tokens_details", {}).get("reasoning_tokens", 0)
                                          for attempt in attempts)}
        report = {"schema": "norn.judge-qualification.results.v1",
                  "created_at": datetime.now(timezone.utc).isoformat(), "wall_seconds": round(time.monotonic() - started, 3),
                  "protocol": "protocol.json", "transport_this_invocation": transport.snapshot(),
                  "observed_attempt_totals": totals, "resumed": resume, "rows": rows,
                  "unexecuted_jobs": [{"model": model, "case": case["id"], "order": order}
                                      for model, case, order in skipped],
                  "summary": summarize(rows, protocol), "cost_usd": None}
        write_new(output, report)
        compact = {model: {key: value for key, value in summary.items() if key != "natural_pairs"}
                   for model, summary in report["summary"].items()}
        print(json.dumps({"summary": compact, "observed_attempt_totals": totals,
                          "transport_this_invocation": report["transport_this_invocation"]}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    protocol_path = HERE / "protocol.json"
    protocol = json.loads(protocol_path.read_text()) if protocol_path.exists() else prepare()
    if not args.live:
        print(json.dumps({"protocol": str(protocol_path), "planned_requests": 24, "live": False}))
        return
    if not args.key_file:
        parser.error("--live requires an explicit --key-file")
    key = json.loads(args.key_file.read_text())["api_key"]
    live(protocol, key, args.resume)


if __name__ == "__main__":
    main()
