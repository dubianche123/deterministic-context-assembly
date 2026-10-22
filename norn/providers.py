"""One explicit language-rendering boundary, with a fully offline default.

The deterministic engine is authoritative. Mock replaces only prose generation;
it never pretends to measure model tokens or automatically selects a paid route.
"""
from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from .core import engine_module
from .guardrails import validate_output

PROVIDERS = ("mock", "openai-compatible", "anthropic", "gemini", "bedrock")
UNAVAILABLE_TEXT = "织机暂时没能接上语言的回声。你的选择还在，请稍后重试。"
INVALID_TEXT = "这一次回声没有形成完整的回答。你的选择还在，可以重新尝试。"


def localize_result(result, language):
    """Localize product failure copy, without changing status, usage or prose."""
    if language != "en" or result.get("status") in ("ok", "sanitized", None):
        return result
    result = deepcopy(result)
    result["text"] = ("The loom could not reach the language service. Your choices are still here; please try again."
                      if result.get("status") in ("provider_unavailable", "internal_error") else
                      "The reply did not arrive as a complete answer. Your choices are still here; you can try again.")
    return result


def provider_config(provider: str, config: dict | None = None) -> dict:
    """Resolve only the chosen provider's settings; never inspect a global config."""
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider; choose " + ", ".join(PROVIDERS))
    values = dict(config or {})
    if provider == "mock":
        return {}
    cache_enabled = values.get("cache_enabled", os.environ.get("NORN_PROMPT_CACHE", "off"))
    if isinstance(cache_enabled, str):
        if cache_enabled.lower() not in ("on", "off"):
            raise ValueError("NORN_PROMPT_CACHE must be on or off")
        cache_enabled = cache_enabled.lower() == "on"
    if type(cache_enabled) is not bool:
        raise ValueError("cache_enabled must be a boolean")
    if provider in ("openai-compatible", "anthropic", "gemini"):
        defaults = {"anthropic": "https://api.anthropic.com/v1",
                    "gemini": "https://generativelanguage.googleapis.com/v1beta"}
        base_url = values.get("base_url", os.environ.get("NORN_BASE_URL", defaults.get(provider, ""))).strip()
        model = values.get("model", os.environ.get("NORN_MODEL", "")).strip()
        api_key = values.get("api_key", os.environ.get("NORN_API_KEY", ""))
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Set NORN_BASE_URL to an explicit HTTP(S) API base URL without credentials or query parameters")
        if not model:
            raise ValueError("Set NORN_MODEL for the explicitly selected provider")
        if provider in ("anthropic", "gemini") and (not isinstance(api_key, str) or not api_key.strip()):
            raise ValueError("Set NORN_API_KEY for the explicitly selected provider")
        timeout = values.get("timeout", 30)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
            raise ValueError("Provider timeout must be between 0 and 120 seconds")
        resolved = {"base_url": base_url.rstrip("/"), "model": model,
                    "api_key": api_key, "timeout": timeout, "cache_enabled": cache_enabled}
        if provider == "openai-compatible":
            reasoning = values.get("reasoning_effort", os.environ.get("NORN_REASONING_EFFORT"))
            if reasoning is not None:
                if reasoning not in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
                    raise ValueError("NORN_REASONING_EFFORT must be off, minimal, low, medium, high, xhigh, or max")
                resolved["reasoning_effort"] = reasoning
        return resolved
    # Bedrock remains explicitly selected and is the only SDK-dependent route.
    engine = engine_module("slow_thinker")
    return {**values, "cache_enabled": cache_enabled,
            "model": values.get("model", os.environ.get("NORN_MODEL", engine.BEDROCK_MODEL_ID)),
            "region": values.get("region", os.environ.get("NORN_REGION", engine.BEDROCK_REGION))}


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _usage(raw: dict | None = None, *, mock: bool = False, provider: str = "openai-compatible") -> dict:
    """Normalize counters without turning absent accounting into measured zero.

    Anthropic's input_tokens excludes cache reads/writes. Gemini's candidate
    tokens exclude thoughts; total minus prompt covers its complete output.
    Keep the original counters to make these different semantics inspectable.
    """
    raw = deepcopy(raw) if isinstance(raw, dict) else {}
    read = write = uncached = input_total = output = None
    if provider in ("anthropic", "bedrock"):
        uncached = _count(raw.get("input_tokens"))
        read = _count(raw.get("cache_read_input_tokens"))
        write = _count(raw.get("cache_creation_input_tokens"))
        if all(value is not None for value in (uncached, read, write)):
            input_total = uncached + read + write
        output = _count(raw.get("output_tokens"))
    elif provider == "gemini":
        input_total = _count(raw.get("promptTokenCount"))
        read = _count(raw.get("cachedContentTokenCount"))
        total = _count(raw.get("totalTokenCount"))
        if total is not None and input_total is not None and total >= input_total:
            output = total - input_total
        # This adapter never creates an explicit cachedContent resource. Absence
        # of a cache counter still remains unknown rather than an observed zero.
        if input_total is not None and read is not None and read <= input_total:
            uncached = input_total - read
    else:
        input_total = _count(raw.get("prompt_tokens", raw.get("input_tokens")))
        output = _count(raw.get("completion_tokens", raw.get("output_tokens")))
        details = raw.get("prompt_tokens_details")
        read = _count(details.get("cached_tokens")) if isinstance(details, dict) else None
        if read is None:
            read = _count(raw.get("cache_read_input_tokens"))
        if read is None:
            read = _count(raw.get("prompt_cache_hit_tokens"))
        write = _count(raw.get("cache_creation_input_tokens"))
        if input_total is not None and read is not None and read <= input_total:
            uncached = input_total - read
        elif _count(raw.get("prompt_cache_miss_tokens")) is not None:
            uncached = _count(raw.get("prompt_cache_miss_tokens"))
    values = {"input_tokens": input_total, "uncached_input_tokens": uncached,
              "output_tokens": output, "cache_read_input_tokens": read,
              "cache_creation_input_tokens": write, "cached_tokens": read}
    return {**values, **{name + "_observed": value is not None for name, value in values.items()},
            "source": "not_applicable" if mock else ("provider" if raw else "unavailable"),
            "raw": raw}


