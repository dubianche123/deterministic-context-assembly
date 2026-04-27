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

Underneath the visual interface, the frontend records only lightweight behavioral signals, the backend compresses them into stable behavioral context, and the model turns that bounded context into readable language. The system does not store user sessions or raw choices in a database.

## Runtime Dataflow

![Runtime dataflow architecture](the-norn-machine/docs/c4-dataflow.svg)

This diagram separates the two kinds of computation that the project deliberately keeps apart:

- **Solid black path**: deterministic, typed data flow from the browser to API Gateway and Lambda.
- **Dashed red path**: probabilistic language rendering from Lambda to Amazon Bedrock.

The key compression point sits inside Lambda. Raw interaction history reached 15,107 input tokens in the overflow benchmark, but Fast Thinker folded that history into 2,383 bounded prompt tokens before the model saw it. That is the core claim of the system: the LLM should not be asked to carry unbounded history when code can assemble the relevant state first.

## Benchmark Evidence

The benchmark is where the architectural claim becomes measurable. We compared the production-style deterministic context assembly against a naive, unoptimized version that concatenates every selected card's raw metadata directly into the original prompt.

![Context compression benchmark](the-norn-machine/backend/benchmark/benchmark_summary.svg)

| Metric | Optimized Prompt | Original Prompt | Result |
|:--|:--:|:--:|:--|
| Average input tokens | 2,039 | 5,179 | 2.5x less input context |
| Average total latency | 5,714ms | 6,822ms | 16.2% lower end-to-end latency |
| Estimated cost, 20 calls | $0.0873 | $0.1649 | 47.0% lower cost |
| Overflow scene input tokens | 2,387 | 11,286 | 4.7x less context in long sessions |

**Overflow case study**

| Scenario | Original Prompt | Optimized Prompt | What Changed |
|:--|:--:|:--:|:--|
| 30 rounds / 80 selections | 15,107 input tokens | 2,383 input tokens | Raw card history becomes a bounded behavioral profile |
| Same scenario latency | 9,373ms | 6,179ms | End-to-end latency drops after context growth is contained |

Before analyzing the data, we define three interaction depths: **short context scenes (sparse scenes, 1-5 selections)**, **sufficient context scenes (dense scenes, 6-20 selections)**, and **excessive context scenes (overflow scenes, >20 selections)**.

In sparse scenes, the optimized prompt is not always shorter than the original prompt. Because there is very little raw metadata to send in these cases, our optimized pipeline deliberately allocates a fixed context budget toward system rules, few-shot examples, and a structured behavioral skeleton. This ensures that the model can still produce high-quality, stylistically consistent answers even with minimal input.

The true architectural advantage lies in its remarkable **token consistency**. As the interaction depth progresses into dense and overflow scenes, the original prompt grows continuously with every selection (reaching up to 15,107 tokens in our tests). The optimized prompt, however, remains tightly bounded between 1,800 and 2,400 tokens. This happens because the Fast Thinker deterministic layer compresses all card traits into a fixed-format behavioral profile *before* passing any data to the LLM. Whether the user selects 1 or 80 cards, the model receives this highly distilled profile instead of a raw historical log.

For Time To First Token (TTFT), the test data is intentionally reported rather than hidden: the optimized pipeline is slightly slower on first token (914ms optimized vs. 867ms original). TTFT is sensitive to provider scheduling, prompt structure, and single-run variance, so this project does not claim a first-token speedup. The measured win is elsewhere: bounded context growth, lower generated-output burden, lower total latency, and lower cost once the interaction becomes non-trivial.

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

| Overflow Interaction Problem | Traditional Agentic Pattern | The Norn Machine Pattern |
|:--|:--|:--|
| Who owns global logic? | The LLM must infer state, enforce rules, remember context, and write the answer in one pass. | Python owns feature extraction, routing, limits, and fallback behavior; the LLM renders bounded language. |
| What happens as history grows? | Prompt size grows with the raw log and can drift toward context overflow. | Raw selections are compressed into a fixed-format behavioral profile before model invocation. |
| Latency profile | More history usually means more input to scan and less predictable total latency. | Benchmark shows 16.2% lower average end-to-end latency across 20 Bedrock calls. |
| Rule adherence | Rules compete with long raw context inside the same model prompt. | Hard limits, fallback paths, and output guardrails are enforced outside the model where possible. |
| Failure mode | The user may see brittle model behavior or infrastructure-shaped errors. | Deterministic fallback text keeps the user experience coherent when the model path is blocked. |

## Related Work: Compiled AI

