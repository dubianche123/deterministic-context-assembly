# Reproducible context and session experiments

The current benchmark is independent of the archived April Bedrock experiment. It uses the production profile, context builders, and provider generation boundary. It defaults to **offline mock**, sends **zero model requests**, and needs no credentials:

```bash
python3 -m norn benchmark --output /tmp/norn-benchmark.json
# Equivalent standalone entry:
python3 -m norn.benchmark --output /tmp/norn-benchmark-standalone.json
```

Output paths must be new files. Without `--output`, the complete JSON report goes to stdout. Reports include full prompts, generated text, the replayable fixture, hashes, rule/template versions, evidence checks, request sizes, individual usage observations, errors, and aggregate comparisons. Use synthetic data or review a report before sharing it: a custom fixture and its generated conversation are included.

For the **0.6 learned preference model**, inspect the separate [learning report](../benchmarks/results/preference-learning-v1.json) and [training/evaluation design](learning.md). Its artificial 24-round fixture produces held-out accuracy 0.50, equal to random; it does not establish accuracy on people. The earlier context/proxy reports below retain their original versions and predate the learned-preference default.

## What is compared

The built-in deterministic fixture contains 24 completed rounds, 24 selected cards, six cancel/reselect actions, and explicit durations. It permits the product's full three follow-up turns. Supply another completed event journal or replay export with `--fixture PATH`; it must contain at least 21 rounds and a nonempty selection. Empty-result fixed prose is a model-free product path, outside this model-context comparison.

| Arm | Calls per repeat | Question answered |
| --- | ---: | --- |
| `raw_single` | 1 | What overhead does retaining the entire journal and displayed card metadata add to the same bounded task? |
| `bounded_single` | 1 | What does the production analysis builder actually send? |
| `legacy_summary_session` | 4 | What does the original truncated-reading and flattened-history approach retain across analysis plus three questions? |
| `frozen_native_session` | 4 | Does the production frozen profile and native role history preserve evidence throughout the same complete session? |

The raw arm deliberately keeps the same frozen task/profile and appends raw evidence. This isolates redundant input overhead; it is **not** a claim that a model inferred a profile correctly from raw events. The legacy baseline freezes the previous summary behavior: first 180 reading characters, mode/type labels, and the final four history messages flattened into a user string. Its first analysis uses the same production builder as the new session arm.

The default two repeats produce 20 generation-boundary dispatches, all simulated when offline. The first repeat is labeled `cold`, the next `warm`; more repeats are available with `--repeats 2..20`. These are experiment positions. The runner does not clear a provider cache, force a cold start, or assert a warm cache hit. Compare actual cache counters on matched records. `shared_prefixes` measures common **logical prompt** bytes separately; it is not an HTTP wire trace or evidence of cache acceptance.

## Reading offline results

Offline checks validate the profile hash, source event references, bounded selection/revision evidence, unchanged scoring snapshots, complete native history prefixes, request byte lengths, and repeated prompt hashes. The production builder bounds evidence; the full journal and coordinate ledger remain available in the report's fixture, outside the bounded model prompt.

`estimated_input_tokens` is explicitly `ceil(canonical JSON UTF-8 bytes / 3)`, matching the runtime inspector's heuristic. It is a size heuristic, **not a tokenizer result**, and is unsuitable for calculating bills or proving provider capacity. Measured token counts remain null unless actually returned by a provider. Offline cache observations are `unavailable`, and cost is null. Local Python boundary latency is recorded for inspection but says nothing about model inference latency. Mock prose is not graded for model quality or psychological validity.

## Explicit live experiments

Live runs require all of `--live`, an explicit real provider, and a positive hard `--max-calls` dispatch limit. No configuration or credentials are discovered from other projects. Set only the chosen route's environment variables as described in the README. For an explicitly configured local or remote Chat Completions endpoint:

```bash
export NORN_BASE_URL='http://127.0.0.1:8000/v1'
export NORN_MODEL='your-model-name'
# Set NORN_API_KEY privately only if that endpoint requires it.
python3 -m norn benchmark --live --provider openai-compatible \
  --max-calls 20 --repeats 2 --output /tmp/norn-live-benchmark.json
```

This example authorizes at most 20 actual dispatches and may incur model charges. A failure counts toward the cap. There are no automatic retries or fallback routes; a failed session stops that arm's repeat. The report identifies every failure and partial run. Live responses can differ, so later prompt histories may differ even with a shared frozen prefix. A fair language-quality comparison needs independent grading, repeated sessions, matched provider/model/parameters, and actual usage; this tool does not invent such results.

Every call stores raw provider usage, normalized total input/output, observed cache read/write counts, exact logical prompt hash, provider/model, versions, run/turn indices, duration, status, and retry count. Missing counters remain null, including unsupported cache accounting. An observed zero is distinct from an absent field. Cache statistics summarize only calls whose provider supplied the respective counter.

## Optional explicit price snapshot

No built-in price table is assumed. To calculate estimates, provide a dated snapshot that exactly matches the selected provider and model:

```json
{
  "provider": "openai-compatible",
  "model": "your-model-name",
  "as_of": "2026-10-20",
  "usd_per_million_tokens": {
    "input": 1.0,
    "output": 2.0,
    "cache_read": 0.1,
    "cache_write": 1.25
  }
}
```

