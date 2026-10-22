# Learning image preferences from recorded choices

Norn learns a small **personal preference model** from the actual cards displayed and selected over multiple rounds. Since scoring v4 that model is a Bayesian sequential choice model over 12 card features; profiles v2/v3 used a 979-feature pairwise ranker. Scoring v5 adds small-sample shrinkage and an evidence gate around those same features. The feature extractor is inspectable, and the language layer explains a frozen result. The local runtime and Cloudflare adapter share Python training/replay sources; the currently deployed build and current local source have separate version and acceptance records.

There is no ground-truth personality label, clinical interpretation, or claimed personality accuracy. A ranking says which pictures this fitted model scores above others under its observed comparisons. An unselected picture is not a proven dislike, and a preference score is not a probability that a person has a trait.

## Scoring v5 (local default): shrinkage and content-evidence fallback

`norn.profile.v5` / `norn.scoring.v5` use `norn.choice.guarded-mnl.v1` in `backend/choice_policy.py`. The same twelve features and STOP remain. Card-weight precision is fixed at `1.5*(1+12/max(1,n))`, where n counts completed rounds with eligible exposure; STOP precision remains .5. Each completed round is predicted using only the preceding candidate, with no within-round refit. Accumulated conditional-card log evidence must reach `log(20)` before card weights become effective; it can fall below that threshold again. Fallback has exactly zero card weights and a training-only fitted STOP.

Candidate MAP/Laplace uncertainty, effective scoring parameters, guard evidence and contributions are separately identified. Recommendations, map scores and language context obey the effective parameters. Fallback displays insufficient evidence rather than candidate ellipses, type guesses or arbitrary tied recommendations. Open rounds can update provisional candidate parameters but never advance the gate. [The fixed policy protocol](choice-policy-protocol.md) explains the narrow null assumption and threshold, and [validation](choice-policy.md) reuses every original diagnostic journal and outer split without retuning.

This is the current **local source** default; the deployed Cloudflare pilot remains scoring v4 until separately released. The code retains exact scoring/profile v1–v4 dispatch. The `hybrid` evaluator candidate and `records report` remain the explicit v4 raw reference; `hybrid_guarded` is a separate candidate in `records evaluate`. Their identities must not be conflated. Existing v4 cross-runtime evidence does not automatically validate the new v5 implementation or production cost.

## Scoring v4 (historical reference): a Bayesian sequential choice model

New profiles are `norn.profile.v4` with scoring `norn.scoring.v4`. Their learned model is `norn.choice.seq-mnl.v1` (`backend/choice_model.py`, standard library only). The 979-dimension pairwise ranker described in the later sections is unchanged and still available for `norn learn` / `norn rank` and for replaying v2/v3 profiles, but it is no longer the default.

**Why it changed.** A 2026-10-20 review found that the 979 dimensions were 64 pixel statistics, 76 trait one-hots that are a 1:1 function of each card's type, and 839 motif one-hots, 630 of which occur on a single card, so most of the vector identified individual cards. That finding stands on the feature audit. The accompanying planted-preference simulation is weaker evidence than it was first presented as ([`scripts/model_comparison_planted.py`](../scripts/model_comparison_planted.py), [results](../benchmarks/results/model-comparison-planted-v1.json); 24 rounds, 80 simulated users per cell, AUC against the planted utility):

| Simulated taste | 979-D ranker | 4-D axes | 12-D hybrid (current) |
| --- | ---: | ---: | ---: |
| type axes | 0.74 | 0.92 | 0.89 |
| pixel statistics | 0.75 | 0.58 | 0.73 |
| recurring motifs | 0.61 | 0.56 | 0.56 |
| axes + pixels | 0.73 | 0.75 | 0.78 |

- **The first row is built in.** Those users are planted on exactly the four coordinates the 4-D model uses.
- **The hybrid does not win everywhere.** It loses to the 979-D ranker on pixel and motif tastes and is close to chance on motif tastes.
- **The four axes cannot separate same-type cards.** They take only 16 values, one per type, so within a type only the eight pixel components can separate cards.
- **The comparison changes several things at once.** The 979-D ranker and v4 differ in features, training objective and stop modelling together, so the gap is not all due to fewer dimensions.
- **Choices follow the simulated model's own logit rule.** Nothing here measures real people.

