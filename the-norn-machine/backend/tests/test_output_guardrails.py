"""Tests for final-reading prompt and output guardrails."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fast_thinker import fast_think
from slow_thinker import _violates_output_guardrail
from template_router import route_template


def test_prompt_payload_hides_internal_axis_fields():
    analysis = fast_think(
        [{"id": "NORN_0001", "round": 25}],
        total_rounds=25,
    )
    payload = route_template(analysis)
    user_messages = "\n".join(
        message["content"]
        for message in payload["messages"]
        if message["role"] == "user"
    )

    for fragment in (
        "计算结果",
        "E/I",
        "S/N",
        "T/F",
        "J/P",
        "MBTI",
        "置信度",
        "坐标",
        "差值",
        "均值",
    ):
        assert fragment not in user_messages


def test_output_guardrail_catches_cross_user_and_internal_terms():
    assert _violates_output_guardrail("你的节奏比上一位要快。") is True
    assert _violates_output_guardrail("你的数据里有一个 T/F 差值。") is True
    assert _violates_output_guardrail("你在混乱里寻找秩序，也给自己保留余地。") is False