The same architectural pressure appears in recent systems research. The April 2026 arXiv paper [*Compiled AI: Deterministic Code Generation for LLM-Based Workflow Automation*](https://arxiv.org/abs/2604.05150) studies workflows where an LLM is used during a generation or compilation phase, and the deployed workflow then runs as deterministic code without further model invocation.

The paper reports that, on BFCL function-calling tasks, compiled AI reaches 96% task completion with zero execution tokens, breaks even against runtime inference at roughly 17 transactions, and reduces token usage by 57x at 1,000 transactions. Its reported latency comparison is even sharper: 4.5ms P50 for compiled execution versus 2,004ms for direct runtime LLM inference.

The Norn Machine is not a full compiled-AI system because it still calls Bedrock to render the final language. It sits in the adjacent hybrid space: deterministic code owns state compression, routing, guardrails, and fallbacks, while the model is kept as a bounded renderer. In other words, it does not reach zero-token execution, but it follows the same direction of travel: move repeatable control logic out of the runtime model call.

## Architecture

![The Norn Machine AWS topology](the-norn-machine/Norn-machine.drawio.svg)

This architecture diagram shows how the current MVP implementation is assembled. CloudFront is the public HTTPS entry, S3 serves the static frontend, API Gateway protects the backend with an API key and usage plan, Lambda runs deterministic context assembly, and Amazon Bedrock renders the final language output.

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

$$
W_i = \exp\left(\frac{r_i}{R}\right) \times \beta_{h,i}
$$

Where \(r_i\) is the round in which card \(i\) was selected, \(R\) is total rounds, and \(\beta_{h,i}\) is the capped hesitation multiplier:

$$
\beta_{h,i} = 1 + \hat{d}_{r_i} \times (1.12 - 1.00)
$$

$$
\hat{d}_r = \frac{\mathrm{clip}(d_r, 0, 45000) - d_{min}}{\max(d_{max} - d_{min}, 1)}
$$

Later rounds therefore count more, because later choices usually reflect a sharper preference after the player has seen more of the card space. Hesitation is deliberately small: round duration is normalized inside the current session and can only move a card from `1.0x` to `1.12x`, because image loading and rendering can pollute timing data.

The final coordinate is a weighted average:

$$
\bar{x}_{dim} = \frac{\sum_i W_i \cdot x_{i,dim}}{\sum_i W_i}
$$

Deselection is handled separately because canceling an already selected card is a stronger signal than merely taking time. The system counts deselection events, estimates a revision strength, nudges the openness/closure axis toward more revision, and adds traits such as choice review or repeated calibration into the same aggregation pass.

$$
S_{revision} = \min\left(\frac{D / R}{1.5}, 1\right)
$$

$$
\bar{x}_{J/P}' = \bar{x}_{J/P} - S_{revision} \times 2.4
$$

The same weights also aggregate traits, motifs, scenes, and rationales. Finally, the signature signal is chosen from motifs by combining frequency with cosine alignment against the final coordinate vector:

$$
score(m) = F_m \times \left(1 + \max(\cos(\bar{x}_m, \bar{x}), 0)\right)
$$

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

## Edge Cases And Guardrails

The system is designed to degrade through deterministic code paths before exposing model or infrastructure failure to the user.

| Edge Case | Deterministic Control Point | User-Facing Behavior |
|:--|:--|:--|
| Player selects no cards | Frontend and Lambda both recognize empty `selections` | A fixed mystical reading is returned without calling Bedrock. |
| Bedrock call fails or is blocked | `slow_thinker` catches the exception or guardrail intervention | A safe fallback reading replaces the model output. |
| Model mentions internal fields or cross-user comparison | Backend output guardrail scans banned fragments such as coordinates, confidence, and "previous player" phrasing | The output is replaced with a neutral guarded response. |
| Dialogue exceeds allowed turns | Server recalculates the turn limit from total rounds | The conversation closes with deterministic lockout text. |
| Dialogue input exceeds 100 characters | Frontend and backend both validate length | The request is rejected before model invocation. |
| Runtime API config is missing in local preview | Frontend config loader detects missing endpoint/key | The page shows an in-world unavailable-backend reading instead of hanging. |

This is the practical value of keeping the model outside the control plane. The model can fail, be blocked, or produce an unsafe phrase, but the surrounding code still owns the final boundary of the experience.

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

## Technical Outlook

The current Fast Thinker is intentionally a rule-based expert system. Its parameters are readable and inspectable: hesitation can only add a `1.12x` multiplier, deselection can only nudge the J/P axis by a capped amount, and motif selection is based on weighted frequency plus cosine alignment. That makes the system easy to audit, but it also means the feature weights come from domain judgment rather than learned evidence.

A natural next step would be a separate data-driven behavioral intent engine. With explicit consent and anonymized event streams, the same interaction layer could learn nonlinear patterns from click cadence, hover time, deselection frequency, scroll depth, comparison behavior, and downstream outcomes. The target would not be "personality reading" anymore; it would be early intent prediction before a decisive action happens.

| Current MVP | Data-Driven Successor |
|:--|:--|
| Hand-authored feature weights | Learned embeddings and calibrated feature weights |
| Stateless request payload | Consented, anonymized event dataset |
| Descriptive reading after submission | Intent prediction before the final action |
| Deterministic prompt assembly | Model-assisted intervention policy with deterministic guardrails |

This would turn the project from interpretable context assembly into a multimodal behavioral intent prediction and intervention engine. The design lesson remains the same: keep the model away from uncontrolled authority, and let code own the boundaries.

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
- [`c4-dataflow.svg`](the-norn-machine/docs/c4-dataflow.svg): visualizes deterministic and probabilistic data boundaries in the runtime dataflow.
- [`Norn-machine.drawio.svg`](the-norn-machine/Norn-machine.drawio.svg): visualizes the deployed AWS topology used by the MVP.

## Conclusion

The Norn Machine is a small demo, but it tests a larger architectural stance: when the judgment matters, the system should make the judgment traceable before asking the model to speak. Code compresses and constrains the world; the model makes that compressed world readable.

<p align="center"><sub>The Norn Machine: A serverless, stateless cloud-native prompt engine</sub></p>
