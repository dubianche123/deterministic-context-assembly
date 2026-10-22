# X-Ray: open the loom after the reveal

X-Ray is the optional explanation behind Last Card. The ordinary experience is
pictures, a sealed guess, a real reveal and an entertaining character reading.
Technical detail stays collapsed. Opening X-Ray, scrubbing a completed visit
or playing a replay makes no decision-model call.

## A reveal with an answer the loom could not see

After exploration, the server draws four pictures that were never dealt in
this game. It freezes the exploration profile and seals the local prediction
before the player picks a favourite. The reveal shows the favourite beside the
guess, with an honest hit or miss. Its probabilities, candidate IDs, frozen
history/profile hashes and salted commitment are available in the technical
details. Ties are resolved with the sealed salt; a lucky tie-break is not
personal evidence. The final favourite is a sidecar, not another training pick.

The reference baseline and effective v6 model see the same four candidates.
The baseline is the PAM∃LA reference-rater utility, not the original four-axis
heuristic. If v6 falls back to that reference, X-Ray says so and presents their
shared result honestly. Two identical outputs are not two independent pieces
of evidence. An unaccepted candidate never replaces the effective model just
to create an ML improvement.

The reading's character is an authored entertainment interpretation of real
kept pictures and the final favourite. It can have a distinctive name, tension
and invitation without claiming a validated personality classification. Its
resonance rating is separate from prediction accuracy. A model's evidence
gate qualifies a predictive claim; it does not cancel the player's character
or their share card. See [the Last Card contract](last-card.md).

## The history stays inspectable

The completed-round replay compares the **reference** and **effective v6**
predictor on the same previously displayed pictures. For round `r`, it fits
only rounds `1..r-1`, then predicts the first finally-kept picture in round `r`.
It retains every kept image for display, but several keeps are not several
chances for a hit. No-pick rounds and rounds with fewer than two eligible
pictures are unscored. The live historical recap also requires a unique top
picture and a prediction returned before any interaction. A later replay
cannot retroactively earn live credit.

These historical scores have a different target from the single-choice finale;
their counts, labels and denominators stay separate. Candidate probabilities
are conditional on choosing a picture in that set. They do not predict the
complete multi-selection outcome, STOP or departure. The exposure set is
retrospectively established, not proof of first-click visibility. One game is
not a population accuracy estimate or a causal recommendation-benefit result.

The reference, sensitivity and personal-deviation layers describe one model's
evidence gate. The original circle was an uncertainty ellipse on the old
four-axis deck. The whole-deck image constellation is now a secondary view;
it ranks intrinsic picture scores, while a round comparison ranks its actual
candidates. Neither should be labelled a prediction of a target it did not see.

## Pacing is advice, not a learning certificate

Last Card has no fixed five-round threshold. The player may turn the final
cards after one completed exploration round. After each completed round,
`norn.pacing.v1` advises continuing or offers the finale from the history then
available. A passed predictive gate can trigger an offer; otherwise randomized
offers explore possible stopping points. The recorded trace includes the
reason, probability and reproducible draw. Twelve rounds is a product/resource
ceiling.

The first policy is untrained. It must not announce that it has learned enough
about a person. X-Ray can expose the trace and actual number of completed
rounds without treating a voluntary decision to stop as a randomized outcome.
Old research protocols and model locks remain unchanged.

## Jev makes one optional finale guess

Where a server-held key and capacity are available, Last Card enables Jev by
default. The pre-entry control explains the Typesafe request and lets the
player turn it off. It locks on entry. Collection consent and the independent
round-feedback switch do not change that choice. Jev receives earlier choices
and numerical candidate features, not image pixels, the final favourite,
future answers or the character reading. The context-free deck is supported;
a deck with a position layer is refused until the comparison contract supports
it. The optional DeepSeek call writes prose and has a different task.

There are **zero Jev requests during exploration**, and at most one finale
attempt per game. Its prediction must settle and be sealed before the player
chooses to earn a finale hit. A late, failed or unavailable prediction has no
hit; it is not scored as zero loss or fabricated into a third answer. Missing
coverage and a lucky win do not establish a general Jev advantage. Inspecting
a cached result or replaying X-Ray never dispatches another request.

The pinned model is `jev-1.13.0`. The existing limits remain: a 15-second total
deadline, zero automatic retries, at most eight attempts per browser actor
across its absolutely expiring 30-minute capabilities, and a global bucket of
100 attempts per UTC day with two concurrent calls. A durable pending receipt
precedes external I/O; an unknown outcome is not automatically dispatched
again. Quota or provider failures leave ordinary play, the local guess and the
reading available. The finale's consented sidecar can retain its settled Jev
status and prediction; temporary admission receipts remain actor state.

