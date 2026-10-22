"""Real-model A/B of reading prompts: same sessions, two projections, blinded judge.

Each simulated session is read through the production service (prompt build,
provider call, output guardrails) once per prompt version and model, followed
by up to two fixed follow-up questions. Every output gets rule checks; the two
readings of the same session are then compared by a judge model from another
family, in both orders, so position bias cancels.

    python3 scripts/prose_eval.py --base-url http://127.0.0.1:17863/v1 \\
        --model cn:glm-5.3-flash --model cn:deepseek-v4.1-flash \\
        --judge cn:mimo-v2.6-pro \\
        --workers 6 --max-in-flight 6 --key-file PATH_TO_JSON_WITH_api_key \\
        --output benchmarks/results/prose-eval-v3.json

The key comes from --key-file or NORN_API_KEY and never enters the report.
Sessions are synthetic; no visitor data is sent anywhere.
The scoring contract stays at v4 for both prompt arms, independently of the
application's current default; v4/v5 here name the prose prompt versions.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from norn.context import PROMPT_V4 as PROMPT_VERSION, PROMPT_V5, build_session_context  # noqa: E402
from norn.core import engine_module  # noqa: E402
from norn.profile import build_event_profile  # noqa: E402
from norn.service import NornService  # noqa: E402
from norn.simulate import simulate_records  # noqa: E402
from norn.providers import (UNAVAILABLE_TEXT, INVALID_TEXT, _usage, _cache_metadata,
                            compatible_payload, compatible_result)  # noqa: E402
from norn.cloud_provider import DEEPSEEK_MODEL, DEEPSEEK_URL, prepare_cloud_request  # noqa: E402
from scripts.eval_transport import EvalTransport, safe_usage  # noqa: E402
from scripts.eval_state import (RunState, RunStateError, UncertainRequestError,
                                RequestNotDispatchedError)  # noqa: E402

FIXTURES = ((4, 5, 11), (4, 5, 12), (10, 14, 21), (10, 14, 22), (22, 24, 31), (22, 24, 32))
# Prompt v4/v5 were compared on the same scoring-v4 evidence. Keep that
# historical experiment independent of the application's current scorer.
PROSE_SCORING_VERSION = "norn.scoring.v4"
QUESTIONS = ("为什么说我会被这些画面吸引？", "这准吗？还是只是随便说说。")
LEAKS = {"type_letters": r"(?<![A-Za-z])[EI][NS][TF][JP](?![A-Za-z])", "mbti": r"MBTI|人格类型",
         "jargon": r"维度|像素|通道|饱和度|边缘|概率|模型|分数|排序|留出|基线|算法|权重|log",
         "digits": r"[0-9]"}
JUDGE_SYSTEM = """你是严格的中文文本评审。比较同一次图片选择得到的两段解读 A 和 B。
「事实」列出生成解读时系统提供的全部材料：真实的轮数、每张最终留下的画面（轮次、场景、画面元素、气质词）、系统算出的视觉统计、按选择推断的画面元素、推荐画面及其元素、线索清楚程度。引用这些都不算编造；事实里没有的具体画面、元素或经历才算编造。视觉统计是技术描述，引用它不算编造，但会让文字不够清楚。
解读不得作人格诊断，不应出现技术术语或数字指标。
评分 1-5：grounded（具体、只引用事实且不编造）、resonance（读来有温度、像在说我）、clarity（清楚自然、没有术语）。
overclaim：是否把有限线索说成确定的性格或命运。better 必须与你的评分和 overclaim 一致。只输出一个 JSON 对象，不要其他文字：
{"better":"A"|"B"|"tie","grounded":{"A":n,"B":n},"resonance":{"A":n,"B":n},"clarity":{"A":n,"B":n},"overclaim":{"A":bool,"B":bool},"reason":"一句话"}
JSON 字符串中的英文双引号必须正确转义；reason 如需引用文字，请用中文「」引号，避免在字符串内部使用英文双引号。仍只输出一个合法 JSON 对象。"""
SCORES = ("grounded", "resonance", "clarity")
# Qualified for assisted review only (benchmarks/results/judge-qualification-20261021.md):
# a single default judge, no automatic fallback to a judge that did not qualify.
DEFAULT_JUDGES = ("cn:mimo-v2.6-pro",)


def digest(value):
    wire = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(wire.encode()).hexdigest()


def _progress(kind, **fields):
    print(json.dumps({"event": kind, **fields}, ensure_ascii=False), file=sys.stderr, flush=True)


class EvaluationPaused(BaseException):
    """Stop orchestration without the application's renderer swallowing it."""


