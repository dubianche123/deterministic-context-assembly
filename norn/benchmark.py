"""Inspectable context/session experiments; offline by default, no implicit route.

The shipped experiment validates context structure, not mock prose quality.
Cold/warm identify repeat positions only; cache accounting comes from providers.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from .core import engine_module
from .events import canonical_json, content_hash
from .profile import export_replay

REPORT_VERSION = "norn.benchmark.v1"
EXPERIMENT_VERSION = "norn.context-session.v1"
QUESTIONS = ("你为什么这样理解我的选择？", "这次撤回和重新选择说明了什么？", "下一步我可以做什么？")
ARMS = ("raw_single", "bounded_single", "legacy_summary_session", "frozen_native_session")
# Frozen April/0.4 baseline. Keep it independent from the current production
# template so later prompt edits cannot silently change the control arm.
LEGACY_SYSTEM = (
    "你是「命运织机」在结果页留下的最后回声。\n\n规则：\n"
    "- 严格遵守当前轮次上限；如果轮数已满，必须用宿命、星象、玄学、线头收束之类的意象礼貌结束，不再延长对话。\n"
    "- 即使当前轮次是 1/1 或最后一轮，也必须先回应玩家这句话，再自然收束。\n"
    "- 不要用「没有回应」「无回应」「嘶鸣」「沉默」这类不回答式文本。\n"
    "- 每次回复都要简短，中文 40-120 字，最多两段，不要列表。\n- 用第二人称「你」。\n"
    "- 可以回应、安抚、轻微点醒，但最后一轮不要继续抛出新问题。\n"
    "- 语气保持神秘、克制、带一点温度。\n"
    "- 尽量根据上一段结论和历史对话给出具体回应，不要泛泛而谈。")
LEGACY_EXAMPLES = (
    ("分析背景:\n结果: INFJ；模式: sparse_mode；上一段结论: 你一直在确认某种尚未被说出的东西。\n\n历史对话:\n（空）\n\n当前轮次: 1/1\n玩家输入: 我是不是太敏感了？\n\n收束提示:\n一轮已满，织机不再继续回声。\n\n要求: 只给出当前轮次的回应；如果已经到最后一轮，就自然收束，不要继续抛出新问题。",
     "你不是太敏感，你只是比别人更早听见了空气里的波纹。\n\n这根线到这里先收住；剩下的，不必急着证明给任何人看。"),
    ("分析背景:\n结果: ENTP；模式: dense_mode；上一段结论: 你习惯把僵局拆开，直到它露出新的入口。\n\n历史对话:\n玩家: 那我到底该继续吗？\n织机: 继续不是答案，真正的答案是你是否还在变得更清醒。\n\n当前轮次: 2/2\n玩家输入: 可是我会不会只是逃避？\n\n收束提示:\n两轮已满，织机不再继续回声。\n\n要求: 只给出当前轮次的回应；如果已经到最后一轮，就自然收束，不要继续抛出新问题。",
     "逃避会让人变轻，而你这句话反而很重。它说明你还在看自己，不只是看出口。\n\n星位到此合上，答案留给下一次行动。"),
)


def default_journal():
    """A fixed 24-round recording, with real revision and duration evidence."""
    events = []

    def add(kind, **fields):
        sequence = len(events) + 1
        events.append({"event_id": f"bench-{sequence:03d}", "sequence": sequence,
                       "type": kind, **fields})

    for rnd in range(1, 25):
        card = f"NORN_{rnd:04d}"
        add("round_started", round=rnd,
            card_ids=[card, f"NORN_{rnd + 24:04d}", f"NORN_{rnd + 48:04d}"])
        add("card_selected", round=rnd, card_id=card)
        if rnd % 4 == 0:
            add("card_deselected", round=rnd, card_id=card)
            add("card_selected", round=rnd, card_id=card)
        add("round_completed", round=rnd, duration_ms=2500 + rnd * 1300)
    add("session_completed")
    return {"schema_version": "norn.events.v1", "events": events}


def _text(prompt):
    """Logical order only; this is not an HTTP body or tokenizer trace."""
    return str(prompt.get("system", "")) + "\n" + "\n".join(
        str(message.get("role", "")) + ":" + str(message.get("content", ""))
        for message in prompt.get("messages", []))


def _size(prompt):
    encoded = canonical_json(prompt).encode("utf-8")
    return {"serialized_utf8_bytes": len(encoded),
            "estimated_input_tokens": math.ceil(len(encoded) / 3),
            "estimate_method": "ceil(canonical JSON UTF-8 bytes / 3); not provider tokens"}


def _raw_prompt(bounded, journal):
    # Hold the task/instructions/frozen profile constant; measure raw-evidence
    # overhead rather than silently changing what the model is asked to do.
    prompt = deepcopy(bounded)
    cards = engine_module("fast_thinker")._load_cards()
    displayed = {card for event in journal["events"] for card in event.get("card_ids", [])}
    evidence = {"journal": journal, "displayed_card_metadata":
                {key: cards[key] for key in sorted(displayed)}}
    prompt["messages"][-1]["content"] += ("\n\nRAW_EVIDENCE (same task; redundant full event and card evidence):\n"
                                              + canonical_json(evidence))
    return prompt


def _legacy_prompt(profile, reading, history, message, turn):
    """Freeze the pre-0.5 summary baseline, including its 180-character cut."""
    analysis = profile["analysis"]
    # Scoring v4 has no four-axis type; the frozen baseline then says so.
    summary = f"结果: {analysis.get('mbti_type') or '未计算'}；模式: {analysis['data_density']}；上一段结论: "
    summary += reading.strip().replace("\n", " ")[:180]
    lines = []
    for item in history[-4:]:
        prefix = "玩家" if item["role"] == "user" else "织机"
        lines.append(prefix + ": " + item["content"])
    history_text = "\n".join(lines) or "（空）"
    content = (f"分析背景:\n{summary}\n\n历史对话:\n{history_text}\n\n当前轮次: {turn}/3\n玩家输入: {message}"
               "\n\n收束提示:\n三轮已满，织机不再继续回声。\n\n剩下的事，只能交给时间和你自己的直觉。"
               "\n\n要求: 必须先回应玩家当前这句话；只给出当前轮次的回应；如果已经到最后一轮，就用一两句宿命式语气自然收束，不要继续抛出新问题。")
    messages = []
    for user, assistant in LEGACY_EXAMPLES:
        messages.extend([{"role": "user", "content": user},
                         {"role": "assistant", "content": assistant}])
    messages.append({"role": "user", "content": content})
    return {"system": LEGACY_SYSTEM, "messages": messages,
            "max_tokens": 260, "temperature": 0.7,
            "prompt_version": "norn.prompt.legacy-dialogue.v1"}


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _cache_counts(usage):
    read = _count(usage.get("cache_read_input_tokens"))
    if read is None:
        read = _count(usage.get("cached_tokens"))
    write = _count(usage.get("cache_creation_input_tokens"))
    if write is None:
        write = _count(usage.get("cache_write_input_tokens"))
    return read, write


def _prices(snapshot, provider, model):
    if snapshot is None:
        return None
    if not isinstance(snapshot, dict) or snapshot.get("provider") != provider or snapshot.get("model") != model:
        raise ValueError("Price snapshot must match the explicitly selected provider and model")
    if not isinstance(snapshot.get("as_of"), str) or not snapshot["as_of"]:
        raise ValueError("Price snapshot requires an explicit as_of date")
    rates = snapshot.get("usd_per_million_tokens")
    names = ("input", "output", "cache_read", "cache_write")
    if not isinstance(rates, dict) or set(rates) != set(names):
        raise ValueError("Price snapshot requires input/output/cache_read/cache_write rates")
    if any(type(rates[name]) not in (int, float) or not math.isfinite(rates[name])
           or rates[name] < 0 for name in names):
        raise ValueError("Price rates must be finite nonnegative numbers")
    return deepcopy(snapshot)


def _cost(usage, prices):
    if prices is None:
        return None
    inp, out = _count(usage.get("input_tokens")), _count(usage.get("output_tokens"))
    read, write = _cache_counts(usage)
    # Missing cache counters are unknown, not free tokens. Providers normalize
    # input_tokens to total input, retaining their original response in raw.
    if None in (inp, out, read, write) or read + write > inp:
        return None
    rates = prices["usd_per_million_tokens"]
    return ((inp - read - write) * rates["input"] + out * rates["output"]
            + read * rates["cache_read"] + write * rates["cache_write"]) / 1_000_000


def _prefix_stats(records):
    result = []
    for arm in ARMS:
        for turn in range(4 if arm.endswith("session") else 1):
            prompts = [item["prompt"] for item in records if item["arm"] == arm and item["turn"] == turn]
            if len(prompts) < 2:
                continue
            strings = [_text(prompt).encode("utf-8") for prompt in prompts]
            first = strings[0]
            common = len(first)
            for value in strings[1:]:
                common = min(common, len(value))
                index = 0
                while index < common and first[index] == value[index]:
                    index += 1
                common = index
            result.append({"arm": arm, "turn": turn, "samples": len(strings),
                           "common_logical_prefix_utf8_bytes": common,
                           "prefix_sha256": hashlib.sha256(first[:common]).hexdigest(),
                           "identical_prompt_hashes": len({content_hash(p) for p in prompts}) == 1})
    return result


def run_benchmark(*, fixture=None, repeats=2, provider="mock", live=False,
                  max_calls=None, max_cost_usd=None, price_snapshot=None,
                  config=None, generate=None, stop_after_failures=None):
    """Return a complete report. Only explicit live+provider+call cap can go online.

    ``generate`` is the existing provider boundary, injectable for offline tests.
    There are no automatic retries. A failed session terminates that arm's run.
    A monetary threshold checks observed spend between calls; it is not a hard
    currency cap. Use provider-side limits for a hard monetary stop.
    """
    from .context import build_session_context, build_analysis_prompt, build_dialogue_prompt
    from .providers import generate as default_generate, provider_config
    if type(repeats) is not int or not 2 <= repeats <= 20:
        raise ValueError("repeats must be between 2 and 20 (first/cold, later/warm)")
    if not live and provider != "mock":
        raise ValueError("A real provider requires explicit --live and --max-calls")
    if live and (provider == "mock" or type(max_calls) is not int or not 1 <= max_calls <= 1000):
        raise ValueError("Live mode requires an explicit real provider and max_calls between 1 and 1000")
    if not live and max_calls is not None:
        raise ValueError("max_calls applies to explicit live runs only")
    if stop_after_failures is not None and (type(stop_after_failures) is not int or not 1 <= stop_after_failures <= 1000):
        raise ValueError("stop_after_failures must be a positive integer no greater than 1000")
    settings = provider_config(provider, config)
    model = settings.get("model", settings.get("model_id")) if live else None
    prices = _prices(price_snapshot, provider, model)
    if max_cost_usd is not None:
        if not live or prices is None or type(max_cost_usd) not in (int, float) or not math.isfinite(max_cost_usd) or max_cost_usd <= 0:
            raise ValueError("max_cost_usd requires live mode, matching explicit prices, and a positive finite value")
    journal = deepcopy(fixture) if fixture is not None else default_journal()
    if isinstance(journal, dict) and journal.get("schema_version") == "norn.replay.v1":
        from .profile import replay_bundle
        replay_bundle(journal)
        journal = journal.get("journal")
    bundle = export_replay(journal)
    profile = bundle["profile"]
    if profile["analysis"]["total_rounds"] <= 20:
        raise ValueError("Session benchmark needs at least 21 rounds to compare three authorized follow-ups")
    if not profile["analysis"]["total_selections"]:
        raise ValueError("Context benchmark requires nonempty selections; the product's empty result makes no analysis model call")
    context = build_session_context(profile)
    analysis_prompt = build_analysis_prompt(context)
    evidence = context.get("block", {}).get("evidence", {})
    source_event_ids = {event["event_id"] for event in journal["events"]}
    evidence_rows = evidence.get("selections", []) + evidence.get("revisions", [])
    structural_evidence = {
        "profile_hash_retained": context.get("profile_hash") == profile["profile_hash"],
        "selection_budget": evidence.get("selection_limit"),
        "selections_sent": len(evidence.get("selections", [])),
        "revision_budget": evidence.get("revision_limit"),
        "revisions_sent": len(evidence.get("revisions", [])),
        "selection_budget_respected": len(evidence.get("selections", [])) <= evidence.get("selection_limit", 0),
        "revision_budget_respected": len(evidence.get("revisions", [])) <= evidence.get("revision_limit", 0),
        "evidence_has_source_events": bool(evidence_rows) and all(row.get("event_id") in source_event_ids for row in evidence_rows),
        "bounded_evidence": deepcopy(evidence),
    }
    render = generate or default_generate
    records = []
    calls = 0
    observed_cost = 0.0
    known_costs = True
    stopped = None
    consecutive_failures = 0
    source_hash = content_hash(profile)
    for arm in ARMS:
        for run in range(repeats):
            history = []
            reading = ""
            for turn in range(4 if arm.endswith("session") else 1):
                if live and calls >= max_calls:
                    stopped = "max_calls"
                    break
                if live and max_cost_usd is not None and (not known_costs or observed_cost >= max_cost_usd):
                    stopped = "usage_unavailable" if not known_costs else "observed_cost_threshold"
                    break
                message = QUESTIONS[turn - 1] if turn else ""
                if not turn:
                    prompt = _raw_prompt(analysis_prompt, journal) if arm == "raw_single" else deepcopy(analysis_prompt)
                elif arm == "legacy_summary_session":
                    prompt = _legacy_prompt(profile, reading, history, message, turn)
                else:
                    prompt = build_dialogue_prompt(context, deepcopy(history), message, turn, 3)
                before = content_hash(profile)
                prompt_snapshot = deepcopy(prompt)
                native_pairs = sum(item.get("role") == "assistant" for item in prompt["messages"])
                started = time.perf_counter()
                # Count dispatches before invocation, including failures.
                calls += int(live)
                try:
                    result = render(provider, deepcopy(prompt), task="dialogue" if turn else "analysis",
                                    analysis=deepcopy(profile["analysis"]), user_message=message,
                                    turn_count=turn or 1, max_turns=3, config=deepcopy(settings))
                    if not isinstance(result, dict):
                        raise ValueError("Invalid generation result")
                except Exception:
                    # Endpoint/config/credential-bearing exceptions never enter artifacts.
                    result = {"status": "generation_exception", "text": "", "usage": {},
                              "provider_calls": int(live), "simulated": not live}
                elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                usage = deepcopy(result.get("usage")) if isinstance(result.get("usage"), dict) else {}
                cost = _cost(usage, prices) if live else None
                if live:
                    known_costs = known_costs and cost is not None
                    if cost is not None:
                        observed_cost += cost
                cache_read, cache_write = _cache_counts(usage)
                record = {"arm": arm, "run_index": run, "phase": "cold" if run == 0 else "warm",
                          "turn": turn, "task": "analysis" if not turn else "dialogue",
                          "provider": provider, "model": result.get("model") or model,
                          "requested_model": model, "provider_request_id": result.get("request_id"),
                          "simulated": not live,
                          "status": result.get("status", "unknown"), "retry_count": 0,
                          "output_status": result.get("output_status", result.get("status", "unknown")),
                          "reason_codes": deepcopy(result.get("reason_codes", [])),
                          "cache_metadata": deepcopy(result.get("cache")),
                          "latency_ms": elapsed_ms, "provider_calls": int(live),
                          "provider_elapsed_ms": result.get("elapsed_ms"),
                          "prompt_hash": content_hash(prompt_snapshot), "prompt": prompt_snapshot,
                          "profile_hash": profile["profile_hash"],
                          "profile_version": profile["schema_version"],
                          "algorithm_version": profile["algorithm_version"],
                          "metadata_version": profile["metadata_version"],
                          "context_version": context.get("schema_version"),
                          "context_hash": context.get("context_hash"),
                          "prompt_version": prompt.get("prompt_version", context.get("prompt_version")),
                          "request_size": _size(prompt_snapshot), "usage": usage,
                          "raw_usage": deepcopy(usage.get("raw")),
                          "cache_read_input_tokens": cache_read,
                          "cache_creation_input_tokens": cache_write,
                          "cost_usd": cost, "output": result.get("text", ""),
                          "structural_checks": {
                              "profile_unchanged": before == content_hash(profile) == source_hash,
                              "frozen_profile_hash_sent": profile["profile_hash"] in _text(prompt_snapshot),
                              "native_assistant_messages": native_pairs,
                              "history_messages_expected": len(history),
                              "history_retained_as_native_prefix": (prompt_snapshot["messages"][:len(history)] == history) if turn else None,
                          }}
                records.append(record)
                if record["status"] != "ok":
                    consecutive_failures += 1
                    if stop_after_failures is not None and consecutive_failures >= stop_after_failures:
                        stopped = "consecutive_failures"
                    break
                consecutive_failures = 0
                if not turn:
                    reading = result.get("text", "")
                    history = ([] if arm == "legacy_summary_session" else
                               deepcopy(prompt_snapshot["messages"]) + [{"role": "assistant", "content": reading}])
                else:
                    if arm == "legacy_summary_session":
                        history.extend([{"role": "user", "content": message},
                                        {"role": "assistant", "content": result.get("text", "")}])
                    else:
                        history = deepcopy(prompt_snapshot["messages"]) + [
                            {"role": "assistant", "content": result.get("text", "")}]
            if stopped:
                break
        if stopped:
            break
    summaries = []
    for arm in ARMS:
        subset = [row for row in records if row["arm"] == arm]
        summaries.append({"arm": arm, "completed_dispatches": len(subset),
                          "planned_dispatches": repeats * (4 if arm.endswith("session") else 1),
                          "serialized_input_utf8_bytes": sum(row["request_size"]["serialized_utf8_bytes"] for row in subset),
                          "estimated_input_tokens": sum(row["request_size"]["estimated_input_tokens"] for row in subset),
                          "measured_input_tokens": (sum(row["usage"]["input_tokens"] for row in subset)
                                                    if subset and all(_count(row["usage"].get("input_tokens")) is not None for row in subset) else None),
                          "cost_usd": sum(row["cost_usd"] for row in subset) if subset and all(row["cost_usd"] is not None for row in subset) else None,
                          "failures": sum(row["status"] != "ok" for row in subset)})
    observed_cache = [row for row in records if row["cache_read_input_tokens"] is not None]
    return {"schema_version": REPORT_VERSION, "experiment_version": EXPERIMENT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": "live" if live else "offline_structural", "provider": provider, "model": model,
            "fixture": bundle, "questions": list(QUESTIONS), "repeats": repeats,
            "context_checks": structural_evidence,
            "method": {"raw_single": "Same bounded task/profile plus full journal and displayed card metadata; isolates redundant raw-evidence overhead.",
                       "bounded_single": "Production context and analysis builder.",
                       "legacy_summary_session": "Production analysis then frozen pre-0.5 truncated reading/flattened-history baseline, three follow-ups.",
                       "frozen_native_session": "Production frozen context and native role history, analysis plus three follow-ups.",
                       "phase": "First repeat is labeled cold, later repeats warm; no provider cache is reset or asserted.",
                       "quality": "Structural evidence/profile/history retention only; prose correctness and model quality are not graded.",
                       "latency": "Local boundary elapsed time; offline latency is not inference performance.",
                       "cost_threshold": "Observed spend is checked between calls, may overshoot by one call; provider-side controls are needed for a hard currency cap."},
            "limits": {"max_calls": max_calls, "max_cost_usd": max_cost_usd,
                       "stop_after_failures": stop_after_failures,
                       "actual_provider_calls": calls, "automatic_retries": 0, "stopped": stopped},
            "price_snapshot": prices,
            "total_cost_usd": observed_cost if live and known_costs else None,
            "cache_observation": {"status": "provider_observed" if observed_cache else "unavailable",
                                  "observed_calls": len(observed_cache),
                                  "cache_read_input_tokens": sum(row["cache_read_input_tokens"] for row in observed_cache) if observed_cache else None,
                                  "warning": "Shared logical prefixes and repeat positions do not prove cache hits."},
            "shared_prefixes": _prefix_stats(records), "arms": summaries, "calls": records}


def add_benchmark_arguments(parser):
    parser.add_argument("--fixture", type=Path, help="Completed event journal or replay JSON; defaults to fixed 24-round fixture")
    parser.add_argument("--repeats", type=int, default=2, help="2–20: first labeled cold, later warm")
    parser.add_argument("--live", action="store_true", help="Authorize the explicitly selected real provider")
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--max-calls", type=int, help="Required hard dispatch limit for live mode")
    parser.add_argument("--max-cost-usd", type=float, help="Observed-spend stop between calls; requires explicit prices")
    parser.add_argument("--price-snapshot", type=Path, help="Explicit provider/model/date and per-million input/output/cache_read/cache_write USD")
    parser.add_argument("--output", type=Path, help="Write inspectable JSON to a new file (refuses overwrite)")
    return parser


def _read_json(path):
    from .server import MAX_REQUEST_BYTES, _no_duplicates, _no_constant
    with path.open("rb") as handle:
        raw = handle.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Benchmark input exceeds the 8 MiB limit")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_constant)


def benchmark_main(args):
    written = False
    try:
        # Reserve destination before any paid calls; an existing artifact must
        # fail without running an experiment whose report cannot be saved.
        handle = args.output.open("x", encoding="utf-8") if args.output else None
    except OSError as exc:
        print(f"Benchmark output failed: {exc}", file=sys.stderr)
        return 1
    try:
        report = run_benchmark(fixture=_read_json(args.fixture) if args.fixture else None,
                               repeats=args.repeats, provider=args.provider, live=args.live,
                               max_calls=args.max_calls, max_cost_usd=args.max_cost_usd,
                               price_snapshot=_read_json(args.price_snapshot) if args.price_snapshot else None)
        encoded = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        if handle:
            handle.write(encoded)
            written = True
        else:
            sys.stdout.write(encoded)
        return 0
    except (OSError, ValueError, RecursionError) as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if handle:
            handle.close()
            if not written:
                args.output.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_benchmark_arguments(parser)
    return benchmark_main(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
