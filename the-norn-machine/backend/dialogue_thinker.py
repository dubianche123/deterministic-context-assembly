"""
The Norn Machine — Final Dialogue Helper
Builds the final one-to-three-turn user dialogue and enforces the hard guardrails.
"""
import json
from pathlib import Path

from slow_thinker import _invoke_bedrock

MAX_USER_CHARS = 100
DEFAULT_DIALOGUE_TURNS = 2
NON_REPLY_MARKERS = ("没有回应", "无回应", "嘶鸣", "沉默不语")

FALLBACK_TEXT = (
    "命运已经把线收拢到这里了。\n\n"
    "再往前追问，答案也只会像雾一样散开。"
)

_TEMPLATE = None


def dialogue_limit_for_rounds(total_rounds: int = None, mode: str = None) -> int:
    if total_rounds is not None:
        try:
            rounds = int(total_rounds)
        except (TypeError, ValueError):
            rounds = None
        if rounds is not None:
            if rounds <= 5:
                return 1
            if rounds <= 20:
                return 2
            return 3

    if mode == "sparse_mode":
        return 1
    if mode == "overflow_mode":
        return 3
    return DEFAULT_DIALOGUE_TURNS


def lockout_text(max_turns: int) -> str:
    turn_words = {1: "一轮", 2: "两轮", 3: "三轮"}
    turn_label = turn_words.get(max_turns, f"{max_turns}轮")
    return (
        f"{turn_label}已满，织机不再继续回声。\n\n"
        "剩下的事，只能交给时间和你自己的直觉。"
    )


def _load_template():
    global _TEMPLATE
    if _TEMPLATE is None:
        p = Path(__file__).parent / "config" / "prompts" / "final_dialogue.json"
        with open(p, "r", encoding="utf-8") as f:
            _TEMPLATE = json.load(f)
    return _TEMPLATE


def validate_dialogue_request(body: dict) -> dict:
    user_message = str(body.get("user_message", "")).strip()
    if not user_message:
        raise ValueError("请输入一句不超过100字的话")
    if len(user_message) > MAX_USER_CHARS:
        raise ValueError("输入不能超过100字")

    try:
        turn_count = int(body.get("turn_count", 1))
    except (TypeError, ValueError):
        turn_count = 1
    if turn_count < 1:
        turn_count = 1

    history = body.get("history", [])
    if history is None:
        history = []
    if not isinstance(history, list):
        raise ValueError("history 必须是数组")

    analysis = body.get("analysis", {})
    if analysis is None:
        analysis = {}
    if not isinstance(analysis, dict):
        raise ValueError("analysis 必须是对象")

    total_rounds = body.get("total_rounds", analysis.get("total_rounds"))
    server_max_turns = dialogue_limit_for_rounds(total_rounds, analysis.get("mode"))
    max_turns = server_max_turns

    client_max_turns = body.get("max_turns")
    if client_max_turns is not None:
        try:
            client_max_turns = int(client_max_turns)
        except (TypeError, ValueError):
            client_max_turns = None
        if client_max_turns and client_max_turns > 0:
            max_turns = min(server_max_turns, client_max_turns)

    return {
        "user_message": user_message,
        "turn_count": turn_count,
        "history": history,
        "analysis": analysis,
        "total_rounds": total_rounds,
        "max_turns": max_turns,
    }


def should_lock_dialogue(turn_count: int, max_turns: int = DEFAULT_DIALOGUE_TURNS) -> bool:
    return turn_count >= max_turns


def build_analysis_summary(analysis: dict) -> str:
    if not analysis:
        return "暂无额外画像数据，只保留当前这句追问。"

    parts = []
    if analysis.get("mbti_type"):
        parts.append(f"结果: {analysis['mbti_type']}")
    if analysis.get("mode"):
        parts.append(f"模式: {analysis['mode']}")
    if analysis.get("reading"):
        reading = str(analysis["reading"]).strip().replace("\n", " ")
        parts.append(f"上一段结论: {reading[:180]}")
    return "；".join(parts) if parts else "暂无额外画像数据，只保留当前这句追问。"


def format_history(history: list) -> str:
    if not history:
        return "（空）"

    lines = []
    for item in history[-4:]:
        role = item.get("role", "user")
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        prefix = "玩家" if role == "user" else "织机"
        lines.append(f"{prefix}: {content}")
    return "\n".join(lines) if lines else "（空）"


def build_prompt_payload(validated: dict) -> dict:
    template = _load_template()
    analysis_summary = build_analysis_summary(validated["analysis"])
    history_text = format_history(validated["history"])

    user_content = template["user_template"].format(
        analysis_summary=analysis_summary,
        history_text=history_text,
        user_message=validated["user_message"],
        turn_count=validated["turn_count"],
        max_turns=validated["max_turns"],
        lockout_text=lockout_text(validated["max_turns"]),
    )

    messages = []
    for example in template.get("few_shot", []):
        messages.append({"role": "user", "content": example["user"]})
        messages.append({"role": "assistant", "content": example["assistant"]})

    messages.append({"role": "user", "content": user_content})

    return {
        "system": template["system_prompt"],
        "messages": messages,
        "max_tokens": template.get("max_tokens", 220),
        "temperature": template.get("temperature", 0.75),
    }


def _looks_like_non_reply(text: str) -> bool:
    cleaned = str(text or "").strip()
    if len(cleaned) < 8:
        return True
    return any(marker in cleaned for marker in NON_REPLY_MARKERS)


def _repair_non_reply(ended: bool) -> str:
    if ended:
        return (
            "织机听见了你的问题。答案不必落成判词：你此刻在意的，"
            "正是那根线牵住你的地方。\n\n"
            "这一轮到此收束，剩下交给直觉。"
        )
    return (
        "织机听见了你的问题，只把答案压得更低：先看见自己为什么被这句话牵住，"
        "再决定要不要继续追问。"
    )


def dialogue_think(validated: dict, guardrail_id: str = None) -> dict:
    analysis = validated.get("analysis", {})
    mode = analysis.get("mode") if isinstance(analysis, dict) else None
    max_turns = validated.get(
        "max_turns",
        dialogue_limit_for_rounds(validated.get("total_rounds"), mode),
    )
    if validated["turn_count"] > max_turns:
        return {
            "text": lockout_text(max_turns),
            "usage": {},
            "blocked": True,
            "ended": True,
            "max_turns": max_turns,
        }

    prompt_payload = build_prompt_payload(validated)
    result = _invoke_bedrock(prompt_payload, FALLBACK_TEXT, guardrail_id)

    result["ended"] = should_lock_dialogue(validated["turn_count"], max_turns)
    result["max_turns"] = max_turns
    if result["ended"] and result["blocked"]:
        result["text"] = lockout_text(max_turns)
    elif not result.get("blocked") and _looks_like_non_reply(result.get("text", "")):
        result["text"] = _repair_non_reply(result["ended"])
    return result
