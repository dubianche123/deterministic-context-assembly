"""
Benchmark Scenario Generator
Generates 20 simulated player sessions with varying rounds and selection counts.
For each scenario, produces:
  1. Optimized prompt (via fast_thinker + template_router pipeline)
  2. Naive prompt (raw card metadata dumped directly into the prompt)

Output: benchmark_prompts.json
"""
import sys
import os
import json
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fast_thinker import fast_think, _load_cards
from template_router import route_template

random.seed(42)

# ── Load all card IDs ──
cards = _load_cards()
ALL_CARD_IDS = list(cards.keys())

# ── Naive prompt builder: dumps raw metadata for every selected card ──
NAIVE_SYSTEM_PROMPT = (
    "你是一个性格分析系统。根据用户在多轮图片选择测试中选择的卡牌信息，"
    "分析用户的性格特征。每张卡牌都包含坐标、特质、意象、场景和理由等元数据。"
    "请根据这些信息生成一份性格分析报告。\n\n"
    "规则：\n"
    "- 用第二人称「你」\n"
    "- 绝不提及 MBTI、性格测试、心理学术语\n"
    "- 绝不输出内部字段名\n"
    "- 中文回答\n"
    "- 自然分段"
)


def build_naive_prompt(selections, total_rounds, round_durations=None, deselection_events=None):
    """Build a naive prompt that dumps ALL raw card metadata into the context."""
    cards_data = _load_cards()

    card_details = []
    for sel in selections:
        card_id = sel["id"]
        rnd = sel["round"]
        if card_id in cards_data:
            card = cards_data[card_id]
            card_details.append({
                "card_id": card_id,
                "round": rnd,
                "coords": card["coords"],
                "traits": card["traits"],
                "motifs": card["motifs"],
                "scene": card["scene"],
                "rationale": card["rationale"],
                "type": card["type"],
                "group": card["group"],
            })

    user_content = f"用户完成了 {total_rounds} 轮图片选择测试。\n\n"
    user_content += "以下是用户选择的所有卡牌的完整元数据：\n\n"

    for i, card in enumerate(card_details, 1):
        user_content += f"--- 第 {card['round']} 轮选择 #{i} ---\n"
        user_content += f"卡牌ID: {card['card_id']}\n"
        user_content += f"人格坐标: E_I={card['coords']['E_I']}, S_N={card['coords']['S_N']}, T_F={card['coords']['T_F']}, J_P={card['coords']['J_P']}\n"
        user_content += f"特质: {', '.join(card['traits'])}\n"
        user_content += f"意象: {', '.join(card['motifs'])}\n"
        user_content += f"场景: {card['scene']}\n"
        user_content += f"理由: {card['rationale']}\n"
        user_content += f"类型: {card['type']} ({card['group']})\n\n"

    if round_durations:
        user_content += "每轮停留时间（毫秒）：\n"
        for rd in round_durations:
            user_content += f"  第 {rd['round']} 轮: {rd['duration_ms']}ms\n"
        user_content += "\n"

    if deselection_events:
        user_content += "取消选择事件：\n"
        for de in deselection_events:
            user_content += f"  第 {de['round']} 轮取消: {de['id']}\n"
        user_content += "\n"

    user_content += "请根据以上所有信息，生成一份完整的性格分析报告，包括决策方式、关系模式、压力反应、盲区和建议。"

    # Determine max_tokens based on round count to match optimized version
    if total_rounds <= 5:
        max_tokens = 360
    elif total_rounds <= 20:
        max_tokens = 650
    else:
        max_tokens = 850

    return {
        "system": NAIVE_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
        "max_tokens": max_tokens,
        "temperature": 0.75,
    }