def _behavior_sections(analysis: dict) -> list[str]:
    # Reuse the same code-generated interpretation as the real model request.
    router = engine_module("template_router")
    return [line.removeprefix("- ").split(": ", 1)[-1]
            for line in router._build_behavior_profile(analysis).splitlines()]


def _mock_text(task: str, analysis: dict, user_message: str,
               turn_count: int, max_turns: int) -> str:
    if not analysis.get("total_selections"):
        if task == "dialogue":
            return ("这一次你没有留下选中的画面，因此织机没有更多选择证据。"
                    "你可以把这句追问留给自己，也可以重新开始，看看哪一张画面会让你停下来。")
        return engine_module("lambda_function").EMPTY_SELECTION_READING

    if analysis.get("analysis_kind") == "learned_preference":
        return _mock_preference_text(task, analysis, user_message, turn_count, max_turns)

    sections = _behavior_sections(analysis)
    if task == "analysis":
        return "织机把这一次的选择，收拢成几条可以慢慢辨认的线。\n\n" + "\n\n".join(sections)

    if (any(word in user_message for word in ("为什么", "依据", "证据", "怎么得出"))
            and not any(word in user_message for word in ("撤回", "改选", "犹豫", "反复"))):
        evidence = engine_module("session_context")._evidence(analysis)
        selected = evidence["selections"]
        if selected:
            row = selected[0]
            text = (f"这次线索包括第{row['round']}轮留下的选择（卡牌{row['card_id']}，事件{row['event_id']}）。"
                    + sections[0] + "这只是对本次选择的有限解释，不能证明你的性格原因。")
        else:
            text = "这次没有足够的具体选择证据，因此不能补造原因。" + sections[4]
    elif any(word in user_message for word in ("撤回", "改选", "犹豫", "反复")):
        revision = analysis.get("choice_revision", {})
        count = revision.get("total", 0)
        text = (f"这次记录里有 {count} 次撤回。" + sections[0] if count else
                "这次记录里没有撤回，因此织机不会把反复改选当作你的线索。" + sections[0])
    elif any(word in user_message for word in ("建议", "怎么办", "怎么做", "下一步")):
        text = sections[4]
    elif any(word in user_message for word in ("关系", "朋友", "别人", "相处")):
        text = sections[1]
    elif any(word in user_message for word in ("压力", "焦虑", "不安")):
        text = sections[2]
    elif any(word in user_message for word in ("盲区", "缺点", "困住")):
        text = sections[3]
    else:
        text = "这句追问可以先放回你这一次的选择里理解：" + sections[0] + " " + sections[4]
    if turn_count >= max_turns:
        text += "\n\n这一轮到此收束，剩下的线，交给你慢慢辨认。"
    return text


