<div align="right">
  <sub>
    <strong>English</strong> |
    <a href="README_CN.md">中文</a>
  </sub>
</div>

# The Norn Machine: Deterministic Context Assembly

## A serverless, stateless cloud-native prompt engine

**Version**: 2.0 MVP  
**Author**: Leo  
**Date**: April 2026  
**Live MVP**: [https://d3gncg0hircdt9.cloudfront.net/](https://d3gncg0hircdt9.cloudfront.net/)

The Norn Machine is a deterministic context-assembly engine wrapped in an intuitive image-selection personality-reading interface. The visible product is a ritual-like demo, but the core subject is a method: how to capture lightweight behavior, assemble bounded context, optimize prompts, and run the whole loop as a cloud-native LLM application.

The project is built around a simple engineering principle:

**Deterministic code should make the judgment. The language model should render the judgment.**

Underneath the visual interface, the frontend records only lightweight behavioral signals, the backend compresses them into a stable behavioral profile, and the model turns that bounded profile into readable language. The system does not store user sessions or raw choices in a database.

## Benchmark Evidence

The benchmark is where the architectural claim becomes measurable. We compared the production-style deterministic context assembly against a naive, unoptimized version that concatenates every selected card's raw metadata directly into the original prompt.

![Context compression benchmark](the-norn-machine/backend/benchmark/benchmark_summary.svg)

| Metric | Optimized Prompt | Original Prompt | Result |
|:--|:--:|:--:|:--|
| Average input tokens | 2,039 | 5,179 | 2.5x less input context |
| Average total latency | 5,714ms | 6,822ms | 16.2% lower end-to-end latency |
| Estimated cost, 20 calls | $0.0873 | $0.1649 | 47.0% lower cost |
| Overflow scene input tokens | 2,387 | 11,286 | 4.7x less context in long sessions |

Before analyzing the data, we define three interaction depths: **short context scenes (sparse scenes, 1-5 selections)**, **sufficient context scenes (dense scenes, 6-20 selections)**, and **excessive context scenes (overflow scenes, >20 selections)**.

In sparse scenes, the optimized prompt is not always shorter than the original prompt. Because there is very little raw metadata to send in these cases, our optimized pipeline deliberately allocates a fixed context budget toward system rules, few-shot examples, and a structured behavioral skeleton. This ensures that the model can still produce high-quality, stylistically consistent answers even with minimal input.

The true architectural advantage lies in its remarkable **token consistency**. As the interaction depth progresses into dense and overflow scenes, the original prompt grows continuously with every selection (reaching up to 15,107 tokens in our tests). The optimized prompt, however, remains tightly bounded between 1,800 and 2,400 tokens. This happens because the Fast Thinker deterministic layer compresses all card traits into a fixed-format behavioral profile *before* passing any data to the LLM. Whether the user selects 1 or 80 cards, the model receives this highly distilled profile instead of a raw historical log.

For Time To First Token (TTFT), performance is nearly identical (914ms optimized vs. 867ms original). This validates our core philosophy: the goal of this architecture is not to provide magical shortcuts for every single metric, but to offer an extremely stable scaling solution without sacrificing response times. When the input becomes non-trivial, deterministic compression effectively halts unbounded context growth and significantly reduces total end-to-end latency.

The impact on cost is even more striking. Based on our current tests, using the original prompt with an average length of 5,179 tokens results in an average total cost of $0.1649 (approx. ¥1.2 RMB) across 20 calls. By employing the deterministic compression architecture, this drops to an average of $0.0873 (approx. ¥0.6 RMB), instantly saving 47% in calling costs. In extreme overflow scenes, this advantage in consistency expands to over 80% cost savings.

## Why This Exists

Many LLM applications let the model do everything at once: infer intent, remember context, choose facts, enforce safety, and write the answer. That is flexible, but it also creates inconsistent outputs, long prompts, higher latency, and more room for hallucination.

The Norn Machine uses a narrower model role. The LLM is not asked to infer everything from raw interaction history. It receives a compact, structured profile:

- decision style
- relationship pattern
- stress response
- blind spot
- suggestion
- optional emotional and visual anchors

The model is still valuable, but its responsibility is language rendering rather than decision authority.

## Architecture

![The Norn Machine architecture](the-norn-machine/Norn-Machine.drawio.svg)

This architecture shows how the MVP is built. CloudFront is the public HTTPS entry, S3 serves the static frontend, API Gateway protects the backend with an API key and usage plan, Lambda runs deterministic context assembly, and Amazon Bedrock renders the final language output.

API Gateway exposes two request-scoped capabilities: result analysis and final dialogue. Both follow the same contract: the browser sends the current payload, Lambda compresses it into structured context, and the model receives only that bounded context rather than raw session history.

The system is stateless by design. There is no database storing player sessions and no cross-user memory. Prompt rules and backend output guardrails help prevent user data leakage risk.

## Runtime Pipeline

### 1. Frontend Interaction

Players move through image-card rounds. Each round allows single selection, multiple selection, or no selection. The frontend tracks:

- selected card IDs and round numbers
- total rounds
- round durations
- deselection events

The app also supports a zero-selection result. If a player reveals fate without selecting any card, the frontend and backend both return a fixed mystical fallback instead of hanging on model inference.

### 2. Fast Thinker

The Fast Thinker is pure deterministic Python. It is the core of the project: the LLM does not decide the user's profile; it receives a compressed profile already shaped by code.

Each card carries a four-axis coordinate vector plus curated traits, motifs, scenes, and rationales. For every selected card, Fast Thinker calculates a selection weight:

```text
selection_weight = exp(round_number / total_rounds) * hesitation_bonus
```

Later rounds therefore count more, because later choices usually reflect a sharper preference after the player has seen more of the card space. Hesitation is deliberately small: round duration is normalized inside the current session and can only move a card from `1.0x` to `1.12x`, because image loading and rendering can pollute timing data.

The final coordinate is a weighted average:

```text
final_axis_value = sum(card_axis_value * selection_weight) / sum(selection_weight)
```

Deselection is handled separately because canceling an already selected card is a stronger signal than merely taking time. The system counts deselection events, estimates a revision strength, nudges the openness/closure axis toward more revision, and adds traits such as choice review or repeated calibration into the same aggregation pass.

The same weights also aggregate traits, motifs, scenes, and rationales. Finally, the signature signal is chosen from motifs by combining frequency with cosine alignment against the final coordinate vector:

```text
signature_score = weighted_frequency * (1 + max(cosine_similarity, 0))
```

That gives the result one concrete word-level hook without letting the model turn the answer into a list of selected objects.

Current signal sources:

- **Round decay**: later rounds carry more weight because later choices tend to reflect a sharper preference.
- **Hesitation bonus**: duration is a small nudge only, because image loading and rendering can pollute timing data.
- **Deselection signal**: canceling a selected card is treated as a stronger uncertainty signal than raw hesitation time.
- **Motif aggregation**: repeated traits, motifs, scenes, and rationales are weighted alongside the coordinate calculation.
- **Signature signal**: one optional word-level motif is selected from weighted motif frequency and cosine similarity against the final coordinate vector.

The signature signal is not a separate inference layer. It is integrated into the existing aggregation pass so the architecture stays small.

### 3. Template Router

The Template Router chooses a prompt mode based on information density:

- 1-5 rounds: short, intuitive reading; one follow-up dialogue turn.
- 6-20 rounds: sharper behavioral reading; two follow-up dialogue turns.
- More than 20 rounds: reflective long-form reading; three follow-up dialogue turns.

Every prompt receives the same behavioral skeleton. The style may change, but the analytical backbone stays stable.

### 4. Slow Thinker

The Slow Thinker calls Bedrock to render the final text. The model receives compressed facts and a small number of few-shot examples. It is instructed to translate concrete image anchors into broader emotional and behavioral patterns, rather than listing the objects the player selected.

### 5. Final Dialogue

After the result appears, the dialogue panel fades in. Input is capped at 100 characters, and the number of turns is bound to the number of rounds. When the limit is reached, the system closes the loop with an in-world response instead of continuing open-ended chat.

## Frontend Experience

The frontend is a static app. The reveal scene uses a canvas particle sequence:

1. particles gather while the result is being prepared;
2. the result text appears;
3. the particle cluster dissipates after the result is visible.

This timing matters because the animation's appearance should belong to the reading, not finish silently while the API is still loading.

Music is user-gesture bound: it starts when the player begins the test, which keeps browser autoplay behavior predictable.

## Security And Privacy

- No database stores player sessions.
- The backend receives only the current request payload.
- Dialogue is capped by turn count and character length.
- API Gateway provides API-key validation and request limiting.
- Prompt rules and backend output guardrails provide an additional safety layer.
- The public frontend entry is CloudFront HTTPS; direct S3 website hosting is not the intended public route.

## Transferable Pattern

The useful pattern is not the personality-reading theme itself. It is:

**implicit behavior capture + deterministic state compression + model-rendered language**

This can transfer to:

- **Conversational commerce**: infer preference style from browsing behavior, then let the model explain deterministic product candidates.
- **Customer support**: keep workflow state in code, then let the model render bounded replies.
- **Game NPCs**: let game state and rule logic decide what is true, then let the model express it in character.
- **Personal AI profiles**: adapt presentation emphasis based on visitor behavior while keeping factual content fixed.

## Appendix: Asset Pipeline Concepts

The image pipeline is based on curated metadata rather than random prompt generation.

Each card has:

- a personality coordinate vector
- a role group
- trait keywords
- visual motifs
- a scene rationale
- human-presence policy
- style, palette, and composition fields

The image manager exists to keep those concepts consistent across the card set: it seeds curated metadata, audits duplicate prompts, tracks generated assets, and exports frontend/backend card data. The management scripts are part of the repository, but the important idea is the data contract: every image is both a visual asset and a structured semantic signal.

## Appendix: Benchmark Artifacts

The benchmark suite is kept in the repository so the numbers above can be inspected rather than accepted as a claim:

- [`generate_scenarios.py`](the-norn-machine/backend/benchmark/generate_scenarios.py): builds 20 simulated sessions across sparse, dense, and overflow interaction patterns.
- [`run_bedrock_benchmark.py`](the-norn-machine/backend/benchmark/run_bedrock_benchmark.py): calls Claude Haiku 4.5 through the Amazon Bedrock Streaming API and records token count, TTFT, total latency, and estimated cost.
- [`benchmark_prompts.json`](the-norn-machine/backend/benchmark/benchmark_prompts.json): stores the optimized and naive prompt pairs used for the benchmark.
- [`benchmark_results.json`](the-norn-machine/backend/benchmark/benchmark_results.json): stores raw API measurements and the aggregate summary.
- [`benchmark_summary.svg`](the-norn-machine/backend/benchmark/benchmark_summary.svg): visualizes the context-growth curve used in this README.

## Conclusion

The Norn Machine is a small demo, but it tests a larger architectural stance: when the judgment matters, the system should make the judgment traceable before asking the model to speak. Code compresses and constrains the world; the model makes that compressed world readable.

<p align="center"><sub>The Norn Machine: A serverless, stateless prompt engine</sub></p>