v4 is a reasonable cold-start structure for a few dozen choices per person. Whether it is the right one is an open question that real choices decide, with [`norn records evaluate`](measurement.md#comparing-models-on-held-out-choices).

**Features (12 per card).**

| Feature | Dimensions | Construction |
| --- | ---: | --- |
| Type axes | 4 | The card's hand-authored E_I, S_N, T_F, J_P coordinates divided by 8. Every card has ±8, so these are ±1 and encode the card's four type letters. |
| Pixel components | 8 | First eight principal components of the 64 z-scored pixel statistics above, each scaled to unit standard deviation, then multiplied by 0.5. Together they keep about 70% of the pixel statistics' variance. |

Trait and motif annotations are not used. `backend/config/choice_features.json` stores these values with a content hash; `python3 scripts/prepare_choice_features.py` regenerates it (NumPy at generation time only) and `--check` confirms the stored file is current. At load, the axes are cross-checked against `cards_metadata.json`. Each pixel component carries a label built from its three largest loadings (for example, component 1 rises with the share of higher green-channel intensities and falls with the shares of low green-channel and very dark pixels); these labels are descriptions of pixel statistics, not of meaning.

**Labels.** For every completed round, the cards that are still selected when the round closes are taken in the order of their first click. Each is a choice among the displayed cards not yet picked plus a "stop" option, and the round ends with stop. A round with no pick is a single stop choice, which is evidence that no card beat stopping. A round where every card was picked ends with a forced stop that carries no information. Withdrawn cards are simply not picks; they never become negative labels. In `norn.events.v2` and `v3` journals, cards outside `round_completed.seen_card_ids` leave that round's choice set, as in the 979-D ranker; `null` visibility and v1 journals keep every displayed card. An open round is left out of the trajectory; a provisional preview counts its picks without a stop.

**Model.** A card's utility is `u(c) = w · x(c)`; the stop option has utility `b`. Each choice follows a multinomial logit over the remaining cards and stop. The prior is `w ~ N(0, I/1.5)` and `b ~ N(0, 1/0.5)`. The posterior mode of these 13 parameters is found by Newton's method (until every step is below 1e-9), and its covariance is the inverse of the negative Hessian there (Laplace approximation).

**Outputs.** After each round: the posterior mean, standard deviation and covariance of the four axis weights, the stop bias, a utility for all 256 cards, the top six cards, and up to four most probable "types". A type probability is the posterior probability that the four axis weights have those signs, estimated from 4,096 fixed quasi-random draws (Halton bases 2/3/5/7 with antithetic pairs), so it is deterministic. It says which card archetypes this session's picks favoured; it is not a personality measurement, and nothing here has personality labels to validate against.

**Contributions.** Each v4 profile stores `analysis.preference.contributions` (`norn.choice.contributions.v1`): the 12 posterior mean weights with their standard deviations and, for every listed card (the top six recommendations and up to eight final picks), its 12 feature values, the 12 terms `w_k × x_k`, their sum (the card's utility) and the posterior sd of that sum, `sqrt(xᵀ Cov(w) x)`. `choice_model.verify_contributions` recomputes every value, term and total to within 1e-12, and replay rebuilds the whole profile anyway. The trajectory carries a rounded copy for each round, shown as a table in X-Ray's 工程细节. The language context never receives these weights.

**Holdout.** With at least five completed rounds, the model is refitted on the earlier 80% and scored on the later 20%: mean negative log-likelihood per held-out choice, against two baselines. The uniform baseline makes every remaining card and stop equally likely. The stop-only baseline fits just the stop bias on the same training rounds, with every card weight at 0, because a model can beat uniform merely by learning how often this person stops: in 12 simulated 30-round sessions whose picks ignored every feature, the model beat uniform 12 times but the stop-only baseline only 4 times, each by at most 0.015 nats. `beats_baselines` (beating both) is the verdict the reading uses; `beats_random` and `beats_stop_only` are also reported. Fewer than three held-out choices is reported as `insufficient`. This measures this session's own held-out choices only, and a narrow win can still be noise.

**Reproduction.** `tests/fixtures/choice-model-demo.json` holds the prototype's demo journal (`prototypes/xray-v2/build_trajectory.py`, NumPy) and its full-precision posterior after every round. The standard-library implementation matches it to within 1e-6 (observed differences are about 1e-15) under both numerical contracts.

**Numerics and cost.** v4 profiles declare `norn.numeric.decimal34.v1`, so exp/log/sqrt go through the portable helper. A golden hash is pinned in `tests/test_choice_model.py`; [the scoring-v4 runtime probe](../benchmarks/results/numeric-runtime-scoring-v4.json) reproduced it, and every derived field, byte for byte under Pyodide 314.0.6. Re-run on 2026-10-21 after the stop-only baseline and contribution block changed the profile content: the new golden hash and every derived field still match byte for byte, and the installed 0.8.0 wheel gives the same hash. The per-round trajectory is a display projection: it uses native math, is rounded for transport (four decimals for means and standard deviations, three for card utilities) and is never hashed. Every round's state is a full refit warm-started from the previous one, so cost grows quadratically with rounds. On an Apple M3 with CPython 3.14 the whole trajectory took about 0.07 s for 20 rounds, 0.4 s for 80 and 2.7 s for 256; Workers timing has not been measured.

**What the reading sees.** The language context (`session_context.py`, unchanged) receives the model status, counts, the eight pixel components with their labels and the holdout result. Axis weights and type probabilities are deliberately not sent; the prompt still forbids type letters.

**API and X-Ray.** `POST /api/trajectory` accepts `{"journal": …}`, a bare events journal, or a `norn.replay.v1` export, which is verified first whatever its scoring version, and returns every round's state in one reply. `GET /api/sessions/<id>/trajectory` adds the frozen profile identity, byte metrics and call/cache usage. Neither creates a session or calls a model. The X-Ray panel fetches this once and scrubs it locally: a round slider and play button, a 16×16 tapestry of all cards (S|N left/right, T|F top/bottom, I|E columns and J|P rows inside each quadrant) lit by standardized utility, a marker with a 1.5σ ellipse on the S/N × T/F plane, four axis bars with ±1 sd bands, the round's cards with the largest moves, the top three types and six guessed cards. Versions, bytes, calls, cache and raw JSON are in one collapsed 工程细节 section. Live mode refetches only when a round closes, a reading freezes or a follow-up completes.

**No four-axis heuristic.** v4 does not run the v1–v3 four-axis calculation at all: no round-decay weights, no hesitation multiplier, no withdrawal J/P nudge, and therefore no `coords_avg`, `confidence` or `mbti_type` in the profile (the API returns them as `null` for v4). Round durations stay in the journal as recorded data and are shown in X-Ray, but they are not a scoring input. A v4 profile's `analysis.attribution` is `norn.attribution.v3`: the final selections and withdrawals with their source event IDs, and nothing else; the reading context and post-reading rating use it to cite real events. v1–v3 profiles still replay through the original code. Hesitation-duration reweighting, within-session position-bias modelling and adaptive card display were tried in the review and prototype and gave no meaningful gain, so v4 does not include them.

The sections below describe the 979-feature ranker behind profiles v2/v3.

## Features and provenance (979-D ranker, profiles v2/v3)

The committed `backend/config/preference_features.json` contains 256 card vectors with **979 dimensions**:

| Descriptor group | Dimensions | Construction |
| --- | ---: | --- |
| RGB distribution | 12 | Four intensity bins for each of red, green, and blue |
| Saturation distribution | 6 | Six normalized histogram bins |
| Brightness distribution | 6 | Rec.709-weighted sRGB brightness proxy, six bins |
| Spatial brightness | 16 | Average brightness in each cell of a 4×4 grid |
| Spatial saturation | 16 | Average saturation in each cell of a 4×4 grid |
| Edge orientation | 8 | Unsigned Sobel orientation bins, weighted by edge magnitude |
| Existing trait annotations | 76 | Stable sorted vocabulary, binary membership |
| Existing motif annotations | 839 | Stable sorted vocabulary, binary membership |

Images are decoded from the included WebP files, EXIF orientation is applied, and RGB pixels are resized to 64×64 with BOX filtering. Brightness is a readable sRGB proxy, not a physical light measurement. These are hand-defined descriptors, **not a learned image encoder**. The later ranking weights are learned from choices.

Only the first 64 continuous pixel dimensions use catalog-wide population means and standard deviations. A near-zero standard deviation uses scale 1. The trait and motif dimensions remain binary before normalization: absent annotations stay exactly zero. Each descriptor group is L2-normalized, the six visual groups receive total squared weight 1/2, traits 1/4, and motifs 1/4, then the complete vector is L2-normalized. All current cards contain 74 nonzero values. Stored means, scales, per-card group norms, and final norms allow the transformation to be reconstructed.

Personality coordinates, MBTI/type labels, group labels, rationale prose, unique scene names, and card identity are excluded from feature dimensions. Card IDs only join vectors to source images and events. Trait/motif annotations were authored for the original asset collection and can carry editorial bias; their presence is not independent evidence of human psychological meaning.

`source_assets_hash` covers actual image bytes. `source_metadata_hash` covers only the normalized trait/motif sets actually used. `feature_version` hashes the complete canonical catalog except itself, including transformation parameters, values, and the display projection. No timestamps or personal file paths enter it. The normal application reads precomputed JSON with the standard library; Pillow is needed only for explicit regeneration:

```bash
python3 -m pip install '.[assets]'
python3 scripts/prepare_preference_features.py
# Optional explicit inputs:
python3 scripts/prepare_preference_features.py --assets-dir /path/to/cards \
  --metadata /path/to/cards_metadata.json --output /tmp/preference_features.json
```

## What supplies a training label

For each completed round, the event journal establishes the displayed card set and the active selection state **at that round's close**. Each selected displayed card supplies a weak positive comparison against an unselected card displayed in the same round. A later round cannot retroactively relabel an earlier one. Canceling and reselecting the same card leaves it selected at closure; the model must not turn that interaction into a final rejection.

The normal frontend draws 4–6 cards uniformly from the remaining picture pool. Legacy personality types and coordinate labels no longer determine exposure. The recorded displayed set, rather than a guessed candidate pool, supplies the comparison boundary.

No selection or selecting every displayed card supplies **no pair**, because neither case establishes a relative winner and loser. Undisplayed cards never become negative labels. In `norn.events.v2` and `v3` journals, a displayed card that never reached the screen (not ≥50% in the viewport with its image shown before the round closed, per `round_completed.seen_card_ids`) is neither a positive nor a negative; `null` visibility, and every v1 journal, keep the historical displayed-card comparison. Old snapshot input has no display history, so it produces `no_observations` rather than invented comparison labels. Duplicate source journals are ignored. A bounded deterministic sampler limits pairs per round and overall, recording possible, sampled, and omitted counts.

Completed journals are the default. Explicit `--include-open` admits a provisional open-round snapshot; it is labeled provisional and is not used as completed holdout evidence. Nothing in the dwell time, revision count, or personality coordinates automatically becomes a target label for this model.

## Learned model and additive explanation

For a card vector `x`, the score is `s(x) = Σ w[j] × x[j]`. For a same-round pair `(selected, unselected)`, the fitted probability used by the logistic objective is `sigmoid(s(selected) − s(unselected))`. Training minimizes mean pairwise logistic loss plus `l2 × ||w||² / 2`, with deterministic batch updates and a decreasing learning rate. No provider API or external machine-learning runtime is involved.

The current model identifier is `norn.preference.linear-bt.v2`; v1 remains available for historical checkpoints. The model is linear and limited: it cannot automatically learn arbitrary nonlinear interactions or infer why a user clicked. Descriptors, regularization, presentation order, and sparse/noisy comparisons all affect the fitted ranking. More observations do not by themselves establish out-of-session validity.

Each ranked card exposes named feature value, learned weight, and their product. Summing **all** contribution terms reconstructs the score. A short list of largest terms is only a display summary; it is not the complete accounting or a causal explanation of the person's mind. Similar-weight ties and zero-information models must not be presented as certainty.

The dimension count is not a dense image encoder running on every click. Runtime reads precomputed descriptors, with **74 nonzero values per card**, and uses sparse pair differences for training. Default training is bounded to 40 epochs, at most 32 sampled pairs per round, and 4,096 pairs overall; the map has 256 fixed points. The cloud adapter runs fitting inside the Durable Object, while compact chart responses avoid transferring the full checkpoint and ledger on every slider movement. Performance measurements must identify the runtime, input size, and whether they include network or cold startup.

## Holdout and honest evaluation

Validation uses the latest approximately 20% of completed labeled rounds as a chronological holdout. Earlier training rounds that share **any displayed card** with holdout—including weak negatives—are purged. Repeated displays therefore cannot masquerade as independent held-out evidence. With multiple explicitly supplied recordings, their supplied order defines the source chronology; the system does not infer a shared real-world clock or user identity.

A separate fit on the retained training subset evaluates holdout; the final personal checkpoint can still fit all admitted observations. Repeated-display observations within each split are grouped when counting independent evidence, so repeating one display does not increase that count. Metrics include pairwise accuracy and logistic log loss, with the random/tied-score baseline **0.5 accuracy and ln(2) loss**. Ties count as half-correct. Fewer than five completed labeled rounds, fewer than three independent training groups or two holdout groups, or purging away usable data produces an `insufficient` reason. `no_observations` is distinct from poor measured performance. Pair sampling uses the admitted round facts, not future labels or a whole-recording hash.

These metrics test prediction of held-out **relative image choices**, not personality labels or general recommendation quality. Training fit is not held-out accuracy. The built-in 24-round benchmark fixture is an artificial recorded interaction with real image assets, **not a dataset collected from people**. It must not be used as evidence of real user preference accuracy. Synthetic planted-preference tests can show that the optimizer learns a known signal; high synthetic accuracy validates implementation behavior only.

The retained [0.6 / model-v1 learning report](../benchmarks/results/preference-learning-v1.json) contains that fixture, complete native-math checkpoint, summary, and all-card score reconstruction checks. It has 24 labeled rounds / 48 pairs, with 19 independent training groups and five holdout groups. Its held-out pairwise accuracy is **0.50**, equal to random, and log loss is **0.665318** versus random **0.693147**. Because accuracy does not exceed baseline, `beats_random` is false. All 256 additive scores reconstructed with zero observed error. These numbers describe that historical constructed-fixture run only; they are not new 0.7 or deployed Workers measurements.

## Portable numerical contract

Profiles `norn.profile.v4` and `norn.profile.v3`, scoring `norn.scoring.v4` and `norn.scoring.v3`, model `norn.preference.linear-bt.v2`, and historical-coordinate attribution `norn.attribution.v2` declare `norn.numeric.decimal34.v1`. The native/Pyodide probe results cited below predate v4. The attribution remains historical coordinate bookkeeping, separate from the learned model's additive score terms.

Native CPython and WASM Python can return slightly different last bits for `exp`, `log`, `log1p`, and `sqrt`. A tiny weight difference is enough to change a canonical profile hash. The portable helper converts each binary64 input exactly with `Decimal.from_float`, performs the operation in a fresh explicit 34-digit `ROUND_HALF_EVEN` context, then converts once to binary64. Ordinary float arithmetic and `math.fsum` retain their existing behavior. Context-local dispatch isolates concurrent requests from one another and from ambient Decimal settings.

This is a versioned numerical change, not a relaxed equality check. New profiles and checkpoints record the contract, and source verification still recomputes and compares the entire result. The [native-versus-Pyodide release probe](../benchmarks/results/numeric-runtime-release-0.7.0.json) covers a complete 24-round profile, checkpoint, frozen prompt hashes, edge round-decay inputs, and a 256-round/4,096-pair pressure fit with exact derived results. The [public Workers acceptance run](../benchmarks/results/cloudflare-public-smoke-v1.json) also exports a real 24-round profile after three follow-ups and strictly verifies it on native CPython. The [redeployment recovery run](../benchmarks/results/cloudflare-public-resume-v1.json) preserves that frozen export, learned checkpoint, and retry receipts across a deployment change. The [earlier portability investigation](../benchmarks/results/numeric-portability-v1.json) retains the original native-libm differences that motivated this contract. Local probe timings and WASM heap sizes do not establish deployed Worker CPU or total memory.

Previously exported profile/scoring v1 and v2, attribution v1, and model v1 continue using `norn.numeric.native-libm.v1`. They are never silently migrated to Decimal math. A historical export whose native calculations differ on another platform fails with an explicit compatible-runtime error. Its source journal can be replayed into a new v3 profile, but that produces a newly versioned result and does not authenticate or replace the old hash.

## Checkpoints, replay, and explicit personal accumulation

The browser's normal settlement trains from its current journal only. It does not silently merge visitors, persist a cross-user model, or search other projects. Local execution keeps requests in the user's own Python process and defaults to key-free mock prose. The hosted demo sends the journal to its same-origin Cloudflare API and stores temporary reading/history in a browser-isolated SQLite actor. The 0.8 hosted renderer sends frozen profile evidence and conversation messages to official DeepSeek for live prose, using a server-held encrypted key and durable paid-attempt limits; the key never enters the profile or replay export. [Live API acceptance](../benchmarks/results/cloudflare-deepseek-live-v1.json) verifies a real reading plus three follow-ups and strict native replay of the unchanged learned export. The 0.7 mock deployment reports remain historical. Both paths freeze the same learned result with the rest of the profile. Explicit local commands can train several recordings that the user has deliberately selected as belonging to the same person:

```bash
python3 -m norn learn tests/fixtures/replay-session.json \
  --output /tmp/my-norn-preferences.json --json
python3 -m norn rank /tmp/my-norn-preferences.json --top 8 --json
# Verify training against the supplied source recording as well:
python3 -m norn rank /tmp/my-norn-preferences.json \
  --recording tests/fixtures/replay-session.json --top 8 --json
```

The three-round fixture is useful for a smoke run and is too small for sufficient holdout validation. To opt into accumulation, pass more than one recording to `learn` in the intended order. Output paths must be new files. Retain the original recordings if later source replay is required. These files can reveal choices and should be shared deliberately.

Checkpoints record schema/model/feature versions, the current numerical contract, fitted weights, feature names and transform identity, training configuration, source hashes, training hash, pair counts, validation, and a checkpoint hash. Without original sources, validation checks content identity, shape, feature version, and finite parameters. With `rank --recording`, the full deterministic fit is recomputed and compared. Hash consistency establishes reproducible content, not authorship or proof that a human made the choices.

Profile `norn.profile.v3` with scoring `norn.scoring.v3` (the 0.8 default) contains the learned checkpoint, summary, and numerical contract; v4 profiles contain the choice model's posterior summary instead. In v1–v3 the old four-axis calculation remains an explicitly labeled historical reference with its reconstructible ledger; it is not a feature, target, or accuracy measure for the learned model. v4 no longer computes it. Previously exported v1/v2 profiles retain their original replay semantics and native-runtime compatibility boundary. The replay envelope stays `norn.replay.v1`; the profile version inside determines its scoring contract.

## Fixed 2D picture map

The catalog also contains a deterministic 48-iteration, centered PCA projection of the same 979-D descriptors. Its axes use fixed signs and a common scale, and card coordinates remain fixed while event replay updates scores and selection state. The two axes explain approximately **23.64%** of descriptor variance. Most information is outside this view, so distances are an approximate visual aid.

The PCA axes are descriptor similarity coordinates, **not preference scores, personality dimensions, or learned psychological embeddings**. PCA points are not inputs to the ranking objective. The preference model uses the full catalog vectors. Dragging or scrubbing the interface previews the chosen event prefix and score changes rather than refitting the map's coordinates.

The X-Ray panel no longer draws this map (see "Scoring v4" above for the current panel); it remains in `/api/preview`, `/api/replay` and `/api/sessions/<id>/inspect` responses, coloured by the v4 utilities for v4 profiles. A non-winning or insufficient validation result remains labeled as preference learning that has not demonstrated improvement over random.
