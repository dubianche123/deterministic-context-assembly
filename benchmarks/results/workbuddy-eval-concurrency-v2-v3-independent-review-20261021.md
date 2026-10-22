# Independent review of Workbuddy concurrency smokes v2 and v3

Reviewed the frozen `workbuddy-eval-concurrency-v2.json` and `workbuddy-eval-concurrency-v3.json`, the supplied protocol, and Axiom-shortloop's `AGENTS.md` and `docs/EXPERIMENT_RESULT_REVIEW.md`. The initial judgment was formed before receiving the main agent's supplemental source/log finding below. No tests, network/model calls, or credential inspection were performed. This is a model review, not external peer review or statistical proof. The main agent should link its response to this review in the final experiment record.

## Supported conclusion

Both reports record six dispatched, single-attempt requests, client peak activity six, and an authenticated proxy status observation of aggregate in-flight six with simultaneous per-account counts `[2, 2, 2]` across three accounts. This supports the narrow claim that the local proxy reported occupying all six configured slots during these short synthetic bursts. It does not establish six concurrent upstream generations or the duration of that overlap.

The mixed v2 burst completed with all three GLM requests returning HTTP 200 and an exact-OK flag, while all three DeepSeek requests returned HTTP 503. The all-GLM v3 burst completed with six HTTP 200 responses, six exact-OK flags, and `stop` finish reasons. Both reports retain all six terminal outcomes, record no retries or cancellations, and finish with client active zero and proxy in-flight zero. Thus v3 demonstrates one successful six-request GLM transport smoke with observed proxy occupancy six; v2 does not demonstrate successful mixed-model operation. The association between DeepSeek and the three errors does not identify their cause.

## Limits and material qualification

- These are two short, sequential bursts from the same client and proxy pool, with different model composition. They are not independent service sources, repeated reliability trials, a controlled model comparison, a sustained-load test, or evidence for a production default change. The reported 2.271 s and 3.256 s wall times do not establish comparative speed or concurrency benefit; no serial control exists.
- Polling at about 50 ms yielded 38 and 59 status samples without reported polling errors. The JSON stores summary counts rather than timestamped status samples or upstream traces. It cannot independently reconstruct overlap duration, dispatch ordering, successful request-to-account assignment, or upstream execution.
- `reasoning_effort: off` describes the requested setting. Reported usage nevertheless includes 13 reasoning tokens in v2 request 5 and 127 in v3 request 5. The evidence therefore does not support claiming that reasoning was actually disabled for every request. This does not invalidate the observed proxy counts or the success flags.
- Exact-OK checks establish only compliance with the tiny synthetic instruction as reported by the harness; response text and the harness revision are not present in these artifacts. No prose-quality conclusion follows.
- All 12 dispatched attempts are represented, including the three errors. Disclosed token usage does not establish full cost: failure usage/charges, unit prices, and measurement/runtime costs are absent. Missing costs are unknown, not zero; no cost or payback conclusion follows. The six-request caps were consumed without recorded budget rejections.

## Supplemental finding and next step

After the independent judgment, the main agent reported that its read-only source/log inspection mapped all three DeepSeek 503 responses to upstream HTTP 400 / business code 11133 `invalid_request_error`, without isolating a parameter. The response bodies were not retained in these artifacts, so this review cannot independently verify that mapping. It should be attributed to the main agent's supplemental inspection, not to the JSON reports. If accurate, it further weakens any attribution of those failures to concurrency.

No further paid calls are necessary to support the narrow six-slot proxy smoke conclusion. If DeepSeek is needed for a larger evaluation, the one prioritized next experiment is a zero-model request-construction check: capture the sanitized prepared upstream payload for the same frozen prompt/settings and compare it with the route's documented or locally implemented accepted request contract. Preserve the relevant fields and contract evidence. The falsifiable question is whether an unsupported or incorrectly encoded request field is forwarded. Stop after that single capture/comparison, recording any unresolved contract gap; do not expand into real calls automatically. A future live probe would need an explicit hypothesis and bounded authorization.

There is no dissent from the bounded claim that proxy occupancy six was observed and v3 succeeded 6/6. The material qualifications are the boundary between proxy occupancy and upstream generation, and the mismatch between the requested reasoning-off setting and reported reasoning-token usage.

## Main-agent response

Accepted. The implementation and handoff report proxy-observed occupancy of six split 2/2/2, and a 6/6 successful GLM smoke; they do not claim upstream generation overlap, sustained throughput, comparative speed, model quality, cost, or fully disabled reasoning. Both frozen probes retain their failures and numeric usage. The DeepSeek upstream-error classification remains supplemental log/source evidence, with the rejected parameter unresolved. No further model calls or changes to model settings are needed for this concurrency correction.