def _mock_text_en(task, analysis, message, turn, max_turns):
    """English offline rendering of the same recorded facts, with no paid call."""
    evidence = engine_module("session_context")._evidence(analysis)
    selections = evidence["selections"]
    count, rounds = analysis.get("total_selections", 0), analysis.get("total_rounds", 0)
    validation = analysis.get("preference", {}).get("validation", {})
    checked = (validation.get("status") == "measured"
               and validation.get("beats_baselines", validation.get("beats_random")) is True)
    limits = ("Your choices showed some consistency within this visit, though these clues remain limited to this record."
              if checked else "The clues are still too thin to say that they will reliably predict your next choices.")
    if not count:
        text = ("You did not keep any pictures this time, so there is no choice evidence to explain. "
                "You can begin again with whichever image makes you pause.")
    elif task == "analysis":
        text = (f"This time, you kept {count} pictures across {rounds} rounds. They form a small record of what held your attention, "
                "rather than a definition of who you are. A picture you passed over may simply have felt less inviting beside the others.\n\n"
                + limits + "\n\nOn another visit, notice the colours, light, and details that make you linger. "
                "You may find a familiar thread, or discover that a different scene draws you in.")
    elif any(word in message.lower() for word in ("undo", "deselect", "change", "revision", "hesitat")):
        kept = [row for row in evidence["revisions"] if row.get("final_selected")]
        text = f"The record contains {evidence['revision_total']} deselections. "
        text += ("At least one picture in the selected evidence was ultimately kept again; changing a choice is not the same as rejecting it. "
                 if kept else "A changed choice records a moment of reconsideration; it cannot establish a psychological reason. ")
        text += limits
    elif any(word in message.lower() for word in ("why", "evidence", "which", "basis")) and selections:
        row = selections[0]
        text = (f"One checkable clue is the picture you kept in round {row['round']} ({row['card_id']}). "
                "The interpretation follows choices among pictures shown together, without treating every unchosen image as a dislike. "
                + limits)
    else:
        text = ("A useful next step is to compare pictures with similar scenes but different colours, light, or details. "
                "Notice whether the same things still hold your attention. These choices offer a starting point for exploration, "
                "rather than an answer about your personality. " + limits)
    if task == "dialogue" and turn >= max_turns:
        text += "\n\nThe loom rests here for now; another set of pictures can offer a new thread."
    return text