def generate_scenario(scenario_id, total_rounds, num_selections):
    """Generate one benchmark scenario."""
    # Randomly pick cards across rounds
    selections = []
    round_durations = []
    deselection_events = []

    for rnd in range(1, total_rounds + 1):
        # Each round: 1-4 cards selected
        cards_this_round = min(
            random.randint(1, 4),
            num_selections - len(selections)
        )
        if cards_this_round <= 0:
            cards_this_round = 1

        available = [c for c in ALL_CARD_IDS if not any(s["id"] == c for s in selections)]
        if not available:
            available = ALL_CARD_IDS  # allow repeats if exhausted

        chosen = random.sample(available, min(cards_this_round, len(available)))
        for card_id in chosen:
            selections.append({"id": card_id, "round": rnd})

        # Random duration per round
        round_durations.append({
            "round": rnd,
            "duration_ms": random.randint(2000, 25000)
        })

        # Occasional deselection events
        if random.random() < 0.25 and chosen:
            desel_card = random.choice(ALL_CARD_IDS)
            deselection_events.append({"id": desel_card, "round": rnd})

        if len(selections) >= num_selections:
            break

    # Trim to exact selection count
    selections = selections[:num_selections]

    # ── Optimized pipeline ──
    analysis = fast_think(selections, total_rounds, round_durations, deselection_events)
    optimized_payload = route_template(analysis)

    # ── Naive pipeline ──
    naive_payload = build_naive_prompt(selections, total_rounds, round_durations, deselection_events)

    # Calculate text lengths
    optimized_text = optimized_payload["system"]
    for msg in optimized_payload["messages"]:
        optimized_text += msg["content"]

    naive_text = naive_payload["system"]
    for msg in naive_payload["messages"]:
        naive_text += msg["content"]

    return {
        "scenario_id": scenario_id,
        "total_rounds": total_rounds,
        "num_selections": len(selections),
        "num_deselections": len(deselection_events),
        "data_density": analysis["data_density"],
        "optimized_prompt": optimized_payload,
        "naive_prompt": naive_payload,
        "optimized_text_length": len(optimized_text),
        "naive_text_length": len(naive_text),
    }


def main():
    # ── Define 20 scenarios spanning sparse, dense, and overflow modes ──
    scenario_configs = [
        # Sparse mode (1-5 rounds)
        (1, 1),   # minimal: 1 round, 1 card
        (2, 2),   # 2 rounds, 2 cards
        (3, 3),   # 3 rounds, 3 cards
        (3, 5),   # 3 rounds, 5 cards
        (5, 6),   # 5 rounds, 6 cards
        (5, 10),  # 5 rounds, 10 cards
        # Dense mode (6-20 rounds)
        (7, 8),
        (8, 12),
        (10, 15),
        (10, 20),
        (12, 18),
        (15, 25),
        (15, 30),
        (18, 35),
        (20, 40),
        # Overflow mode (>20 rounds)
        (22, 40),
        (25, 50),
        (25, 60),
        (30, 65),
        (30, 80),
    ]

    scenarios = []
    for i, (rounds, selections) in enumerate(scenario_configs, 1):
        print(f"Generating scenario {i:2d}: {rounds:2d} rounds, {selections:2d} selections...")
        scenario = generate_scenario(i, rounds, selections)
        scenarios.append(scenario)

    # ── Summary statistics ──
    print(f"\n{'='*60}")
    print(f"{'Scenario':>10} {'Rounds':>7} {'Cards':>6} {'Mode':>15} {'Opt.Len':>8} {'Naive Len':>10} {'Ratio':>7}")
    print(f"{'-'*60}")
    for s in scenarios:
        ratio = s["naive_text_length"] / max(s["optimized_text_length"], 1)
        print(f"{s['scenario_id']:>10} {s['total_rounds']:>7} {s['num_selections']:>6} "
              f"{s['data_density']:>15} {s['optimized_text_length']:>8} "
              f"{s['naive_text_length']:>10} {ratio:>6.1f}x")

    avg_opt = sum(s["optimized_text_length"] for s in scenarios) / len(scenarios)
    avg_naive = sum(s["naive_text_length"] for s in scenarios) / len(scenarios)
    print(f"{'-'*60}")
    print(f"{'AVERAGE':>10} {'':>7} {'':>6} {'':>15} {avg_opt:>8.0f} {avg_naive:>10.0f} {avg_naive/avg_opt:>6.1f}x")

    # ── Save to file ──
    output_path = os.path.join(os.path.dirname(__file__), "benchmark_prompts.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Saved {len(scenarios)} scenarios to {output_path}")


if __name__ == "__main__":
    main()