def _pause_if_unstarted(args, attempts):
    if attempts and not any(row.get("dispatched") for row in attempts) and any(
            row.get("error") in ("budget_exhausted", "cancelled") for row in attempts):
        args.cancelled.set()
        raise RequestNotDispatchedError("No request was dispatched")


def _unavailable(reason="provider_error"):
    return {"text": UNAVAILABLE_TEXT, "usage": _usage(), "blocked": False,
            "status": "provider_unavailable", "output_status": "provider_unavailable",
            "reason_codes": [reason]}


def _identifier(value, key):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value) and not (key and key in value) else None


def session_generator(args, key, job_key, observations):
    """Inject only the offline transport; keep production wire mapping and guards."""
    def generate(provider, prompt, *, config, **kwargs):
        if args.cancelled.is_set():
            raise KeyboardInterrupt()
        if provider != "openai-compatible":
            raise ValueError("The evaluation requires the explicit compatible route")
        if args.wire == "production":
            # Byte-for-byte the Worker's request (thinking disabled, same output cap);
            # only the model name changes so the local endpoint can route it.
            payload = {**json.loads(prepare_cloud_request(prompt)), "model": config["model"]}
        else:
            payload = compatible_payload(prompt, config)
        cache_key = digest({"job": job_key, "task": kwargs.get("task"), "payload": payload})
        reused = args.state.get("generation", cache_key) is not None

        def dispatch():
            started = time.monotonic()
            raw, attempts = args.transport.complete(payload, timeout=min(args.timeout, 120))
            _pause_if_unstarted(args, attempts)
            if raw is None:
                reason = "budget_exhausted" if any(row.get("error") == "budget_exhausted" for row in attempts) else "provider_error"
                result = _unavailable(reason)
            else:
                raw = deepcopy(raw)
                raw["usage"] = safe_usage(raw.get("usage"))
                raw["model"] = _identifier(raw.get("model"), key) or config["model"]
                raw["id"] = _identifier(raw.get("id"), key)
                result = compatible_result(raw, None, config)
                if key and key in json.dumps(result, ensure_ascii=False):
                    result = {**_unavailable("credential_echo"), "text": INVALID_TEXT,
                              "status": "invalid_output", "output_status": "invalid_output", "blocked": True}
            result.update(model=result.get("model", config["model"]), provider=provider, simulated=False,
                          provider_calls=sum(row.get("dispatched") is True for row in attempts),
                          elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                          cache=_cache_metadata(provider, prompt, config))
            return {"result": result, "attempts": attempts}

        try:
            saved = args.state.execute("generation", cache_key, dispatch)
        except RequestNotDispatchedError:
            raise EvaluationPaused()
        except UncertainRequestError:
            saved = {"result": {**_unavailable("uncertain_previous_attempt"), "provider_calls": 0}, "attempts": []}
        observations.append({"task": kwargs.get("task"), "reused": reused, "attempts": saved["attempts"]})
        return deepcopy(saved["result"])
    return generate


def vocabulary():
    cards = engine_module("fast_thinker")._load_cards()
    words = set()
    for card in cards.values():
        words.update([card.get("scene", ""), *card.get("motifs", []), *card.get("traits", [])])
    return {word for word in words if word}


def checks(text, block, known):
    style = re.search(r"(\d+)至(\d+)字", str(block.get("style", "")))
    chars = len(re.findall(r"[一-鿿]", text))
    scenes = {row["scene"] for row in block.get("evidence", {}).get("selections", []) if row.get("scene")}
    preference = block.get("preference", {})
    motifs = {row["motif"] for row in preference.get("drawn_to", [])}
    next_scenes = {row["scene"] for row in preference.get("next_to_try", [])}
    quoted = re.findall(r"[「“\"]([^」”\"]{2,20})[」”\"]", text)
    return {
        "chars": chars,
        "length_ok": bool(style and int(style.group(1)) * 0.85 <= chars <= int(style.group(2)) * 1.15),
        "leaks": {name: len(re.findall(pattern, text)) for name, pattern in LEAKS.items()},
        "scenes_cited": sum(scene in text for scene in scenes),
        "motifs_cited": sum(motif in text for motif in motifs),
        "next_to_try_cited": any(scene in text for scene in next_scenes),
        "unknown_quotes": [phrase for phrase in quoted if not any(phrase in word or word in phrase for word in known)],
    }


