# Claude handoff compatibility review — 2026-10-21

This review follows the user's division of work: Claude completes the new-image probe; CodeX checks compatibility and tests. The original image pack and frozen protocol are preserved. No paid model requests or deployment were made during this review.

## Confirmed and repaired

The original-deck evaluator's `hybrid` uses the production source's feature matrix, sequential-choice objective, stop option, Gaussian prior and chronological split. It has a separate NumPy solver; it is the same statistical model, not the same solver implementation. The regression checks fitted parameters below `1e-9` and held-out losses to twelve decimal places. This does not establish equivalence to an old deployed bundle.

Three evaluation boundaries were repaired in `norn/evaluate.py` and `tests/test_evaluate.py`:

- Missing within-type uncertainty no longer approves a replacement candidate.
- A useful candidate that cannot yet meet the selection criteria returns `useful_candidates_inconclusive`, including the fixed reasons; it is not described as “no picture model beats stop-only.”
- Every imported original-deck journal passes `replay_events()` before choice extraction. Unknown versions, missing event IDs and broken sequences are rejected and counted by structural stage without copying raw participant data into errors.

`score(..., types=None)` now leaves the within-type count at zero while preserving the other metrics. An unlabeled test set need not invent a type. A scene-based secondary metric may use actual design groups explicitly; a scene is not a personality type.

Two collection boundaries were repaired:

- A visible tab can resume a hover when the card still matches `:hover`. It does not count hidden time or assume the old pointer remained in place.
- The default journal is now `norn.events.v4`. Each selection and withdrawal carries its actual coarse layout; v3's readiness-only layout is retained as initial layout. v1/v2/v3 remain accepted; frozen v3 journal/profile hashes are unchanged. Behavior metadata leaves model inputs and posteriors unchanged, while different raw journals have different content hashes.

The Node browser fixture also exposes the real standard `URLSearchParams` API required by the pilot route. No production workaround was added for a test-only missing API.

## Probe integration owned by Claude

CodeX's unused standalone evaluator draft was moved out of the checkout. The integration uses `norn.probe` and `norn records evaluate`; there is no competing choices format. The new probe explicitly pins `PROBE_EVENTS_SCHEMA_VERSION = "norn.events.v4"`, so changing the browser's future default cannot silently reinterpret stored probes. A small in-memory check confirmed this pin; it is resolved.

One protocol discrepancy was still present at review time: [frozen protocol](../benchmarks/generalization/v1/protocol.json) reserves **held-out `all` negative log-likelihood, including stop, against the visitor's original-training stop-only baseline**. `NEW_MATERIAL_RULE` instead bases `pixel_taste_transfers` on **`cards − uniform`**, with a separate 30-visitor gate. These answer different questions. The frozen protocol delegated coverage/sample-size/interval rules to the evaluator, so the 30-visitor gate is not itself a violation of a frozen sample-size requirement; the primary estimand and comparator are the discrepancy.

Before opening human outcomes, preserve `all − own_stop_only` as the declared primary and list cards-only loss as secondary, or write an explicit dated protocol amendment with rationale. Keep the original protocol/hash and prior artifacts intact. Do not call an unrecorded change adherence to the old protocol. No human outcomes have been used here.

Probe shipping also needs end-to-end acceptance after Claude completes it: derived catalog matches the verified pack/projection; the sixteen displayed assets and page manifest are in the installed/Worker allowlists; the main journal freezes before the probe; probe selections never enter the reading/profile; consent, skip, retry and export preserve both journals correctly. At the earlier inspection point these files were still being added, so this note does not label their unfinished state a final defect or claim acceptance.

## Verification scope

- New-material pack `--check`: passed, sixteen images; pack identity remains `sha256:7b84691608845c171768c65f5586b9021c23ec0103c34adabc38d02a086b7058`.
- Evaluation tests with NumPy: thirteen passed, including the three boundary repairs, historical journal admission and production-model agreement.
- The latest combined event/front-end/bilingual run passed **thirty-three tests and 111 subtests**, including the newly added browser-handler pilot case. An earlier intermediate run caught a missing media-element stub while that case was still being written; the later run passed. This is handler/validator coverage, not acceptance of shipped image files or a real-browser pilot.
- The newly added backend probe module passed **eight tests** with NumPy, covering derived catalog identity, journal validation, consented storage, Worker request fields and simulated evaluation. `prepare_probe_catalog.py --check` reports both derived catalogs current. Simulated choices verify the tool, not human transfer.
- Full core suite snapshot: **480 passed, fourteen optional skips, 917 subtests passed**. Tests were collected while probe work continued; this number does not cover every later-added probe test or constitute final release acceptance.
- No Worker deployment was performed. Rebuild and re-test the completed combined source before deployment. Preserve historical benchmark files; no large simulation or judge batch was rerun.

## Final resolution — 2026-10-21

The review above records its inspection-time state. Claude subsequently completed the probe; CodeX performed final acceptance and deployed Worker version `49bcc208-083b-4acf-81ca-6565d1d0619b`. The primary is now correctly **`all − own_stop_only`**, including stop, with cards-only and within-scene results secondary. The original pack/projection identities are unchanged. The 30-visitor, five-main-round and two-probe-round coverage rule was fixed before human outcomes; it is distinct from the original-deck model-selection gate.

Final checks cover invalid protocol types returning 400 before generation, exact frozen catalog/asset identity, image decoding and deterministic WebP bytes, consent and probe admission, installed-wheel/Worker allowlists, sealed main profiles, separate probe archival, equal JSON retries, and unchanged replay exports. Existing lost-archive-reply tests cover idempotent put/retry/withdraw and unresolved receipts. A new permanent paid-path regression verifies that restored actors archive only `annotations.engagement` counts, never follow-up text, and retries annotate only once.

Core verification passed **496 tests and 954 subtests**, with 19 optional skips; the extra-enabled focused suite passed 50 tests. The later packaging repair passed 40 focused tests and 14 subtests; the final annotation regression file passed all five tests. Suites overlap. The first cloud upload failed Python-module validation (10021); generating a sole self-contained `entry.py` fixed packaging and the corrected upload succeeded.

Current evidence: [installed wheel](../benchmarks/results/installed-pilot-release-20261021.json), [281 public paths](../benchmarks/results/cloudflare-pilot-assets-20261021.json), [browser layouts](../benchmarks/results/cloudflare-pilot-browser-20261021.json), [real DeepSeek API/archive acceptance](../benchmarks/results/cloudflare-pilot-live-20261021.json), and [full resource ledger](../benchmarks/results/cloudflare-pilot-resources-20261021.json). The successful API run passed 20 checks with one reading and one follow-up. Including harness diagnostics, five fresh model calls were made, no judge calls; all three synthetic records were withdrawn. No human trial or generalization conclusion is claimed. See handoff section 18 for the next-agent entry point.
