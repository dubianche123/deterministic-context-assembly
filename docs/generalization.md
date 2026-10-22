# New-material generalization

This work reserves a genuinely new image batch and makes the existing pixel feature map usable on it. It is separate from the prediction-evaluation tooling and simulator work. A working feature pipeline is an engineering result; human preference generalization still requires human choices.

The frozen protocol is [`benchmarks/generalization/v1/protocol.json`](../benchmarks/generalization/v1/protocol.json), bound as `inputs.protocol.sha256` in the test-pack manifest. For v1, the design and exact prompts were written before generation dispatch; the final consolidated protocol and its hash were sealed while the first batch was pending. That consolidation used the already-written prompts, without inspecting generated results. It is therefore not a claim that the final protocol hash was registered before the first request. See the [execution notes](../benchmarks/generalization/v1/execution-notes.md) for the timing audit. A later amendment gets a new protocol identity; preserve this version and its original source references.

## Three distinct tests

| Test | What is held out | What it can establish |
| --- | --- | --- |
| Existing-card holdout | Some of the original 256 cards | Transfer within the original asset distribution under the evaluator's split. |
| New-material holdout | New generated depictions, built later without old images as inputs | Transfer to this declared new batch, once real choices are collected. |
| New-domain test | For example, actual products with price and shopping intent | A separate application and target; this image pack cannot establish it. |

New pixels do not automatically mean a new semantic category. Courtyards or workshops can resemble themes in the original deck. The pack therefore claims new depictions and generation provenance, not unseen concepts, natural-image representativeness or independent source-model training data.

## The reserved deck

The v1 design has **16 images: eight content families crossed with two rendering styles**. Each family appears once as an editorial illustration and once as a naturalistic photographic rendering. Both are generated illustrations; “photographic” describes rendering style, not a photograph of a real event.

The families are courtyard, market stall, craft workshop, reading corner, coastal path, transit waiting area, a table set for a shared meal and small garden. The exact content and style instructions are frozen in `prompts.json` and bound by the protocol. They contain no personality type, four-axis coordinate or archetype-to-scene rule. Every scene excludes people and an imposed emotional narrative; preferences about human subjects remain outside this pack's scope.

IDs are `HOLDOUT_0001`–`HOLDOUT_0016`, ordered by content family then style. They do not become production `NORN_*` IDs. Four generation batches each contain two families in both styles, so style is not assigned to a separate batch. Each image gets an independent generation call without reference images. A contact sheet or augmentation of an old card cannot substitute for a design cell.

The matching is by requested content, not an assertion that the generator preserved every object or composition perfectly. Visible deviations and execution details are recorded in [execution-notes.md](../benchmarks/generalization/v1/execution-notes.md). Technical failures may be regenerated under the protocol's attempt limit. Model scores, attractive rankings and later human choices may never decide which images are kept.

## Freeze before looking at holdout outcomes

The original production cards, catalogs and replay contracts remain unchanged. Record the protocol-pinned source hashes, then fit the pixel transformation using **only the original 256 cards**:

1. Decode an image into the existing 64 pixel descriptors.
2. Apply the original training-only normalization, including its declared group normalization and rounding convention.
3. Apply the full frozen 64-to-8 principal-component map and its original output scales.
4. Store the eight unscaled coordinates. The candidate's `0.5` pixel scale is applied once when the candidate builds its features.

The current choice catalog stores only three largest loadings per component, so those explanatory labels alone cannot project a new image. [`norn/feature_projection.py`](../norn/feature_projection.py) supplies a separate, hash-checked full transformation. It does not alter `choice_features.json` or silently refit the catalog. NumPy is a build-time dependency for freezing; applying a frozen map uses the standard library. Pixel extraction needs Pillow.

The companion transform must retain enough source identity and arithmetic detail to reproduce the original eight pixel coordinates within **1e-8 maximum absolute error**. This is a numerical compatibility check, not a new profile-hash contract. Holdout images never change means, scales, principal directions, component signs, vocabulary, regularization or candidate settings. Outlying coordinates are retained and reported rather than clipped using test-set statistics.

The old preference normalizer allocates fixed normalized energy to its nonempty trait and motif groups. The frozen pixel map retains that original normalization constant solely to reproduce the old pixel coordinates. It does not create trait or motif annotations for a new image. The resulting eight coordinates alone cannot supply the four missing axes of the current hybrid model.

## Missing annotations are real missing information

The new pack starts with **no four-axis, type or motif annotations**. It does not create neutral-looking zero axes, infer a type from the same pixels being evaluated, or use test choices to author labels.

