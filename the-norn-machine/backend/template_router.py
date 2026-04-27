"""
The Norn Machine — Step 2: Template Router
Routes to the correct Few-Shot prompt based on data density.
Assembles the final Bedrock Messages API payload.
"""
import json
from pathlib import Path

_TEMPLATES = {}

def _load_templates():
    global _TEMPLATES
    if not _TEMPLATES:
        prompts_dir = Path(__file__).parent / "config" / "prompts"
        for mode in ["sparse_mode", "dense_mode", "overflow_mode"]:
            with open(prompts_dir / f"{mode}.json", "r", encoding="utf-8") as f:
                _TEMPLATES[mode] = json.load(f)
    return _TEMPLATES


def route_template(analysis: dict) -> dict:
    """
    Takes fast_thinker output, returns a fully assembled Bedrock Messages API payload.
    """
    templates = _load_templates()
    mode = analysis["data_density"]
    template = templates[mode]

    # ── Build the user message with structured data ──
    # For overflow_mode, prepend a code-generated statistical summary
    stats_block = ""
    if mode == "overflow_mode":
        stats_block = _build_stats_summary(analysis)

    user_content = template["user_template"].format(
        mbti_type=analysis["mbti_type"],
        confidence_ei=f'{analysis["confidence"]["E_I"]:.0%}',
        confidence_sn=f'{analysis["confidence"]["S_N"]:.0%}',
        confidence_tf=f'{analysis["confidence"]["T_F"]:.0%}',
        confidence_jp=f'{analysis["confidence"]["J_P"]:.0%}',
        coords=json.dumps(analysis["coords_avg"], ensure_ascii=False),
        behavior_profile=_build_behavior_profile(analysis),
        emotional_texture=_build_emotional_texture(analysis),
        concrete_anchors=_build_concrete_anchors(analysis),
        signature_signal=_build_signature_line(analysis),
        total_rounds=analysis["total_rounds"],
        total_selections=analysis["total_selections"],
        stats_block=stats_block,
    )

    # ── Assemble messages array ──
    messages = []

    # Few-shot examples
    for example in template.get("few_shot", []):
        messages.append({"role": "user", "content": example["user"]})
        messages.append({"role": "assistant", "content": example["assistant"]})

    # Actual user message
    messages.append({"role": "user", "content": user_content})

    return {
        "system": template["system_prompt"],
        "messages": messages,
        "max_tokens": template.get("max_tokens", 500),
        "temperature": template.get("temperature", 0.8),
    }


