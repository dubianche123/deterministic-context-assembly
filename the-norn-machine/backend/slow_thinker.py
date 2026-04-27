"""
The Norn Machine — Step 3: Slow Thinker
Calls Amazon Bedrock to generate the final personality reading.
"""
import json
import os
import boto3

BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0"
)
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "ap-northeast-1")

# Fallback text if Bedrock fails or guardrail blocks
FALLBACK_TEXT = (
    "命运之线在此刻缠绕得过于紧密，织机暂时无法解读。\n\n"
    "这并不意味着你的选择没有意义——恰恰相反，"
    "它可能暗示着某种连织机本身都需要时间去理解的复杂性。\n\n"
    "请稍后再试，或带着新的直觉重新开始。"
)

OUTPUT_GUARDRAIL_TEXT = (
    "织机只读取你这一次留下的线，不会把你和任何人比较。\n\n"
    "这些选择指向的也不是某个冰冷分数，而是一种反复出现的姿势："
    "你在靠近答案之前，仍然需要确认它是否真的属于你。\n\n"
    "把这当作一个轻量提醒：先相信第一次被牵动的地方，"
    "再慢慢检查它能不能承受现实。"
)

_BANNED_OUTPUT_FRAGMENTS = (
    "上一位",
    "上一个玩家",
    "上一位玩家",
    "上一位测试者",
    "其他玩家",
    "其他测试者",
    "别的玩家",
    "上一条数据",
    "前一个人",
    "比别人",
    "比其他人",
    "E/I",
    "S/N",
    "T/F",
    "J/P",
    "MBTI",
    "置信度",
    "坐标",
    "差值",
    "均值",
)

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _client


def _invoke_bedrock(prompt_payload: dict, fallback_text: str, guardrail_id: str = None) -> dict:
    client = _get_client()

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "system": prompt_payload["system"],
        "messages": prompt_payload["messages"],
        "max_tokens": prompt_payload["max_tokens"],
        "temperature": prompt_payload["temperature"],
    }

    invoke_kwargs = {
        "modelId": BEDROCK_MODEL_ID,
        "contentType": "application/json",
        "accept": "application/json",
        "body": json.dumps(request_body),
    }

    if guardrail_id:
        invoke_kwargs["guardrailIdentifier"] = guardrail_id
        invoke_kwargs["guardrailVersion"] = "DRAFT"

    try:
        response = client.invoke_model(**invoke_kwargs)
        result = json.loads(response["body"].read())

        if result.get("stop_reason") == "guardrail_intervened":
            return {"text": fallback_text, "usage": {}, "blocked": True}

        text = result["content"][0]["text"]
        usage = result.get("usage", {})

        if _violates_output_guardrail(text):
            return {"text": OUTPUT_GUARDRAIL_TEXT, "usage": usage, "blocked": True}

        return {"text": text, "usage": usage, "blocked": False}

    except Exception as e:
        print(f"[Norn] Bedrock error: {e}")
        return {"text": fallback_text, "usage": {}, "blocked": True}


def slow_think(prompt_payload: dict, guardrail_id: str = None) -> dict:
    """
    Args:
        prompt_payload: output from template_router.route_template()
            {system, messages, max_tokens, temperature}
        guardrail_id: optional Bedrock guardrail ID

    Returns:
        {"text": "...", "usage": {...}, "blocked": bool}
    """
    return _invoke_bedrock(prompt_payload, FALLBACK_TEXT, guardrail_id)


def _violates_output_guardrail(text: str) -> bool:
    if not text:
        return False
    return any(fragment in text for fragment in _BANNED_OUTPUT_FRAGMENTS)