Exploration feedback has its own pre-entry switch, off by default. When enabled,
`norn.experience.v1` records the setting and genuinely visible exposures outside
the frozen journal/profile. Missing historical metadata is not confirmed-off.
Feedback-enabled records have a separate cohort. A private game receives the
same reveal, reading, questions and share card without creating a research
record. See [measurement](measurement.md) for the analysis boundary.

### Offline comparisons remain available

An owner can select saved journals and explicitly request a Jev batch with the
comparison CLI. Only the training prefix and each held-out candidate set enter
the request; targets, later choices, readings and the final profile do not.
Preserve model identity, request/input hashes, cutoff, response status and
usage. Do not automatically retry an unknown paid outcome or fabricate scores
for failures.

An imported offline report replaces the complete comparison view. Its baseline
and v6 lanes use the same fixed prefix as Jev; they are not overlaid on a live,
round-by-round updating model. The page identifies the cutoff and verifies
journal, deck and candidate identities. Import makes no inference call.
Integrity checks bind a report to data; they do not authenticate that a remote
request occurred. Historical data cannot recover what an unqueried provider
would have returned, or how a live opponent would change behaviour.

## Picture count and layout

Current Last Card exploration and finale each show four pictures. Older visits
may have dealt four, five or six. A device changes layout, not the current
candidate count. The existing exploration journal distinguishes:

| Quantity | Source | Limit |
| --- | --- | --- |
| Planned count | `round_started.card_ids.length` | Dealt, not necessarily seen |
| Loaded at readiness | Planned minus `round_ready.unloaded_card_ids.length` | A snapshot; a timed-out image can load later |
| Ever seen in a round | `round_completed.seen_card_ids.length` | Cumulative ≥50% viewport exposure; null is unmeasured |
| Ready layout | `round_ready.layout`, aligned with card IDs | Coarse rows/columns at readiness |
| Layout at a choice | Selection/deselection `layout` | Not every intermediate resize |
| Initial simultaneous exposure | Not separately recorded | Not recoverable from cumulative exposure |

The finale separately records choice time and, where available, its readiness,
hidden time, coarse layout and input method. It does not invent exploration
events. The dormant population slot layer is not enabled on the shipped deck;
current visits fit neither position coefficients nor a separate option-count,
fatigue or departure effect. Recording a variable and learning its effect are
different stages. A future count-adaptation study needs a versioned assignment
rule and probability, first-screen exposure and relevant layout changes.

Last Card's `product`, `pacing` and `finale` appendices stay outside the frozen
journal/profile. Contributed records use `<source>.last-card.v1`, with a separate
`.round-feedback.v1` suffix when enabled. The old frozen visitor-analysis reader
excludes those cohorts; it is not silently reused as an analysis of this game.
Current interfaces are documented in [the comparison guide](fast-thinker-comparison.md)
and [the deck contract](research-deck.md).

## Historical engineering acceptance — 2026-10-22

Release `3b7045f9-c186-441f-aca5-9fb87e984040` shipped checkpoints, a portrait,
guessing recaps and optional live Jev. Its [archive acceptance](../benchmarks/results/norn-active-round-live-20261022.json)
passed 28 checks with zero model calls and confirmed cleanup. Separate bounded
checks also succeeded with one real Jev and one DeepSeek call; they are not part
of that zero-model archive run. The subsequent default-off feedback opt-in and
mobile feedback layout revision is deployed (`dbce9b2d-f3be-40cf-81fc-4357b2386909`).
Older reports below retain their original release scopes.

The main site release `1d17277b-db82-4345-91d6-df12ec08df04` passed
[23 pure-interface checks](../benchmarks/results/norn-xray-comparison-live-20261022.json),
[changed asset hashes](../benchmarks/results/norn-xray-assets-20261022.json) and
[actual bilingual browser checks](../benchmarks/results/norn-xray-browser-20261022.json).
The exact [synthetic fixture and offline report](../benchmarks/results/norn-xray-comparison-fixture-20261022/README.md)
are retained for reproduction. None of these checks called a prose or decision
API, and hosted tests did not write a learning record. The full regression run
passed 690 tests and 1,258 subtests with 19 skips; the later verifier safety run
passed two tests and seven subtests. These checks establish integration, not
human preference or recommendation benefit.

Final opt-in revision: `dbce9b2d-f3be-40cf-81fc-4357b2386909`. [Hosted archive acceptance](../benchmarks/results/norn-partial-visit-live-v4-20261022.json) passed 37 checks with no model calls; [browser checks](../benchmarks/results/norn-play1-browser-20261022.json) record actual Jev/DeepSeek integration separately.