def _build_behavior_profile(analysis: dict) -> str:
    """Translate raw card signals into a stable, player-facing behavior profile."""
    coords = analysis.get("coords_avg", {})
    confidence = analysis.get("confidence", {})
    revision = analysis.get("choice_revision", {})

    def describe(dim, positive, negative, balanced):
        val = coords.get(dim, 0)
        conf = confidence.get(dim, 0)
        if conf < 0.18:
            return balanced
        return positive if val >= 0 else negative

    revision_decision = _describe_choice_revision(revision, "decision")
    revision_pressure = _describe_choice_revision(revision, "pressure")
    revision_blind_spot = _describe_choice_revision(revision, "blind_spot")
    revision_suggestion = _describe_choice_revision(revision, "suggestion")

    decision_parts = [
        revision_decision,
        describe(
            "S_N",
            "判断问题时，你更容易先抓住潜在意义、趋势和可能性。",
            "判断问题时，你更容易先确认事实、经验和现实条件。",
            "判断问题时，你会在现实证据和潜在意义之间反复校准。",
        ),
        describe(
            "T_F",
            "你习惯检查逻辑、结构和因果是否站得住脚。",
            "你习惯检查一件事对人、关系和价值是否说得过去。",
            "你很难只靠逻辑或感受下结论，通常需要两边都能自洽。",
        ),
        describe(
            "J_P",
            "面对不确定时，你会本能地寻找框架、边界和下一步。",
            "面对不确定时，你会给变化保留空间，避免过早定型。",
            "你既需要方向，也不愿太早牺牲可能性。",
        ),
    ]
    decision_style = " ".join(part for part in decision_parts if part)

    relation_style = " ".join([
        describe(
            "E_I",
            "你会被外部反馈和正在发生的现场点亮。",
            "你更需要先回到内在世界，把自己的判断安放好。",
            "你在独处和互动之间切换，关键看这段连接是否值得投入。",
        ),
        describe(
            "T_F",
            "关系里你重视清晰、可靠和边界，讨厌含混消耗。",
            "关系里你重视被理解、被接住，以及彼此感受是否真实。",
            "关系里你既想被理解，也需要清晰边界。",
        ),
    ])

    stress_parts = [
        revision_pressure,
        describe(
            "J_P",
            "压力上来时，你倾向于把局面整理成可控步骤。",
            "压力上来时，你倾向于先保留弹性，等更多信息出现。",
            "压力上来时，你会在控制局面和保留弹性之间拉扯。",
        ),
        describe(
            "T_F",
            "你可能用分析、规划或效率来压住不安。",
            "你可能先感受到关系和情绪的波动，再慢慢整理答案。",
            "你会同时听见理性判断和情绪信号，两边都关不掉。",
        ),
    ]
    stress_response = " ".join(part for part in stress_parts if part)

    blind_parts = [
        revision_blind_spot,
        describe(
            "S_N",
            "盲区可能是太相信远处的图景，反而低估眼前细节的重量。",
            "盲区可能是太信任已知经验，反而错过变化刚出现时的信号。",
            "盲区可能是一直校准，却迟迟不愿承认自己已经有答案。",
        ),
        describe(
            "J_P",
            "你可能把安全感寄托在控制和准确上。",
            "你可能把自由感寄托在不做最终选择上。",
            "你可能同时害怕失控，也害怕太早被定义。",
        ),
    ]
    blind_spot = " ".join(part for part in blind_parts if part)

    suggestion_parts = [
        revision_suggestion,
        describe(
            "J_P",
            "建议你给计划留一点呼吸空间，允许答案不是一次成型。",
            "建议你在保留可能性的同时，为自己设一个轻量的下一步。",
            "建议你先承认拉扯本身，再决定这一刻更需要方向还是余地。",
        ),
        describe(
            "T_F",
            "别只问这是否有效，也问它是否仍然让你像自己。",
            "别只问这是否温柔，也问它是否真的可持续。",
            "别急着在理性和感受之间选边，让它们共同投票。",
        ),
    ]
    suggestion = " ".join(part for part in suggestion_parts if part)

    return "\n".join([
        f"- 决策方式: {decision_style}",
        f"- 关系模式: {relation_style}",
        f"- 压力反应: {stress_response}",
        f"- 盲区: {blind_spot}",
        f"- 建议: {suggestion}",
    ])


def _describe_choice_revision(revision: dict, section: str) -> str:
    signal = revision.get("signal", "none") if isinstance(revision, dict) else "none"
    if signal == "none":
        return ""

    lines = {
        "decision": {
            "light": "你有过轻微撤回，说明第一反应之后还会再检查一次真实偏好。",
            "active": "你会先被某个选项牵动，又很快撤回，像是在防止自己被一时吸引带走。",
            "high": "你频繁改选，真正困难的不是看见偏好，而是允许偏好落定。",
        },
        "pressure": {
            "light": "压力下你会做一次复核，确认自己不是被瞬间情绪推着走。",
            "active": "压力下你容易进入反复校准：越想选准，越会重新审视已经做出的选择。",
            "high": "压力下你可能把撤回当作安全阀，用持续改选来推迟最终承担。",
        },
        "blind_spot": {
            "light": "盲区可能是把必要的谨慎误会成没有答案。",
            "active": "盲区可能是过度尊重每一种可能，导致真正想要的东西被不断延后。",
            "high": "盲区可能是把不落定当成自由，结果反而被选择本身困住。",
        },
        "suggestion": {
            "light": "建议保留复核，但给第一次被吸引的感觉一点信任。",
            "active": "建议你把撤回当作信息，而不是失败：它说明你正在筛掉不够真的东西。",
            "high": "建议给自己设一个温和的截止点，到了那里就让选择先发生，再观察它带来的答案。",
        },
    }
    return lines.get(section, {}).get(signal, "")


