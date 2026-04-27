"""
The Norn Machine — Lambda Handler
Single entry point for POST /analyze
"""
import json
from fast_thinker import fast_think
from template_router import route_template
from slow_thinker import slow_think
from dialogue_thinker import dialogue_limit_for_rounds, dialogue_think, validate_dialogue_request

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Api-Key",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}

EMPTY_SELECTION_READING = (
    "你没有选择任何一张画面，这本身也是一次选择。\n\n"
    "织机把这看作一种停在门槛上的姿势：你没有急着把自己交给某个符号，"
    "也没有让一瞬间的吸引替你决定方向。也许此刻最像你的，不是某个答案，"
    "而是对答案保持距离的那一下迟疑。\n\n"
    "这不是空白。它更像一枚没有落下的骰子：命运已经被拿在手里，"
    "只是你暂时不愿让它发出声音。"
)


def handler(event, context):
    # Handle CORS preflight
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    try:
        path = event.get("path") or event.get("resource") or "/analyze"
        body = json.loads(event.get("body", "{}"))

        if path.endswith("/dialogue"):
            validated = validate_dialogue_request(body)
            result = dialogue_think(validated)
            return {
                "statusCode": 200,
                "headers": CORS_HEADERS,
                "body": json.dumps({
                    "reply": result["text"],
                    "text": result["text"],
                    "blocked": result["blocked"],
                    "ended": result.get("ended", False),
                    "turn_count": validated["turn_count"],
                    "max_turns": result.get("max_turns", validated["max_turns"]),
                }, ensure_ascii=False),
            }

        selections = body.get("selections", [])
        total_rounds = body.get("total_rounds", 1)
        round_durations = body.get("round_durations", [])
        deselection_events = body.get("deselection_events", [])

        if not selections:
            analysis = fast_think([], total_rounds, round_durations, deselection_events)
            return {
                "statusCode": 200,
                "headers": CORS_HEADERS,
                "body": json.dumps({
                    "mbti_type": analysis["mbti_type"],
                    "confidence": analysis["confidence"],
                    "reading": EMPTY_SELECTION_READING,
                    "mode": analysis["data_density"],
                    "total_rounds": analysis["total_rounds"],
                    "choice_revision": analysis.get("choice_revision", {}),
                    "max_dialogue_turns": dialogue_limit_for_rounds(analysis["total_rounds"], analysis["data_density"]),
                    "blocked": False,
                    "empty_selection": True,
                }, ensure_ascii=False),
            }

        # Step 1: Fast Think (< 5ms)
        analysis = fast_think(selections, total_rounds, round_durations, deselection_events)

        # Step 2: Template Route (< 1ms)
        prompt_payload = route_template(analysis)

        # Step 3: Slow Think (~1-2s)
        result = slow_think(prompt_payload)

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "mbti_type": analysis["mbti_type"],
                "confidence": analysis["confidence"],
                "reading": result["text"],
                "mode": analysis["data_density"],
                "total_rounds": analysis["total_rounds"],
                "choice_revision": analysis.get("choice_revision", {}),
                "max_dialogue_turns": dialogue_limit_for_rounds(analysis["total_rounds"], analysis["data_density"]),
                "blocked": result["blocked"],
            }, ensure_ascii=False),
        }

    except ValueError as e:
        return {
            "statusCode": 400,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": str(e)}, ensure_ascii=False),
        }

    except Exception as e:
        print(f"[Norn] Handler error: {e}")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Internal server error"}, ensure_ascii=False),
        }
