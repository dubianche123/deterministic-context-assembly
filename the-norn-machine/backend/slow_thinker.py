"""
The Norn Machine — Step 3: Slow Thinker
Calls Amazon Bedrock to generate the final personality reading.
"""
import json
import os

if __package__:
    from .output_guardrails import output_reason_codes, validate_output
else:
    from output_guardrails import output_reason_codes, validate_output

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

_client = None

def _get_client(region=None):
    global _client
    if region is not None and region != BEDROCK_REGION:
        import boto3
        return boto3.client("bedrock-runtime", region_name=region)
    if _client is None:
        # Only the explicitly selected Bedrock adapter needs the AWS SDK.
        import boto3
        _client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _client


def _invoke_bedrock(prompt_payload: dict, fallback_text: str, guardrail_id: str = None) -> dict:
    settings = prompt_payload.get("_bedrock_config", {})
    usage = {}
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "system": prompt_payload["system"],
        "messages": prompt_payload["messages"],
        "max_tokens": prompt_payload["max_tokens"],
        "temperature": prompt_payload["temperature"],
    }

    invoke_kwargs = {
        "modelId": settings.get("model", BEDROCK_MODEL_ID),
        "contentType": "application/json",
        "accept": "application/json",
        "body": json.dumps(request_body),
    }

    if guardrail_id:
        invoke_kwargs["guardrailIdentifier"] = guardrail_id
        invoke_kwargs["guardrailVersion"] = "DRAFT"

    try:
        region = settings.get("region", BEDROCK_REGION)
        client = _get_client() if region == BEDROCK_REGION else _get_client(region)
        response = client.invoke_model(**invoke_kwargs)
        result = json.loads(response["body"].read())
        if not isinstance(result, dict):
            raise ValueError("Invalid Bedrock response object")
        usage = result.get("usage", {})
        metadata = {"model": invoke_kwargs["modelId"],
                    "request_id": response.get("ResponseMetadata", {}).get("RequestId")}

        if result.get("stop_reason") == "guardrail_intervened":
            return {"text": fallback_text, "usage": usage, **metadata,
                    "blocked": True, "status": "policy_blocked",
                    "reason_codes": ["provider_policy"]}
        if result.get("stop_reason") == "max_tokens":
            return {**validate_output(None), "usage": usage, **metadata,
                    "status": "invalid_output", "reason_codes": ["output_truncated"]}

        text = "".join(block.get("text", "") for block in result.get("content", [])
                       if isinstance(block, dict) and isinstance(block.get("text"), str))

        return {**validate_output(text), "usage": usage, **metadata}

    except (ValueError, KeyError, IndexError, TypeError):
        return {**validate_output(None), "usage": usage,
                "status": "invalid_output", "reason_codes": ["invalid_response"]}
    except Exception as e:
        # Exception strings can include remote response bodies or credentials.
        print(f"[Norn] Bedrock error: {type(e).__name__}")
        return {"text": fallback_text, "usage": {}, "blocked": True,
                "status": "provider_unavailable", "reason_codes": ["provider_error"]}


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
    """Compatibility predicate; providers should retain validate_output details."""
    return bool(output_reason_codes(text)) if text else False