def _build_emotional_texture(analysis: dict) -> str:
    """Summarize what selected visual signals represent without naming objects."""
    text = " ".join(
        list(analysis.get("top_traits", []))
        + list(analysis.get("top_motifs", []))
        + list(analysis.get("rationale_snippets", []))
    )

    textures = []
    patterns = [
        (("战略", "系统", "结构", "目标", "效率", "规划", "秩序", "边界", "调度"), "秩序感、可控性、精确推进"),
        (("克制", "独立", "低社交", "内省", "私密", "安静", "沉默"), "克制、留白、自我确认"),
        (("洞察", "共情", "连接", "关系", "温柔", "引导", "照亮"), "深度连接、被理解、温和的责任感"),
        (("理想", "想象", "愿景", "梦境", "象征", "可能性", "灵感"), "想象力、意义感、尚未定型的可能"),
        (("自由", "冒险", "即兴", "感官", "热烈", "现场", "表演"), "活力、开放、即时的生命感"),
        (("守护", "责任", "传统", "稳定", "照料", "务实"), "稳定、照料、可靠的现实感"),
        (("理论", "好奇", "解构", "悖论", "实验"), "好奇、拆解、对答案保持怀疑"),
        (("反复校准", "选择复核", "决策摇摆"), "迟疑、复核、害怕过早定型"),
    ]
    for keys, label in patterns:
        if any(key in text for key in keys):
            textures.append(label)

    if not textures:
        textures.append("复杂、混合、需要被慢慢辨认的个人气质")

    return "、".join(textures[:4])


def _build_concrete_anchors(analysis: dict) -> str:
    """Provide abstracted emotional and sensory traits to induce the 'how did they know' effect."""
    traits = [str(item) for item in analysis.get("top_traits", []) if item]
    motifs = [str(item) for item in analysis.get("top_motifs", []) if item]
    scenes = [str(item) for item in analysis.get("scenes", []) if item]

    combined = []
    for item in traits + motifs + scenes:
        if item and item not in combined:
            combined.append(item)

    if combined:
        return "高频画面与情绪特质: " + "、".join(combined[:5])
    return "没有明显的特定画面情绪特质，主要依靠行为骨架判断。"


def _build_signature_line(analysis: dict) -> str:
    """Format the signature signal as a single concrete anchor the LLM must mention."""
    sig = analysis.get("signature_signal")
    if sig:
        return f"你在选择中反复触碰的一个具体信号是：「{sig}」"
    return "没有足够数据提取单一标志性信号。"


def _build_commonality_summary(analysis: dict) -> str:
    """Backward-compatible wrapper for older tests or prompt drafts."""
    return _build_behavior_profile(analysis)


def _build_stats_summary(analysis: dict) -> str:
    """For overflow mode: code-generated statistical summary before LLM sees data."""
    lines = [
        f"[代码层统计摘要]",
        f"总轮数: {analysis['total_rounds']}, 总选择数: {analysis['total_selections']}",
        f"坐标均值: E/I={analysis['coords_avg']['E_I']}, S/N={analysis['coords_avg']['S_N']}, "
        f"T/F={analysis['coords_avg']['T_F']}, J/P={analysis['coords_avg']['J_P']}",
        f"高频特质: {'、'.join(analysis['top_traits'][:5])}",
        f"情感底色: {_build_emotional_texture(analysis)}",
        f"画面锚点: {_build_concrete_anchors(analysis)}",
    ]
    return "\n".join(lines)