| Candidate | New-pack support |
| --- | --- |
| Uniform | No item annotations required. |
| Stop-only | May transfer the visitor's training-only stopping tendency. |
| Pixel-only | Can use the frozen pixel map with the evaluator's declared feature dimensions. |
| Four axes, 16 types, current axis/pixel hybrid | Report unsupported until independent labels are frozen. |
| 91 shared motifs | Report unsupported until an independent annotation protocol and frozen-vocabulary coverage exist. |

Later annotations must be produced without seeing user choices or model rankings, under a separately frozen annotation protocol. Unknown motifs and unsupported candidates remain visible in coverage reporting; they are not filled in to make every candidate score every image.

## Test-pack handoff

The pack builder owns image ingestion and should supply these immutable artifacts:

- `protocol.json`: the frozen design and acceptance rules, with its consolidation timing recorded in `execution-notes.md`.
- `frozen_projection.json`: the source-only fitted transform, its content hash, source identities and compatibility error.
- `manifest.json`: every `HOLDOUT_*` ID, content/style design cell, batch, relative image path, exact bytes hash, decoded size, prompt hash and measured features. Each row retains 64 raw pixel descriptors and eight frozen coordinates. Shared provenance is bound through `inputs.generation` (receipts), `inputs.protocol.sha256`, `source_deck.extractor_sha256` and `projection.version`; those identities apply to every row. Any absent annotation is explicitly absent.
- Engineering verification: available image count, decode and finite-value checks, original-coordinate reconstruction, source/catalog immutability and unsupported-candidate behavior.
- `index.html`: a read-only, unblinded engineering gallery. It is not a human-study page and its labels should not be shown during a blind preference task.

A partial or failed generation remains a partial pack. Do not replace it with recoloured, cropped or otherwise augmented old cards and call it new material. An import tool should reject duplicate IDs, source hash mismatches, malformed dimensions, nonfinite values and attempts to overwrite an existing frozen pack.

The checked-in v1 pack is already built. Install the optional image dependency, then verify it:

```bash
python3 -m pip install '.[assets]'
python3 scripts/prepare_generalization_pack.py --check
```

For a pack with its declared prompts, receipts and images but no outputs yet, install `.[assets,eval]` and run the command without `--check`. Building freezes the transformation from the pinned original catalog and refuses existing outputs; `--check` validates without fitting a new transformation. `--pack-dir` selects a directory with the same supported v1 design. A changed design or protocol requires an explicitly supported new version. These commands do not change the production deck, call a language model, collect participant choices or deploy a Worker.

For a local preview, run `python3 -m http.server 18766 --bind 127.0.0.1 --directory benchmarks/generalization/v1` from the checkout and open `http://127.0.0.1:18766/`. The [readiness record](../benchmarks/results/generalization-readiness-v1.json) captures the completed engineering checks and browser acceptance. [Desktop](images/generalization-preview-desktop.png) and [320px English](images/generalization-preview-mobile.png) screenshots show the preview, not a participant task.

## Connect to the prediction evaluator

The prediction-evaluation workstream owns candidate fitting, metrics, simulations and statistical decision rules. It should consume this pack rather than creating a second feature-fitting implementation.

For each visitor, fit candidates from original-deck behavior and **freeze them before any new-deck choice**. The new-deck phase must precede a reading or displayed prediction, so explanatory text does not influence those choices. Use the same predeclared options for all candidates and record their actual display order, layout and visibility. Randomize or counterbalance positions under the study protocol. Do not update weights, stop bias or feature processing during this test phase.

The primary outcome reserved here is per-choice negative log-likelihood including stop, compared with a stop-only model fitted on the visitor's original-deck training choices. Aggregate and resample at the visitor level. The evaluator must freeze minimum usable coverage, sample size and interval/comparison rules before opening human outcomes. Unsupported candidates cannot win by dropping their difficult cases.

**Integration status (2026-10-21).** The pilot phase and the evaluator are connected. The page shows these images after the reveal, the choices are stored as a `norn.probe.v1` journal, and `norn records evaluate` reports them under `new_material` with this primary outcome. The coverage, sample-size and interval rules are fixed in [measurement.md](measurement.md#new-images-in-a-pilot). The page uses WebP copies (`scripts/prepare_probe_assets.py`, 384 px wide, checked against the manifest hashes). The model's pixel features remain the ones measured on the source PNGs.

At this stage, the honest completion statement is: **new assets are isolated and their pixels can be transformed under a frozen training-only map**. Until humans make choices, the pack supplies no observed predictive accuracy, no model ranking and no evidence that a reading describes a person's personality. Simulated preferences or model-judge answers cannot fill that gap.