def _mock_preference_text(task, analysis, message, turn, max_turns):
    """A faithful offline explanation of learned choices, without personality claims."""
    preference = analysis.get("preference", {})
    evidence = engine_module("session_context")._evidence(analysis)
    selections = evidence["selections"]
    scenes = "、".join(row["scene"] for row in selections[:3])
    observation_count = preference.get("observation_count", 0)
    validation = preference.get("validation", {})
    checked = (validation.get("status") == "measured"
               and validation.get("beats_baselines", validation.get("beats_random")) is True)
    limit = ("留出回合的预测暂时优于随机基线，仍只适用于这份记录。" if checked else
             "目前还没有足够证据证明它能稳定预测下一轮，你可以把这些线索当成继续探索的起点。")
    if task == "analysis":
        opening = (f"这一次，你留下了{analysis['total_selections']}张画面，其中包括{scenes}。"
                   if scenes else "这一次的选择已经收好。")
        learning = (f"织机从{observation_count}轮有同场比较的选择里学习画面偏好；"
                    "同一轮没选的画面只是相对比较，不等于你讨厌它们。" if observation_count else
                    "这份记录缺少同轮展示与比较证据，因此还不能训练出可用的偏好排序。")
        return opening + "\n\n" + learning + limit + "\n\n下一次可以留意画面的色彩、明暗和意象，看看吸引你的线索是否依然出现。"
    if any(word in message for word in ("撤回", "改选", "犹豫", "反复")):
        count = evidence["revision_total"]
        kept = [row for row in evidence["revisions"] if row.get("final_selected")]
        detail = (f"展示的样本里，{kept[0]['scene']}最后仍被保留；一次撤回并不等于最终放弃。" if kept else
                  "一次撤回只说明当时改变了选择，不能据此确定心理原因。")
        text = f"这次共有{count}次撤回。" + detail
    elif any(word in message for word in ("为什么", "依据", "证据", "怎么得出")) and selections:
        row = selections[0]
        reference = f"事件{row['event_id']}" if row.get("reference_kind") == "event" else "最终选择快照"
        text = f"可核对的线索包括第{row['round']}轮留下的{row['scene']}（{reference}）。学习结果来自同轮图片间的相对选择，不能推成性格结论。" + limit
    else:
        text = "下一步可以比较相近画面中不同的色彩、明暗和意象，留意自己是否仍然作出相近选择。" + limit
    if turn >= max_turns:
        text += "\n\n这一轮先到这里，新的画面会带来新的线索。"
    return text


def _request_json(url, payload, headers, timeout):
    request = Request(url, data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
                      method="POST")
    with urlopen(request, timeout=timeout) as response:
        data = response.read(2 * 1024 * 1024 + 1)
        request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Oversized provider response")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Provider response must be an object")
    return result, request_id


def _validated_result(text, usage, *, model=None, request_id=None):
    checked = validate_output(text)
    # Sanitized prose is a successful answer. Its reason codes and output status
    # remain visible while service state follows the ordinary success contract.
    output_status = checked.pop("status")
    return {**checked, "status": "ok" if output_status == "sanitized" else output_status,
            "output_status": output_status, "usage": usage,
            "model": model, "request_id": request_id}


def _policy_result(usage, *, model=None, request_id=None):
    return {"text": INVALID_TEXT, "blocked": True, "status": "policy_blocked",
            "output_status": "policy_blocked", "reason_codes": ["provider_policy"],
            "usage": usage, "model": model, "request_id": request_id}


def _truncated_result(usage, *, model=None, request_id=None):
    """A token-limit cutoff is incomplete prose, even if its prefix is valid."""
    return {"text": INVALID_TEXT, "blocked": True, "status": "invalid_output",
            "output_status": "invalid_output", "reason_codes": ["output_truncated"],
            "usage": usage, "model": model, "request_id": request_id}


def compatible_payload(prompt_payload: dict, config: dict) -> dict:
    """Construct the compatible wire format without credentials or I/O."""
    messages = [{"role": "system", "content": prompt_payload["system"]}]
    messages.extend(deepcopy(prompt_payload["messages"]))
    payload = {
        "model": config["model"], "messages": messages,
        "max_tokens": prompt_payload["max_tokens"],
        "temperature": prompt_payload.get("temperature", 0.8),
    }
    if config.get("reasoning_effort") is not None:
        payload["reasoning_effort"] = config["reasoning_effort"]
    return payload


def compatible_result(result: dict, request_id: str | None, config: dict) -> dict:
    """Normalize one compatible response through the shared prose guardrails."""
    usage = _usage(result.get("usage"))
    choices = result.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message")
    message = message if isinstance(message, dict) else {}
    metadata = {"model": result.get("model", config["model"]), "request_id": request_id or result.get("id")}
    if choice.get("finish_reason") == "content_filter" or message.get("refusal"):
        return _policy_result(usage, **metadata)
    if choice.get("finish_reason") == "length":
        return _truncated_result(usage, **metadata)
    return _validated_result(message.get("content"), usage, **metadata)


