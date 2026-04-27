"""
Bedrock Benchmark Runner
Calls Claude Haiku 4.5 via Bedrock for both optimized and naive prompts,
measuring token counts, TTFT, total latency, and estimated costs.

Output: benchmark_results.json
"""
import json
import os
import time
import boto3
import sys

BEDROCK_MODEL_ID = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
BEDROCK_REGION = "ap-northeast-1"

# Haiku 4.5 pricing (per 1K tokens, ap-northeast-1)
# https://aws.amazon.com/bedrock/pricing/
INPUT_PRICE_PER_1K = 1.00 / 1000   # $0.001 per 1K input tokens
OUTPUT_PRICE_PER_1K = 5.00 / 1000  # $0.005 per 1K output tokens


def invoke_bedrock_with_timing(client, prompt_payload, label):
    """Invoke Bedrock and measure TTFT + total latency."""
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "system": prompt_payload["system"],
        "messages": prompt_payload["messages"],
        "max_tokens": prompt_payload["max_tokens"],
        "temperature": prompt_payload["temperature"],
    }

    body_str = json.dumps(request_body)

    # ── Streaming call for TTFT measurement ──
    t_start = time.perf_counter()

    response = client.invoke_model_with_response_stream(
        modelId=BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=body_str,
    )

    ttft = None
    chunks = []

    for event in response["body"]:
        chunk = json.loads(event["chunk"]["bytes"])
        if ttft is None and chunk.get("type") in ("content_block_delta", "content_block_start"):
            ttft = time.perf_counter() - t_start

        if chunk.get("type") == "content_block_delta":
            delta = chunk.get("delta", {})
            if "text" in delta:
                chunks.append(delta["text"])

        if chunk.get("type") == "message_delta":
            usage_out = chunk.get("usage", {})
            output_tokens = usage_out.get("output_tokens", 0)

        if chunk.get("type") == "message_start":
            usage_in = chunk.get("message", {}).get("usage", {})
            input_tokens = usage_in.get("input_tokens", 0)

    t_end = time.perf_counter()
    total_latency = t_end - t_start

    if ttft is None:
        ttft = total_latency

    output_text = "".join(chunks)

    return {
        "label": label,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "ttft_ms": round(ttft * 1000, 1),
        "total_latency_ms": round(total_latency * 1000, 1),
        "output_text_length": len(output_text),
        "prompt_text_length": len(body_str),
        "input_cost_usd": input_tokens * INPUT_PRICE_PER_1K / 1000,
        "output_cost_usd": output_tokens * OUTPUT_PRICE_PER_1K / 1000,
    }


