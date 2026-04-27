"""Unit tests for Lambda-level response shaping."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import lambda_function


def test_empty_selection_returns_mystical_reading():
    event = {
        "path": "/analyze",
        "httpMethod": "POST",
        "body": json.dumps({
            "selections": [],
            "total_rounds": 1,
            "round_durations": [{"round": 1, "duration_ms": 1200}],
        }),
    }

    response = lambda_function.handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["empty_selection"] is True
    assert body["mbti_type"] == "XXXX"
    assert body["max_dialogue_turns"] == 1
    assert "没有选择任何一张画面" in body["reading"]


if __name__ == "__main__":
    test_empty_selection_returns_mystical_reading()
    print("All lambda function tests passed.")
