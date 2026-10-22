# V1 execution notes — 2026-10-21

The pack is complete as an engineering fixture. Human new-image predictive accuracy remains unmeasured. This file records execution facts and deviations without changing the frozen protocol or pack identity.

## Generation and timing

- Sixteen independent built-in `image_gen` calls produced sixteen PNGs, one attempt per asset. No old-card references, personality labels, model scores or user choices were supplied. Exact prompts are in [prompts.json](prompts.json); their file SHA-256 is `b72ef4baeaea1102fc1d60169adb48fd80ce19d1e4afac7a7a16b0e3f3120db5`.
- The scene/style design and exact prompts existed before the first dispatch. The final consolidated protocol and its hash were sealed while the first four requests were pending. Consolidation aligned the protocol with the existing prompts, including IDs, no-people constraints and independent calls. The protocol author did not inspect generated images, features, scores or choices during that consolidation. **The final protocol hash was not strictly registered before dispatch.** This is a recorded departure from `protocol.integrity.write_policy`, not a retrospective preregistration claim.
- The first four calls were dispatched together; the remaining twelve were dispatched together. `batch_1`–`batch_4` are the predeclared balanced design groups, each with two scenes and both styles. They do not represent four strictly serial transport windows or known independent generator versions.
- [generation.json](generation.json) binds every asset to its prompt and byte hash. `generated_at` records when the tool result was copied into this pack, not the provider's internal generation time. The tool did not expose a model revision, seed, token accounting or monetary cost; those are unknown.
- No retries, rerolls, crops, recolours or aesthetic selection were used. All sixteen outputs were retained. Originals remain in the generator's output directory; the checked-in PNGs are byte-identical copies.
- The **16 image-generation calls** are distinct from the importer/checker's `model_calls: 0`. No text-generation, judge or Workbuddy API requests were made for this work.

## Visual inspection and scope

All files decode as portrait 1086 × 1448 PNGs, with sixteen unique byte hashes. Each shows its requested scene without people or a conspicuous readable logo/text. The eight scene families are present in both requested rendering styles. All PNGs total 46,993,520 bytes and stay outside the production asset bundle.

Scene labels describe requested content. They are not independently annotated psychological features. Paired outputs vary in viewpoint, object placement, vegetation, light and food contents; they are not exact scene replicas with only style changed. In particular, the transit pair varies in plant size/placement and visible train position; the meal pair includes central serving dishes beyond the four place settings. Illustration and photographic appearance are not objectively calibrated and may overlap. These variations are retained, not corrected according to scores. A future report cannot interpret a pair's difference as a pure causal style effect.

Both styles are AI-generated. This pack tests neither preferences about human subjects nor generalization to real product photographs, prices or shopping intent. Byte uniqueness and prompt provenance establish new supplied assets, not semantic novelty, generator-training independence or authorship.

## Frozen feature pipeline

- Fit scope is exactly `NORN_0001`–`NORN_0256`; no holdout pixels or choices enter fitting. Source file and original asset hashes are checked on both build and verification.
- The artifact contains the full original pixel normalization, source PCA means/scales, all eight 64-dimensional loadings, signs and output scales. It retains the old normalizer's constant semantic-group energy only for arithmetic compatibility, without assigning new semantic labels.
- All original decoded images reproduce the original normalized 64-vector exactly at its stored precision. Their eight stored coordinates reconstruct with maximum absolute error `1.7763568394002505e-15`, below the declared `1e-8` tolerance.
- Every holdout row has 64 finite raw pixel descriptors and eight finite frozen coordinates. Output coordinates are **not** multiplied by the current candidate's `0.5` pixel scale; a compatible evaluator applies its declared scale once.
- `--check` uses the saved transformation and never refits. It checks original source identity, the full source normalizer, original-coordinate reconstruction, current image bytes, prompt receipts, every remeasured feature and the manifest identity. Regression tests include a forged normalizer and PCA parameters with recomputed hashes; a self-consistent hash alone does not establish source fidelity.
- The live 256-card catalog, source assets and historical replay identities are unchanged by this pack. `HOLDOUT_*` IDs must not enter production event replay, which currently expects the original catalog.

The pack identity is `sha256:7b84691608845c171768c65f5586b9021c23ec0103c34adabc38d02a086b7058`; the projection identity is `sha256:26ae31aedbe2befa4a74927ddc7d6a0043db7604bc3c541d399fd29eeff1eb30`.

One schema naming deviation is explicit: the frozen protocol names a flat `protocol_sha256` field, while the implemented manifest binds the same file hash at **`inputs.protocol.sha256`**. The frozen protocol is preserved; consumers use the implemented nested field. Generation receipts, extractor identity and projection identity are shared manifest-level references that apply to every row.

## Verification and next consumer

The [readiness record](../../results/generalization-readiness-v1.json) records successful build/check, 18 focused tests with Pillow/NumPy available, the complete core regression (473 passed, 13 optional skips, 912 subtests), and local browser checks. The first sandboxed full run could not bind test HTTP sockets; the same suite passed with loopback permitted. No production deployment was performed.

The bilingual gallery is read-only and unblinded. It loads all sixteen images, filters each style to eight, keeps engineering JSON collapsed, and has two columns with no horizontal overflow at 320px and 390px. It records no preferences. Screenshots document desktop and a 320px English viewport; they are not physical-device validation.

The prediction evaluator should consume `manifest.rows[].features.pixel8` and bind `pack_version` and `projection.version`. It still needs a compatible held-out trial/choice adapter; the current production session and evaluator do not automatically admit `HOLDOUT_*` IDs. Uniform, stop-only and pixel-only have the necessary item-feature support, subject to a frozen original-deck training fit and choice protocol. Four-axis, 16-type, hybrid and motif91 candidates are **unsupported** until independent annotations are frozen; do not manufacture neutral axes or derive labels from test choices.

Before human collection, freeze presentation sets, stopping rules, randomization, coverage, visitor-level comparisons and decision thresholds. Train only on original-deck behavior; freeze before new-deck choices and show no reading or prediction beforehand. Record actual IDs/order/visibility/layout, timing and revisions as facts; using those behavioral facts in a model is a separately evaluated decision. The user/Claude workstreams own participant collection and prediction evaluation. There are no human outcomes or model rankings to report here yet.
