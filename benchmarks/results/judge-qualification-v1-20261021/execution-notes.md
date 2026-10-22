# Execution notes

The frozen baseline dispatched 24 requests: twelve per judge, max_tokens 6000,
reasoning effort omitted, attempts 1, no fallback. All 24 returned HTTP 200.
Raw judge-request checkpoints were written as each finished.

The first runner invocation had a local metadata bug after those checkpoints:
it supplied a case-kind field where `RunState.put` reserves `kind` for the record
category. This caused the report assembly invocation to fail. It did not change
prompts, responses, scores, or per-request checkpoints. The same process completed
its already planned requests; the 24-call budget was not exceeded.

The runner was corrected to store `case_kind` separately and reuse completed
qualification rows. Offline fake-transport checks exercised fresh runs, raw-only
checkpoint recovery, completed-row recovery, and the later preflight gate.
The baseline was then rebuilt with `--resume`, after verifying 24 finished request
checkpoints and zero unresolved requests. The rebuilding invocation made **zero
network/model requests**. `observed_attempt_totals` retains the original 24 calls;
`transport_this_invocation` is zero because it describes reconstruction only.

The initial max-active and overall wall time were not persisted before this local
failure. Do not interpret the rebuilding wall time as experiment duration or its
zero transport count as zero original cost. Per-request latency, actual usage,
returned model labels, raw verdict text and failures remain in the samples log
and results. Monetary costs are unknown.
