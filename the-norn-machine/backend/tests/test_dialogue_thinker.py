"""Unit tests for the final dialogue guardrails."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import dialogue_thinker


def test_validate_dialogue_request_trims_and_limits():
    payload = {
        "user_message": "  你好，命运。  ",
        "turn_count": "1",
        "history": [],
        "analysis": {"mbti_type": "INFJ"},
    }
    result = dialogue_thinker.validate_dialogue_request(payload)
    assert result["user_message"] == "你好，命运。"
    assert result["turn_count"] == 1
    assert result["max_turns"] == 2

    too_long = {"user_message": "a" * 101}
    try:
        dialogue_thinker.validate_dialogue_request(too_long)
        raise AssertionError("Expected a validation error for >100 characters")
    except ValueError as err:
        assert "100" in str(err)


def test_validate_dialogue_request_clamps_client_limit():
    sparse = dialogue_thinker.validate_dialogue_request({
        "user_message": "继续",
        "turn_count": 1,
        "total_rounds": 3,
        "max_turns": 2,
    })
    assert sparse["max_turns"] == 1

    dense = dialogue_thinker.validate_dialogue_request({
        "user_message": "继续",
        "turn_count": 1,
        "total_rounds": 12,
        "max_turns": 1,
    })
    assert dense["max_turns"] == 1

    overflow = dialogue_thinker.validate_dialogue_request({
        "user_message": "继续",
        "turn_count": 1,
        "total_rounds": 30,
        "max_turns": 5,
    })
    assert overflow["max_turns"] == 3


def test_should_lock_dialogue():
    assert dialogue_thinker.should_lock_dialogue(2) is True
    assert dialogue_thinker.should_lock_dialogue(1) is False
    assert dialogue_thinker.should_lock_dialogue(1, 1) is True
    assert dialogue_thinker.should_lock_dialogue(2, 3) is False


def test_dialogue_limit_for_rounds():
    assert dialogue_thinker.dialogue_limit_for_rounds(5) == 1
    assert dialogue_thinker.dialogue_limit_for_rounds(6) == 2
    assert dialogue_thinker.dialogue_limit_for_rounds(20) == 2
    assert dialogue_thinker.dialogue_limit_for_rounds(21) == 3
    assert dialogue_thinker.dialogue_limit_for_rounds(mode="sparse_mode") == 1
    assert dialogue_thinker.dialogue_limit_for_rounds(mode="overflow_mode") == 3


def test_turns_over_limit_short_circuit_without_bedrock():
    original = dialogue_thinker._invoke_bedrock
    called = {"value": False}

    def fake_invoke(*args, **kwargs):
        called["value"] = True
        return {"text": "should not be used", "usage": {}, "blocked": False}

    dialogue_thinker._invoke_bedrock = fake_invoke
    try:
        result = dialogue_thinker.dialogue_think({
            "user_message": "继续",
            "turn_count": 3,
            "total_rounds": 3,
            "history": [],
            "analysis": {},
        })
        assert result["blocked"] is True
        assert result["ended"] is True
        assert result["max_turns"] == 1
        assert "织机不再继续回声" in result["text"]
        assert called["value"] is False
    finally:
        dialogue_thinker._invoke_bedrock = original


def test_one_turn_non_reply_is_repaired():
    original = dialogue_thinker._invoke_bedrock

    def fake_invoke(*args, **kwargs):
        return {
            "text": "（织机发出一阵轻微的嘶鸣，没有回应）",
            "usage": {},
            "blocked": False,
        }

    dialogue_thinker._invoke_bedrock = fake_invoke
    try:
        result = dialogue_thinker.dialogue_think({
            "user_message": "我是不是想太多？",
            "turn_count": 1,
            "total_rounds": 3,
            "history": [],
            "analysis": {},
            "max_turns": 1,
        })
        assert result["ended"] is True
        assert "没有回应" not in result["text"]
        assert "嘶鸣" not in result["text"]
        assert "收束" in result["text"]
    finally:
        dialogue_thinker._invoke_bedrock = original


if __name__ == "__main__":
    test_validate_dialogue_request_trims_and_limits()
    test_validate_dialogue_request_clamps_client_limit()
    test_should_lock_dialogue()
    test_dialogue_limit_for_rounds()
    test_turns_over_limit_short_circuit_without_bedrock()
    test_one_turn_non_reply_is_repaired()
    print("All dialogue thinker tests passed.")