def run_session(args, key, model, version, fixture_index, journal, known):
    job_key = digest({"model": model, "version": version, "fixture": fixture_index, "journal": journal})
    observations = []
    config = {"base_url": args.base_url, "model": model, "api_key": key, "timeout": min(args.timeout, 120)}
    if args.wire == "compatible":
        config["reasoning_effort"] = "off"  # a proxy hint; DeepSeek routes may raise it to low
    service = NornService("openai-compatible", config,
                          generate=session_generator(args, key, job_key, observations), max_model_calls=100,
                          algorithm_version=PROSE_SCORING_VERSION)
    service.prompt_version = version
    started = time.monotonic()
    result = service.analyze({"journal": journal})
    session = service.sessions.get(result["session_id"]) if result.get("session_id") else None
    block = session.context["block"] if session else {}
    turns = [{"role": "reading", "text": result["reading"], "status": result["status"],
              "reason_codes": result.get("reason_codes", []), "elapsed_ms": result.get("elapsed_ms"),
              "usage": result.get("usage"), "checks": checks(result["reading"], block, known)}]
    for question in QUESTIONS[:result.get("max_dialogue_turns") or 0] if session else ():
        reply = service.dialogue({"session_id": result["session_id"], "user_message": question})
        turns.append({"role": "follow_up", "question": question, "text": reply["reply"],
                      "status": reply["status"], "reason_codes": reply.get("reason_codes", []),
                      "elapsed_ms": reply.get("elapsed_ms"), "usage": reply.get("usage"),
                      "checks": checks(reply["reply"], block, known)})
    return {"model": model, "prompt_version": version, "context_prompt_version": block.get("prompt_version"),
            "fixture": fixture_index, "mode": block.get("mode"), "rounds": block.get("total_rounds"),
            "facts": sorted({row["scene"] for row in block.get("evidence", {}).get("selections", [])}),
            "turns": turns, "wire_requests": observations, "wall_seconds": round(time.monotonic() - started, 2)}


def facts_for(journal, *, algorithm_version=PROSE_SCORING_VERSION):
    """Everything either prompt version handed the writer, labelled by source.

    A judge that only sees the picks calls v4's visual statistics and v5's
    motif counts "fabricated"; both came from the system, so both are listed.
    The default explicitly preserves the saved experiment's scoring-v4
    profile; prompt versions and scoring versions are separate contracts.
    """
    cards = engine_module("fast_thinker")._load_cards()
    rows = engine_module("choice_model").observations(journal)
    picks = [(row["round"], card) for row in rows for card in row["picks"]]
    lines = [f"共 {len(rows)} 轮，最终留下 {len(picks)} 张："]
    lines += [f"第{number}轮 {cards[card]['scene']}（元素：{'、'.join(cards[card]['motifs'])}；"
              f"气质：{'、'.join(cards[card]['traits'])}）" for number, card in picks]
    profile = build_event_profile(journal, algorithm_version=algorithm_version)
    old = build_session_context(profile, PROMPT_VERSION)["block"].get("preference", {})
    new = build_session_context(profile, PROMPT_V5)["block"].get("preference", {})
    statistics_ = [f"{row['description']}（{row['association']}）" for row in old.get("top_features", [])]
    if statistics_:
        lines.append("系统算出的视觉统计（技术描述）：" + "；".join(statistics_))
    validation = old.get("validation") or {}
    if validation.get("status"):
        lines.append("系统的留出验证：" + ("优于随机" if validation.get("beats_random") else "未优于随机或样本不足"))
    if new.get("drawn_to"):
        lines.append("系统按选择推断的画面元素：" + "、".join(
            f"{row['motif']}（留下的画面里有{row['picked']}张含此元素）" for row in new["drawn_to"]))
    if new.get("next_to_try"):
        lines.append("系统推荐、本次没有选过的画面：" + "、".join(
            f"{row['scene']}（元素：{'、'.join(row['motifs'])}）" for row in new["next_to_try"]))
    if new.get("evidence_clarity"):
        lines.append("线索清楚程度：" + new["evidence_clarity"])
    return "\n".join(lines)