def _compatible(prompt_payload: dict, config: dict) -> dict:
    payload = compatible_payload(prompt_payload, config)
    headers = {}
    if config.get("api_key"):
        headers["Authorization"] = "Bearer " + config["api_key"]
    result, request_id = _request_json(config["base_url"] + "/chat/completions", payload, headers, config["timeout"])
    return compatible_result(result, request_id, config)


def _cache_requested(prompt, config):
    cache = prompt.get("cache")
    anchors = cache.get("anchors", []) if isinstance(cache, dict) else []
    anchors = anchors if isinstance(anchors, list) else []
    return bool(config.get("cache_enabled") and any(isinstance(anchor, dict) and anchor.get("type") == "system"
                                                  for anchor in anchors))


def _anthropic_wire(prompt, config):
    system = prompt.get("system", "")
    if _cache_requested(prompt, config):
        system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    return {"system": system, "messages": deepcopy(prompt.get("messages", [])),
            "max_tokens": prompt.get("max_tokens", 650), "temperature": prompt.get("temperature", 0.8)}


def _anthropic(prompt, config):
    payload = {"model": config["model"], **_anthropic_wire(prompt, config)}
    result, request_id = _request_json(config["base_url"] + "/messages", payload,
                                       {"x-api-key": config["api_key"], "anthropic-version": "2023-06-01"},
                                       config["timeout"])
    usage = _usage(result.get("usage"), provider="anthropic")
    metadata = {"model": result.get("model", config["model"]), "request_id": request_id or result.get("id")}
    if result.get("stop_reason") in ("refusal", "guardrail_intervened"):
        return _policy_result(usage, **metadata)
    if result.get("stop_reason") == "max_tokens":
        return _truncated_result(usage, **metadata)
    content = result.get("content")
    content = content if isinstance(content, list) else []
    text = "".join(block["text"] for block in content
                   if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str))
    return _validated_result(text, usage, **metadata)