These are **illustrative rates**, not current vendor prices. Pass `--price-snapshot PATH`. The formula is `(uncached input × input rate + output × output rate + cache read × read rate + cache write × write rate) / 1,000,000`. Normalized `input_tokens` includes cached reads and writes; original provider fields remain in `raw_usage`. If any necessary observation is absent, costs stay null rather than treating missing cache counters as zero. Provider-specific cache TTL tiers or other charges require a matching rate snapshot and may need a future pricing schema.

With a matching price snapshot, `--max-cost-usd NUMBER` optionally stops between calls once observed spending reaches the threshold. Missing usage also stops further calls under that threshold. A single request can exceed it: this is an **observed-spend threshold**, not a hard monetary reservation. Use provider-side budgets for a hard currency cap. The independent `--max-calls` bound remains mandatory.

## October 20, 2026: authorized Workbuddy live validation

The [GLM report](../benchmarks/results/workbuddy-glm-5.3-flash.json) and [DeepSeek report](../benchmarks/results/workbuddy-deepseek-v4.1-flash.json) use the built-in public fixture through the user's explicitly authorized Workbuddy mixed account pool. They preserve **prompt v2**, including the frozen legacy dialogue control. Credentials, configuration paths, and private AXIOM data are absent from these artifacts.

| Proxy-reported model | Norn dispatches | Returned statuses | Total input tokens | Total output tokens | Reported cache-read tokens | USD cost |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| `glm-5.3-flash` | 20 | 20 OK | 60,104 | 4,476 | 39,616 | Unknown |
| `deepseek-v4.1-flash` | 17 | 16 OK, 1 truncated output | 52,492 | 5,657 | 18,816 | Unknown |

These are observations returned by the local proxy, not independently verified upstream accounting. Norn made no automatic retries. A failure ends that arm's repeat, so the DeepSeek warm native-session reading failure skipped its three follow-ups: **17 calls is a partial session comparison**, not a completed 20-call run. The dispatch counts do not establish how many attempts the proxy or upstream account pool made internally. No price snapshot was supplied; the proxy's raw `credit` field is not interpreted as USD.

`reasoning_effort=off` was explicitly requested, but the response usage still reported thinking tokens. GLM reported 718 thinking tokens across 12 calls; the DeepSeek truncated reading spent 675 of its 849 reported output tokens on thinking. The adapter correctly marked that truncated response `invalid_output`, preserved usage, and did not retry it. Requesting a reasoning setting does not prove the route honored it.

The GLM single-reading pair reported 12,337 input tokens per raw request versus 2,239 per bounded request. Its complete frozen native sessions used 21,749 input tokens across eight calls, compared with 9,203 for the lossy legacy-summary arm. Retaining evidence and native history adds logical input; cache-read observations do not by themselves establish a net cost benefit. Cold/warm labels remain repeat positions: preceding calls and the shared pool were not reset into isolated cache states.

**Successful response status is not factual correctness.** Manual review found both models treating the selected evidence rows as the whole history and interpreting canceled-then-reselected cards as finally rejected. For example, GLM calls 5, 9, and 17 describe four revisions although the fixture contains six; call 15 describes moving away from cards that were reselected; calls 17–18 describe a revision-free final eight rounds although rounds 20 and 24 contain cancel/reselect events. The full native history can preserve and repeat an earlier false inference. These reports establish dispatch/usage/retention behavior and expose a prompt defect; they do **not** pass a model-quality or psychological-validity evaluation.

Those findings motivated the subsequent production prompt revision: explicitly distinguish evidence samples from totals, retain each revision's final selected state and final selection event, and reserve more output capacity for routes that ignore the requested reasoning setting. The v2 reports remain available as evidence of the defects rather than being overwritten by a later smoke run. A later production smoke is separate evidence, not a replacement controlled comparison.

## Preference prompt v4 integration smoke

The separate [learned-preference v4 smoke snapshot](../benchmarks/results/workbuddy-preference-v4-smoke.json) records 11 authorized Norn API attempts through the same local mixed account pool. GLM and DeepSeek each completed a reading plus three follow-ups successfully. Their recorded checks preserved profile, context/system prefix, export, replay, and analysis-retry idempotency; proxy usage includes observed cache counters. This is integration evidence, not a rerun of the four-arm v2 comparison or an independent quality evaluation.

Hy3 and Hy4-preview used their entire 1,400-token output allowance on proxy-reported thinking and were correctly classified `invalid_output` / `output_truncated`; Step-5-preview returned `provider_unavailable`. No failure was automatically retried. The requested reasoning setting is not guaranteed to be honored, proxy internal attempts remain unobserved, and USD cost remains unknown.

This is a snapshot taken during the v4 implementation. Human-readable edge-orientation labels were subsequently clarified to describe approximate angles rather than strength; feature vectors, model version, and feature identity were unchanged. The saved prompts, labels, hashes, and responses describe the captured execution, and must not be presented as a bit-for-bit run of the final release.

## Rerun and inspect

The report embeds `fixture` as a standard replay bundle. Save that object to a new JSON file and pass it with `--fixture` to rerun the same scoring evidence. Prompt hashes establish content identity; they do not authenticate a provider or prove what an arbitrary endpoint consumed. Timestamps and observed durations may change. Keep raw reports rather than selecting only successful or cheap requests.

These boundaries borrow useful AXIOM shortloop practices: explicit routes, immutable source evidence, dispatch-level records, all-call accounting, separate estimates and observations, and a zero-model-call protocol smoke. Norn does not import AXIOM's runtime or present its research results as Norn measurements.