def judge(args, key, facts, first, second, *, scope, judges=None):
    """First judge in order that answers; the verdict names which one did.

    Fallback covers transport, truncation and malformed JSON only. A verdict
    that contradicts its own scores abstains instead of asking another judge.
    """
    prompt = f"事实：\n{facts}\n\n解读 A：\n{first}\n\n解读 B：\n{second}"
    calls = []
    for model in judges or args.judge:
        if args.cancelled.is_set():
            raise KeyboardInterrupt()
        # Judges available here think before answering; a small budget ends in empty, truncated output.
        messages = [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": prompt}]
        request_key = digest({"scope": scope, "model": model, "messages": messages,
                              "reasoning": args.judge_reasoning, "max_tokens": args.judge_max_tokens})
        reused = args.state.get("judge_request", request_key) is not None

        def dispatch():
            text, finish, log = args.transport.chat(model, messages, reasoning=args.judge_reasoning,
                                                    max_tokens=args.judge_max_tokens, timeout=args.timeout)
            _pause_if_unstarted(args, log)
            if text is not None and key and key in text:
                return {"text": None, "finish": None, "attempts": log, "rejected": "credential_echo"}
            return {"text": text, "finish": finish, "attempts": log}

        try:
            saved = args.state.execute("judge_request", request_key, dispatch)
        except RequestNotDispatchedError:
            raise EvaluationPaused()
        except UncertainRequestError:
            return {"better": None, "calls": calls + [{"judge": model, "rejected": "uncertain_previous_attempt"}]}
        text, finish, log = saved["text"], saved["finish"], saved["attempts"]
        calls.append({"judge": model, "reasoning_effort": args.judge_reasoning,
                      "max_tokens": args.judge_max_tokens, "reused": reused, "attempts": log})
        if text is None:
            continue  # transport failure: try the next judge
        if finish != "stop":
            calls[-1]["rejected"] = f"finish_reason={finish}"
            continue  # a truncated verdict is not a verdict
        match = re.search(r"\{.*\}", text, re.S)
        try:
            verdict = json.loads(match.group(0)) if match else None
        except ValueError:
            verdict = None
        if not valid_verdict(verdict):
            calls[-1]["rejected"] = "unparsable verdict"
            continue
        issue = verdict_issue(verdict)
        if issue:
            return {**verdict, "judge": model, "calls": calls, "issue": issue}
        return {**verdict, "judge": model, "calls": calls}
    return {"better": None, "calls": calls, "issue": "no_valid_verdict"}


def verdict_issue(verdict):
    """None for a self-consistent verdict, else why it must abstain (never flipped).

    A winner that scores no higher than the loser on every rubric, lower on at
    least one, and does not overclaim less, contradicts the judge's own scores;
    so does a tie where one side is strictly better on every rubric.
    """
    scores = {side: [verdict[name][side] for name in SCORES] for side in ("A", "B")}
    overclaim = verdict["overclaim"]

    def dominates(high, low):
        return all(h >= l for h, l in zip(scores[high], scores[low])) and scores[high] != scores[low]

    better = verdict["better"]
    if better in ("A", "B"):
        loser = "B" if better == "A" else "A"
        excused = overclaim[loser] and not overclaim[better]
        if dominates(loser, better) and not excused:
            return "winner_dominated_by_loser"
    else:
        for side, other in (("A", "B"), ("B", "A")):
            if all(h > l for h, l in zip(scores[side], scores[other])) and not (overclaim[side] and not overclaim[other]):
                return "tie_with_strict_dominance"
    return None


def pair_outcome(rows):
    """Normalise both display orders of one pair: agreement decides, conflict abstains."""
    scores = [row.get("v5_score") for row in rows]
    if len(rows) != 2 or any(score is None for score in scores):
        return "abstain"
    if scores[0] == scores[1]:
        return {1.0: "v5", 0.0: "v4", 0.5: "tie"}[scores[0]]
    if set(scores) == {0.0, 1.0}:
        return "order_disagreement"
    return "split"  # a tie in one order, a winner in the other


def valid_verdict(value):
    if not isinstance(value, dict) or value.get("better") not in ("A", "B", "tie"):
        return False
    for name in ("grounded", "resonance", "clarity"):
        if not isinstance(value.get(name), dict) or any(type(value[name].get(side)) not in (int, float)
                or not math.isfinite(value[name][side]) or not 1 <= value[name][side] <= 5 for side in ("A", "B")):
            return False
    return (isinstance(value.get("overclaim"), dict)
            and all(type(value["overclaim"].get(side)) is bool for side in ("A", "B"))
            and isinstance(value.get("reason"), str))


def summarize(sessions, verdicts):
    rows = []
    for model in sorted({row["model"] for row in sessions}):
        for version in (PROMPT_VERSION, PROMPT_V5):
            group = [row for row in sessions if row["model"] == model and row["prompt_version"] == version]
            outputs = [turn for row in group for turn in row["turns"]]
            # Transport failures say nothing about prose; guardrail rejections do.
            transport = [turn for turn in outputs if turn["status"] == "provider_unavailable"]
            rejected = [turn for turn in outputs if turn["status"] in ("invalid_output", "policy_blocked")]
            readings = [row["turns"][0] for row in group if row["turns"][0]["status"] == "ok"]
            every = [turn for turn in outputs if turn["status"] == "ok"]
            latency = [turn["elapsed_ms"] for turn in every if turn.get("elapsed_ms") is not None]
            rows.append({
                "model": model, "prompt_version": version, "sessions": len(group),
                "outputs": len(outputs), "ok_outputs": len(every), "transport_failures": len(transport),
                "guardrail_rejections": len(rejected),
                "rejection_reasons": sorted({code for turn in rejected for code in turn.get("reason_codes", [])}),
                "reading_length_ok": sum(turn["checks"]["length_ok"] for turn in readings) / max(1, len(readings)),
                "reading_chars_median": statistics.median(turn["checks"]["chars"] for turn in readings) if readings else None,
                "leaks_per_output": {name: sum(turn["checks"]["leaks"][name] for turn in every) / max(1, len(every))
                                     for name in LEAKS},
                "scenes_cited_per_reading": statistics.fmean(turn["checks"]["scenes_cited"] for turn in readings) if readings else None,
                "unknown_quotes_per_output": sum(len(turn["checks"]["unknown_quotes"]) for turn in every) / max(1, len(every)),
                "latency_ms_median": statistics.median(latency) if latency else None,
                "latency_ms_max": max(latency) if latency else None,
            })
    judged = {}
    for model in sorted({row["model"] for row in verdicts}):
        pairs = {}
        for row in verdicts:
            if row["model"] == model:
                pairs.setdefault(row["fixture"], []).append(row)
        outcomes = {fixture: pair_outcome(rows) for fixture, rows in sorted(pairs.items())}
        decided = [fixture for fixture, outcome in outcomes.items() if outcome in ("v5", "v4", "tie")]
        # Scores come only from pairs whose two orders agree; conflicts are reported, not averaged.
        agreed = [row for fixture in decided for row in pairs[fixture]]
        scores = {name: {side: statistics.fmean(row[name][side] for row in agreed) for side in ("v4", "v5")}
                  for name in SCORES} if agreed else {}
        judged[model] = {
            "unit": "independent session pair (both display orders)",
            "pairs": len(pairs), "decided_pairs": len(decided),
            "coverage": len(decided) / len(pairs) if pairs else None,
            "outcomes": {name: sum(outcome == name for outcome in outcomes.values())
                         for name in ("v5", "v4", "tie", "order_disagreement", "split", "abstain")},
            "abstentions": {issue: sum(row.get("issue") == issue for rows in pairs.values() for row in rows)
                            for issue in sorted({row["issue"] for rows in pairs.values() for row in rows if row.get("issue")})},
            "v5_win_rate_decided": (sum({"v5": 1.0, "v4": 0.0, "tie": 0.5}[outcomes[f]] for f in decided) / len(decided)
                                    if decided else None),
            "judges": sorted({row["judge"] for rows in pairs.values() for row in rows if row.get("judge")}),
            "mean_scores_decided": scores,
            "overclaim_rate_decided": {side: statistics.fmean(float(row["overclaim"][side]) for row in agreed)
                                       for side in ("v4", "v5")} if agreed else {}}
    return {"by_arm": rows, "judge": judged}


def configuration(args):
    """Semantic inputs only: concurrency can change when a run is resumed."""
    sources = [Path(__file__), Path(__file__).with_name("eval_transport.py"),
               Path(__file__).with_name("eval_state.py")]
    root = Path(__file__).resolve().parents[1]
    sources += list((root / "norn").glob("*.py"))
    sources += list((root / "the-norn-machine/backend").glob("*.py"))
    sources += list((root / "the-norn-machine/backend/config").rglob("*.json"))
    hashes = {str(path.resolve().relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(sources)}
    return {"schema": "norn.eval-run.v1", "base_url": args.base_url.rstrip("/"),
            "models": args.model, "judges": args.judge, "prompts": [PROMPT_VERSION, PROMPT_V5],
            "scoring_version": PROSE_SCORING_VERSION,
            "fixtures": FIXTURES, "questions": QUESTIONS, "judge_reasoning": args.judge_reasoning,
            "judge_max_tokens": args.judge_max_tokens, "wire": args.wire,
            "generation_reasoning": "thinking disabled (production wire)" if args.wire == "production" else "off",
            "source_hash": digest(hashes),
            "imported_sessions_hash": hashlib.sha256(args.resume_sessions.read_bytes()).hexdigest()
            if args.resume_sessions else None}


def _write_new_json(path, value):
    """Publish a complete new artifact without ever replacing an earlier report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _imported_sessions(args):
    if not args.resume_sessions:
        return None
    rows = json.loads(args.resume_sessions.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("The session checkpoint must contain a list")
    index = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("turns"), list) or not row["turns"]:
            raise ValueError("Invalid session checkpoint")
        identity = (row.get("model"), row.get("prompt_version"), row.get("fixture"))
        if identity in index:
            raise ValueError("Duplicate session checkpoint identity")
        index[identity] = row
    return index


def parallel_map(args, fn, jobs):
    """Cancel queued work on interruption; let active requests settle durably."""
    def run(job):
        if args.cancelled.is_set():
            raise KeyboardInterrupt()
        try:
            return fn(job)
        except BaseException:
            # Set this in the worker before it can dequeue another job.
            args.cancelled.set()
            raise

    pool = ThreadPoolExecutor(args.workers)
    futures = [pool.submit(run, job) for job in jobs]
    try:
        return [future.result() for future in futures]
    except BaseException:
        args.cancelled.set()
        for future in futures:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def evaluate(args, key):
    known = vocabulary()
    journals = [simulate_records(1, visits=(1, 1), rounds=(low, high), seed=seed)[0]["journal"]
                for low, high, seed in FIXTURES]
    imported = _imported_sessions(args)
    jobs = [(model, version, index, journal) for model in args.model for version in (PROMPT_VERSION, PROMPT_V5)
            for index, journal in enumerate(journals)]

    def session_job(job):
        if args.cancelled.is_set():
            raise KeyboardInterrupt()
        model, version, index, journal = job
        cache_key = digest({"model": model, "version": version, "fixture": index, "journal": journal})
        cached = args.state.get("session", cache_key)
        if cached is not None:
            _progress("session.reused", model=model, prompt_version=version, fixture=index)
            return cached
        if imported is not None:
            identity = (model, version, index)
            if identity not in imported:
                raise ValueError("Imported checkpoint does not cover the requested models and fixtures")
            row = deepcopy(imported[identity])
        else:
            row = run_session(args, key, *job, known)
        saved = args.state.put("session", cache_key, row)
        _progress("session.saved", model=model, prompt_version=version, fixture=index,
                  status=row["turns"][0]["status"], imported=imported is not None)
        return saved

    sessions = parallel_map(args, session_job, jobs)
    checkpoint = args.output.with_name(args.output.name + ".sessions.json")
    if not checkpoint.exists():
        _write_new_json(checkpoint, sessions)
    _progress("generation.completed", sessions=len(sessions))
    pairs = []
    for model in args.model:
        for index in range(len(journals)):
            old, new = (next(row for row in sessions if row["model"] == model and row["fixture"] == index
                             and row["prompt_version"] == version) for version in (PROMPT_VERSION, PROMPT_V5))
            if old["turns"][0]["status"] == "ok" and new["turns"][0]["status"] == "ok":
                pairs.append((model, index, old, new))
    facts = [facts_for(journal) for journal in journals]

    def compare(pair):
        model, index, old, new = pair
        rows = []
        pinned = None  # both orders of one pair must come from the same judge
        for v4_side, first, second in (("A", old, new), ("B", new, old)):
            if args.cancelled.is_set():
                raise KeyboardInterrupt()
            cache_key = digest({"model": model, "fixture": index, "v4_position": v4_side,
                                "facts": facts[index], "first": first["turns"][0]["text"],
                                "second": second["turns"][0]["text"]})
            cached = args.state.get("verdict", cache_key)
            if cached is not None:
                rows.append(cached)
                pinned = pinned or cached.get("judge")
                _progress("verdict.reused", model=model, fixture=index, v4_position=v4_side)
                continue
            verdict = judge(args, key, facts[index], first["turns"][0]["text"],
                            second["turns"][0]["text"], scope=cache_key,
                            judges=[pinned] if pinned else None)
            pinned = pinned or verdict.get("judge")
            v5_side = "B" if v4_side == "A" else "A"
            better = verdict.get("better")
            score = None if better not in ("A", "B", "tie") else 0.5 if better == "tie" else float(better == v5_side)
            try:
                detail = {name: {"v4": verdict[name][v4_side], "v5": verdict[name][v5_side]}
                          for name in ("grounded", "resonance", "clarity", "overclaim")}
            except (KeyError, TypeError):
                detail, score = {}, None
            if verdict.get("issue"):
                score = None  # abstain; the raw verdict stays in the row for review
            row = {"model": model, "fixture": index, "v4_position": v4_side,
                   "v5_score": score, **detail, "reason": verdict.get("reason"),
                   "issue": verdict.get("issue"), "raw_better": better,
                   "judge": verdict.get("judge"), "calls": verdict.get("calls")}
            rows.append(args.state.put("verdict", cache_key, row))
            _progress("verdict.saved", model=model, fixture=index, v4_position=v4_side,
                      valid=score is not None, judge=verdict.get("judge"))
        return rows

    verdicts = [row for rows in parallel_map(args, compare, pairs) for row in rows]
    return {"schema_version": "norn.prose-eval.v1",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data": "synthetic sessions from norn.simulate; no visitor data",
            "scoring_version": PROSE_SCORING_VERSION,
            "base_url_host": urlsplit(args.base_url).netloc, "models": args.model, "judge": args.judge,
            "judge_reasoning_effort": args.judge_reasoning,
            "generation_reasoning_effort": ("thinking: disabled, exactly as the production Worker sends it"
                                            if args.wire == "production" else
                                            "off requested; proxy upstream may not honour it"),
            # Three separate roles; only a production-wire run against the official
            # endpoint is an acceptance test of what visitors get.
            "roles": {"production": {"endpoint": DEEPSEEK_URL, "model": DEEPSEEK_MODEL,
                                     "thinking": "disabled", "source": "norn.cloud_provider"},
                      "candidates": {"models": args.model, "wire": args.wire,
                                     "endpoint_host": urlsplit(args.base_url).netloc},
                      "judges": {"models": args.judge, "fallback": "transport/truncation/malformed only",
                                 "status": "assisted review; not qualified for independent ranking"}},
            "prompt_versions": [PROMPT_VERSION, PROMPT_V5], "questions": list(QUESTIONS),
            "execution": {"workers": args.workers, "max_in_flight": args.max_in_flight,
                          "max_requests_this_invocation": args.max_requests, "attempts": args.attempts,
                          "judge_max_tokens": args.judge_max_tokens, "resumed": args.resume},
            "transport_this_invocation": args.transport.snapshot(),
            "summary": summarize(sessions, verdicts), "sessions": sessions, "verdicts": verdicts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--judge", action="append",
                        help="Judge model(s) in fallback order; pick families other than the candidates")
    parser.add_argument("--key-file", type=Path, help="JSON file with an api_key field")
    parser.add_argument("--workers", type=int, default=6, help="Independent session/pair workers (default 6)")
    parser.add_argument("--max-in-flight", type=int, default=6, help="Shared HTTP limit for generation, judges and retries (default 6)")
    parser.add_argument("--max-requests", type=int, default=128, help="HTTP attempt budget per invocation, including retries")
    parser.add_argument("--attempts", type=int, default=2, help="Maximum HTTP attempts per request")
    parser.add_argument("--timeout", type=float, default=360, help="Judge HTTP timeout; generation is capped at 120 seconds")
    parser.add_argument("--judge-max-tokens", type=int, default=24000,
                        help="Separate output budget for thinking judges (6000 truncated HY4 in qualification)")
    parser.add_argument("--judge-reasoning", default="none",
                        help="reasoning_effort sent to judges; default 'none' uses model defaults by omitting it ('off' may not disable thinking)")
    parser.add_argument("--wire", choices=("compatible", "production"), default="compatible",
                        help="production: send the Worker's exact DeepSeek request (thinking disabled) with the model "
                             "renamed; use it for acceptance runs")
    parser.add_argument("--resume-sessions", type=Path, help="Reuse a saved <output>.sessions.json and only judge")
    parser.add_argument("--resume", action="store_true", help="Resume this output's log; reuse finished generations and verdicts")
    parser.add_argument("--dry-run", action="store_true", help="Show the plan without credentials, files or network calls")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.judge_reasoning = None if args.judge_reasoning == "none" else args.judge_reasoning
    args.judge = args.judge or list(DEFAULT_JUDGES)
    if len(set(args.model)) != len(args.model) or len(set(args.judge)) != len(args.judge):
        parser.error("Model and judge lists must not contain duplicates")
    if not 1 <= args.workers <= 32 or not 1 <= args.max_in_flight <= 32 or not 1 <= args.attempts <= 10 or not 1 <= args.max_requests <= 1000000 or not 1 <= args.judge_max_tokens <= 65536:
        parser.error("Worker, request and token limits must be positive and bounded")
    args.output = args.output.resolve()
    args.cancelled = threading.Event()
    try:
        endpoint = urlsplit(args.base_url)
        if endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError()
        config = configuration(args)
    except (ValueError, OSError):
        parser.error("Invalid evaluation endpoint, configuration or checkpoint")
    if args.dry_run:
        print(json.dumps({"models": args.model, "judges": args.judge, "workers": args.workers,
                          "max_in_flight": args.max_in_flight, "max_requests": args.max_requests,
                          "judge_max_tokens": args.judge_max_tokens, "resume": args.resume,
                          "session_jobs": len(args.model) * 2 * len(FIXTURES),
                          "configuration_hash": digest(config)}, ensure_ascii=False))
        return 0
    try:
        key = json.loads(args.key_file.read_text())["api_key"] if args.key_file else os.environ.get("NORN_API_KEY", "")
    except (OSError, ValueError, KeyError):
        parser.error("The key file must contain an api_key field")
    if not isinstance(key, str) or not key.strip() or "\n" in key or "\r" in key:
        parser.error("Provide --key-file or NORN_API_KEY")
    args.transport = EvalTransport(args.base_url, key, concurrency=args.max_in_flight,
                                   max_requests=args.max_requests, attempts=args.attempts, timeout=args.timeout,
                                   cancel_event=args.cancelled)
    try:
        with RunState(args.output, config, resume=args.resume) as args.state:
            if args.output.exists():
                report = json.loads(args.output.read_text(encoding="utf-8"))
                print(json.dumps({"resumed": True, "network_requests_this_invocation": 0,
                                  "summary": report["summary"]}, ensure_ascii=False, indent=2))
                return 0
            report = evaluate(args, key)
            _write_new_json(args.output, report)
    except (EvaluationPaused, KeyboardInterrupt):
        _progress("evaluation.paused", network_requests_this_invocation=args.transport.snapshot().get("requests"),
                  next_step="Repeat the same inputs with --resume; increase --max-requests if needed")
        return 130 if isinstance(sys.exc_info()[1], KeyboardInterrupt) else 75
    except (RunStateError, ValueError, OSError) as exc:
        # Exception messages from upstream/credentials are never printed.
        parser.error("Evaluation state could not be resumed or saved (" + type(exc).__name__ + "). Use the original run inputs and --resume, or a new output for an intentional fresh run.")
    print(json.dumps({"summary": report["summary"], "transport_this_invocation": report["transport_this_invocation"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