def main():
    # ── Load scenarios ──
    scenarios_path = os.path.join(os.path.dirname(__file__), "benchmark_prompts.json")
    with open(scenarios_path, "r", encoding="utf-8") as f:
        scenarios = json.load(f)

    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)

    results = []
    total = len(scenarios)

    print(f"🚀 Running Bedrock benchmark: {total} scenarios × 2 versions = {total * 2} API calls")
    print(f"   Model: {BEDROCK_MODEL_ID}")
    print(f"   Region: {BEDROCK_REGION}")
    print(f"{'='*90}")

    for i, scenario in enumerate(scenarios, 1):
        sid = scenario["scenario_id"]
        rounds = scenario["total_rounds"]
        cards = scenario["num_selections"]
        mode = scenario["data_density"]

        print(f"\n[{i}/{total}] Scenario {sid}: {rounds} rounds, {cards} cards ({mode})")

        # ── Run optimized version ──
        print(f"  ├─ Optimized... ", end="", flush=True)
        try:
            opt_result = invoke_bedrock_with_timing(
                client, scenario["optimized_prompt"], "optimized"
            )
            print(f"✅ {opt_result['input_tokens']} in / {opt_result['output_tokens']} out, "
                  f"TTFT={opt_result['ttft_ms']:.0f}ms, Total={opt_result['total_latency_ms']:.0f}ms")
        except Exception as e:
            print(f"❌ Error: {e}")
            opt_result = {"label": "optimized", "error": str(e)}

        # Small delay to avoid throttling
        time.sleep(0.5)

        # ── Run naive version ──
        print(f"  └─ Naive...     ", end="", flush=True)
        try:
            naive_result = invoke_bedrock_with_timing(
                client, scenario["naive_prompt"], "naive"
            )
            print(f"✅ {naive_result['input_tokens']} in / {naive_result['output_tokens']} out, "
                  f"TTFT={naive_result['ttft_ms']:.0f}ms, Total={naive_result['total_latency_ms']:.0f}ms")
        except Exception as e:
            print(f"❌ Error: {e}")
            naive_result = {"label": "naive", "error": str(e)}

        results.append({
            "scenario_id": sid,
            "total_rounds": rounds,
            "num_selections": cards,
            "data_density": mode,
            "optimized_text_length": scenario["optimized_text_length"],
            "naive_text_length": scenario["naive_text_length"],
            "optimized": opt_result,
            "naive": naive_result,
        })

        time.sleep(0.5)

    # ── Compute aggregate statistics ──
    print(f"\n{'='*90}")
    print(f"📊 RESULTS SUMMARY")
    print(f"{'='*90}")

    valid = [r for r in results if "error" not in r["optimized"] and "error" not in r["naive"]]

    if not valid:
        print("❌ No valid results to summarize.")
    else:
        # Per-scenario comparison
        print(f"\n{'Scn':>4} {'Rnds':>5} {'Cards':>6} {'Mode':>15} │ "
              f"{'Opt In':>7} {'Nav In':>7} {'Ratio':>6} │ "
              f"{'Opt TTFT':>9} {'Nav TTFT':>9} │ "
              f"{'Opt Tot':>8} {'Nav Tot':>8}")
        print(f"{'─'*110}")

        sum_opt_input = sum_naive_input = 0
        sum_opt_output = sum_naive_output = 0
        sum_opt_ttft = sum_naive_ttft = 0
        sum_opt_latency = sum_naive_latency = 0
        sum_opt_cost = sum_naive_cost = 0

        for r in valid:
            o = r["optimized"]
            n = r["naive"]
            ratio = n["input_tokens"] / max(o["input_tokens"], 1)

            opt_cost = o["input_cost_usd"] + o["output_cost_usd"]
            naive_cost = n["input_cost_usd"] + n["output_cost_usd"]

            sum_opt_input += o["input_tokens"]
            sum_naive_input += n["input_tokens"]
            sum_opt_output += o["output_tokens"]
            sum_naive_output += n["output_tokens"]
            sum_opt_ttft += o["ttft_ms"]
            sum_naive_ttft += n["ttft_ms"]
            sum_opt_latency += o["total_latency_ms"]
            sum_naive_latency += n["total_latency_ms"]
            sum_opt_cost += opt_cost
            sum_naive_cost += naive_cost

            print(f"{r['scenario_id']:>4} {r['total_rounds']:>5} {r['num_selections']:>6} "
                  f"{r['data_density']:>15} │ "
                  f"{o['input_tokens']:>7} {n['input_tokens']:>7} {ratio:>5.1f}x │ "
                  f"{o['ttft_ms']:>8.0f}ms {n['ttft_ms']:>8.0f}ms │ "
                  f"{o['total_latency_ms']:>7.0f}ms {n['total_latency_ms']:>7.0f}ms")

        n = len(valid)
        print(f"{'─'*110}")
        print(f"\n📈 AGGREGATE (n={n}):")
        print(f"  Input tokens  — Optimized avg: {sum_opt_input/n:.0f}, Naive avg: {sum_naive_input/n:.0f} "
              f"({sum_naive_input/max(sum_opt_input,1):.1f}x)")
        print(f"  Output tokens — Optimized avg: {sum_opt_output/n:.0f}, Naive avg: {sum_naive_output/n:.0f}")
        print(f"  TTFT          — Optimized avg: {sum_opt_ttft/n:.0f}ms, Naive avg: {sum_naive_ttft/n:.0f}ms "
              f"(Δ {(sum_naive_ttft - sum_opt_ttft)/n:.0f}ms)")
        print(f"  Total latency — Optimized avg: {sum_opt_latency/n:.0f}ms, Naive avg: {sum_naive_latency/n:.0f}ms "
              f"(Δ {(sum_naive_latency - sum_opt_latency)/n:.0f}ms)")
        print(f"  Total cost    — Optimized: ${sum_opt_cost:.6f}, Naive: ${sum_naive_cost:.6f} "
              f"(saved {(1 - sum_opt_cost/max(sum_naive_cost, 0.000001))*100:.1f}%)")

        # ── By mode breakdown ──
        for mode in ["sparse_mode", "dense_mode", "overflow_mode"]:
            mode_results = [r for r in valid if r["data_density"] == mode]
            if not mode_results:
                continue
            mn = len(mode_results)
            avg_opt_in = sum(r["optimized"]["input_tokens"] for r in mode_results) / mn
            avg_naive_in = sum(r["naive"]["input_tokens"] for r in mode_results) / mn
            avg_opt_ttft = sum(r["optimized"]["ttft_ms"] for r in mode_results) / mn
            avg_naive_ttft = sum(r["naive"]["ttft_ms"] for r in mode_results) / mn
            print(f"\n  [{mode}] (n={mn})")
            print(f"    Input tokens — Opt: {avg_opt_in:.0f}, Naive: {avg_naive_in:.0f} ({avg_naive_in/max(avg_opt_in,1):.1f}x)")
            print(f"    TTFT         — Opt: {avg_opt_ttft:.0f}ms, Naive: {avg_naive_ttft:.0f}ms")

    # ── Save results ──
    output_path = os.path.join(os.path.dirname(__file__), "benchmark_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "model": BEDROCK_MODEL_ID,
            "region": BEDROCK_REGION,
            "total_scenarios": len(results),
            "valid_scenarios": len(valid),
            "results": results,
            "summary": {
                "avg_optimized_input_tokens": sum_opt_input / n if valid else 0,
                "avg_naive_input_tokens": sum_naive_input / n if valid else 0,
                "avg_optimized_ttft_ms": sum_opt_ttft / n if valid else 0,
                "avg_naive_ttft_ms": sum_naive_ttft / n if valid else 0,
                "avg_optimized_latency_ms": sum_opt_latency / n if valid else 0,
                "avg_naive_latency_ms": sum_naive_latency / n if valid else 0,
                "total_optimized_cost_usd": sum_opt_cost if valid else 0,
                "total_naive_cost_usd": sum_naive_cost if valid else 0,
                "input_token_ratio": sum_naive_input / max(sum_opt_input, 1) if valid else 0,
                "cost_saving_pct": (1 - sum_opt_cost / max(sum_naive_cost, 0.000001)) * 100 if valid else 0,
            },
        }, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Saved results to {output_path}")


if __name__ == "__main__":
    main()
