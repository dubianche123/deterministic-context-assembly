# Independent review: Workbuddy DeepSeek 503 repair

Reviewed the frozen diagnostic v1/v2 JSON, fixed-v1 smoke JSON, focused-test JSON, and source patch before reading the author report. This is an independent model review, not human gold or statistical proof.

## Judgment

The evidence strongly supports a bounded diagnosis: on a cold CN proxy, `reasoning_effort: "off"` lacked model-capability cache data and was passed through; fetching `/v1/models` populated that state, after which the same request succeeded. The patch makes cold CN preparation use the existing static capability catalog, and its first six post-restart inference calls, with no model-list prewarm, all logged `off` to `low`, returned HTTP 200 on their only attempt, and produced exact `OK`. This is good causal triangulation for the observed cold-start failure and supports the proxy repair.

The strongest justified wording is “cold proxy effort normalization was the proximate cause of this reproduced 503/11133 incident.” The artifacts do not prove that the provider universally rejects `off`, that `low` is its only accepted replacement, or that every historical 503/11133 had this cause. The pre/post diagnostic is sequential and has one request per condition, so time, account selection, or upstream transience are not independently controlled; the cold post-fix 6/6 result and six normalization receipts materially reduce, but do not eliminate, those alternatives.

## What the evidence supports

- Diagnostic v1 is a real HTTP 503 from the proxy wrapping an upstream HTTP 400 / business code 11133. Diagnostic v2 records HTTP 200 for the same frozen payload after model discovery, with exact `OK` and 12 reasoning tokens.
- The patch directly closes the cache dependency: both realms merge remote capability data over a realm-specific static fallback. The added cold-path tests assert CN `off` to `low`, global `off` to `high`, remote metadata precedence, explicit thinking disablement, and unknown-model pass-through.
- The real fixed smoke is a transport/cold-start result: 6 requests, 6 upstream calls, 6 first-attempt HTTP 200 responses, no retries, six exact-`OK` checks, peak proxy in-flight 6, and observed per-account proxy occupancy `[2,2,2]` across three accounts.
- Treating structured HTTP 400 / code 11133 as request-scoped, returning 400, releasing the lease, and avoiding account penalties is well covered by focused local mock tests. Those tests report 48 top-level tests and 101 subtests passed on the combined source.

## Limits and semantics

- The real 6/6 smoke did not exercise the new 11133 error path; all calls returned 200. Therefore real-provider validation applies to cold effort normalization, while fail-fast 400/no-account-penalty behavior remains an offline mocked integration result.
- Proxy occupancy sampled every 50 ms proves local lease concurrency, not exact upstream execution overlap, sustained throughput, latency improvement, output quality, or account-capacity causation. Six identical synthetic prompts are not independent task evidence.
- The client requested `off`, but the effective proxy request was `low`. All successful calls retained reasoning: the fixed smoke records 98 reasoning tokens out of 110 completion tokens, and diagnostic v2 records 12. The result must not be described as disabled reasoning or faithful `off` semantics.
- The error reclassification improves representation and retry/account policy, but it cannot explain the fixed HTTP 200s: classification would turn a remaining 11133 into HTTP 400, not success.
- Across the frozen inference artifacts there were 8 generation attempts: one failed diagnostic, one successful post-model-list diagnostic, and six successful fixed calls. Recorded successful usage is 397 total tokens (273 prompt, 124 completion, including 110 reasoning). The model-list request and 44 status samples are additional non-generation operations. Price, invoice/credit interpretation, failed-request billing, CPU, and engineering cost are absent, so monetary cost and payback are unknown; diagnostic v2’s `credit: 0` is not sufficient evidence of zero cost.
- The author report’s Kimi evaluation paragraph relies on artifacts outside this frozen review set and is not independently endorsed here. Its narrower statements about the reproduced DeepSeek incident are consistent with the frozen evidence when qualified as above.

## Research and product implication

The result is sufficient to continue with the cold-start proxy fix and to treat the six-call run as a real-provider smoke. It is insufficient to change general reasoning defaults, claim cost savings, infer sustained capacity, or generalize the root cause to unrelated 503s. The mocked 11133 policy tests and real 200 smoke should remain labeled separately.

## One optional next experiment

No additional paid success call is needed for the cold-normalization claim. If real-provider evidence is required before relying on the new 11133 account-health policy, run exactly one known, frozen request that reliably bypasses normalization and elicits structured HTTP 400 / code 11133, with attempts=1 and budget=1; capture sanitized outbound parameters plus account health immediately before and after. Pass only if the proxy returns one HTTP 400 preserving 11133, performs no rotation/retry, and leaves the account healthy. Stop after that single response regardless of outcome; if no stable request can be specified in advance, do not spend the call.

## Main-agent response

Accepted the supported, incident-specific diagnosis and all stated limits. The report labels the cold normalization result as real-provider evidence and the 11133 rejection policy as mock-verified; it does not claim disabled reasoning, universal rejection of `off`, upstream overlap, sustained capacity, cost savings or prose quality. The optional live negative control is not needed to finish this bounded repair, so no further model calls were made. The separate Kimi answer is sourced directly to the historical v1/v2 prose artifacts and is not presented as endorsed by this review.