def _gemini(prompt, config):
    # implicit caching needs no request field or resource creation. Preserve
    # native roles while translating only the provider's assistant spelling.
    contents = [{"role": "model" if item["role"] == "assistant" else "user",
                 "parts": [{"text": item["content"]}]} for item in prompt["messages"]]
    payload = {"systemInstruction": {"parts": [{"text": prompt["system"]}]},
               "contents": contents, "generationConfig": {"maxOutputTokens": prompt["max_tokens"],
                                                            "temperature": prompt.get("temperature", 0.8)}}
    model = config["model"].removeprefix("models/")
    url = config["base_url"] + "/models/" + quote(model, safe="") + ":generateContent"
    result, request_id = _request_json(url, payload, {"x-goog-api-key": config["api_key"]}, config["timeout"])
    usage = _usage(result.get("usageMetadata"), provider="gemini")
    metadata = {"model": result.get("modelVersion", config["model"]),
                "request_id": request_id or result.get("responseId")}
    feedback = result.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        return _policy_result(usage, **metadata)
    candidates = result.get("candidates")
    candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
    if candidate.get("finishReason") in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
        return _policy_result(usage, **metadata)
    if candidate.get("finishReason") == "MAX_TOKENS":
        return _truncated_result(usage, **metadata)
    content = candidate.get("content")
    parts = content.get("parts", []) if isinstance(content, dict) else []
    parts = parts if isinstance(parts, list) else []
    text = "".join(part["text"] for part in parts
                   if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought"))
    return _validated_result(text, usage, **metadata)


def _cache_metadata(provider, prompt, config):
    capability = {"mock": "not_applicable", "openai-compatible": "provider_managed",
                  "anthropic": "explicit_breakpoint", "bedrock": "model_dependent_breakpoint",
                  "gemini": "implicit"}[provider]
    return {"capability": capability, "requested": bool(config.get("cache_enabled")),
            "applied": provider in ("anthropic", "bedrock") and _cache_requested(prompt, config),
            "hit": None, "note": "Cache reuse is determined only by observed provider counters."}


def generate(provider: str, prompt_payload: dict, *, task: str = "analysis",
             analysis: dict | None = None, user_message: str = "",
             turn_count: int = 1, max_turns: int = 1,
             config: dict | None = None) -> dict:
    """Render once using an explicit provider, without retries or route fallback."""
    settings = provider_config(provider, config)
    if task not in ("analysis", "dialogue"):
        raise ValueError("Unknown rendering task")
    # Keep each model call tied to its own detached request and profile view.
    prompt_payload = deepcopy(prompt_payload)
    analysis = deepcopy(analysis or {})
    started = time.perf_counter()
    if provider == "mock":
        from .portrait import intent_from_prompt, mock_text
        intent = intent_from_prompt(prompt_payload)
        if intent is not None:
            text = mock_text(intent, task=task, user_message=user_message, turn_count=turn_count,
                             max_turns=max_turns, analysis=analysis)
        else:
            renderer = _mock_text_en if prompt_payload.get("language") == "en" else _mock_text
            text = renderer(task, analysis, user_message, turn_count, max_turns)
        result = _validated_result(text, _usage(mock=True))
    else:
        try:
            if provider == "bedrock":
                wire = _anthropic_wire(prompt_payload, settings)
                wire["_bedrock_config"] = {"model": settings["model"], "region": settings["region"]}
                raw = engine_module("slow_thinker")._invoke_bedrock(wire, UNAVAILABLE_TEXT)
                usage = _usage(raw.get("usage"), provider="bedrock")
                status = raw.get("status")
                if raw.get("text") == UNAVAILABLE_TEXT:
                    status = "provider_unavailable"
                if status is None:
                    status = "invalid_output" if raw.get("blocked") else "ok"
                if status in ("ok", "sanitized"):
                    result = _validated_result(raw.get("text"), usage,
                                               model=raw.get("model", settings["model"]), request_id=raw.get("request_id"))
                    if status == "sanitized" and result["status"] == "ok":
                        result.update(output_status="sanitized", reason_codes=raw.get("reason_codes", []))
                else:
                    result = {**raw, "status": status, "output_status": status, "usage": usage}
            else:
                adapter = {"openai-compatible": _compatible, "anthropic": _anthropic, "gemini": _gemini}[provider]
                result = adapter(prompt_payload, settings)
        except HTTPError as exc:
            # HTTP authentication, rate limits and transport errors remain
            # unavailable. Only explicit protocol policy codes become policy.
            policy = False
            try:
                error = json.loads(exc.read(65536)).get("error", {})
                policy = error.get("code", error.get("type")) in ("content_filter", "safety", "policy_violation")
            except (ValueError, TypeError, AttributeError):
                pass
            exc.close()
            result = (_policy_result(_usage()) if policy else
                      {"text": UNAVAILABLE_TEXT, "usage": _usage(), "blocked": False,
                       "status": "provider_unavailable", "output_status": "provider_unavailable", "reason_codes": ["provider_error"]})
        except (URLError, TimeoutError, OSError):
            # Do not echo remote response bodies, URLs, or credential-bearing errors.
            result = {"text": UNAVAILABLE_TEXT, "usage": _usage(),
                      "blocked": False, "status": "provider_unavailable", "output_status": "provider_unavailable",
                      "reason_codes": ["provider_error"]}
        except (ValueError, KeyError, IndexError, TypeError):
            result = {"text": INVALID_TEXT, "usage": _usage(),
                      "blocked": True, "status": "invalid_output", "output_status": "invalid_output",
                      "reason_codes": ["invalid_response"]}
    result = localize_result(result, prompt_payload.get("language", "zh"))
    return {"model": settings.get("model"), "request_id": None, **result,
            "cache": _cache_metadata(provider, prompt_payload, settings),
            "provider": provider, "simulated": provider == "mock",
            "provider_calls": 0 if provider == "mock" else 1,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
