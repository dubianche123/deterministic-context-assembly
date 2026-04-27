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
