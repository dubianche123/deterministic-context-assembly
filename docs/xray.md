# X-Ray: predictions, choices and evidence

X-Ray explains what the model could predict from the choices available at that
point. It stays closed during the ordinary experience. Opening, scrubbing or
playing a replay makes no decision-model call. A Jev switch is available before
entry, off by default and locked once the visit starts. Selecting it explicitly
allows Typesafe predictions for this visit; opening X-Ray alone does not.

Both entry options reveal a visual portrait of the pictures actually kept and
a recap of guesses fixed before the answers. Both default to hiding prediction
hints until choices are sealed. Immediate round feedback has its own switch
inside X-Ray: off by default, available only before entry and locked when the
visit starts, just like the separate Jev switch. Opting out of collection does
not switch feedback on; opting into Jev does not switch it on either. The main
entry remains **Weave your thread**. Both archival choices provide the complete
portrait and end-of-visit recap.

If a visitor explicitly enables round feedback, completed-round feedback can
be shown while playing even when they consent to collection. A separate
experience sidecar records the setting and actual visible feedback exposures;
it does not modify frozen choice events or train the preference model on
feedback messages. Feedback-enabled visits form a different experience from
the original live-v3 no-feedback protocol. New on rows use a dedicated archive
cohort excluded by that reader's existing deployment gate, while retaining raw
data. No-exposure and unknown-exposure records remain distinct; legacy missing
metadata is not confirmed-off. See [the analysis boundary](measurement.md) for
routing, the historical limitation and future feedback-study requirements.

## Honest guessing and the visual portrait

At each deal the browser copies the event prefix before readiness or choices
and requests the local forecast from that prefix. A guess earns live credit
only if it returned before any selection, deselection or round completion and
has a unique top picture. The target is the first finally-kept picture. Keeping
several pictures is not multiple chances to count a hit. Tied, unavailable or
late predictions, and rounds with no retained picture, are unscored. The recap
uses scored rounds as its denominator. A replay computed later cannot
retroactively earn live guessing credit.

The portrait uses actual finally-kept image IDs and observed visual motifs.
It is present independently of whether the evidence gate accepts a personal
model. Evidence warnings qualify model claims rather than declaring the
visitor indistinguishable or spoiling the reveal. The portrait is an account
of this visit's choices, not an MBTI classification or a personality diagnosis.
The round score is a small game result, not a population accuracy or causal
recommendation-benefit claim.

## One comparison, one set of pictures

The main view compares the **reference baseline** (`u`) and the **effective v6
model**, alongside the pictures actually kept. The baseline comes from the
reference raters of this image set; it is not the original four-axis heuristic.
The old four-axis probability adapter remains a historical comparison on its
own cards, rather than being assigned invented coordinates on PAM∃LA images.

For a completed round `r`, the live comparison uses the model after rounds
`1..r-1`, then ranks the same observed candidate set in round `r`. It never
scores that round with a model already fitted to its answer. The row records
the first finally-kept picture as its scored target and retains the full set
of kept pictures for display. No-pick rounds have no picture target and are
unscored in this comparison. Rounds with fewer than two eligible pictures are
also unscored because they offer no ranking alternative.

The displayed probabilities are **conditional on selecting a picture from that
set**. They do not include STOP, predict a complete multiple-selection outcome,
or measure whether the visitor will leave. The candidate set follows the
recorded, retrospectively established exposure set; this is a conditional
replay comparison, not a claim that first-click visibility was fully observed.
Mean prediction loss uses only scored rounds and a common denominator. A single
visit is not a population accuracy result or proof of a recommendation benefit.

The reference, sensitivity and personal-deviation layers explain **one model's
evidence gate**. They are separate from the comparison's three possible systems.
When v6 falls back to the reference, the two predictions should be identical.
An unaccepted candidate is never substituted to manufacture an ML improvement.

The old circle was an uncertainty ellipse on the original card axes. The image
constellation now supplies a secondary whole-deck view. Whole-deck ranking,
recommendations among pictures not shown yet, and predictions of a completed
round are labelled separately.

## Jev is optional and explicitly enabled before entry

The two local lanes need no API key. Jev is off by default. Its pre-entry
switch explains that earlier choices and numerical candidate features go to
Typesafe and may consume the operator's paid allowance. The switch locks when
the visit starts and does not change archive consent. The
independent feedback switch controls whether predictions are exposed while
playing; by default, either entry path waits until the final reveal. The reading's optional DeepSeek call
is a separate task.

Each live request contains all earlier completed rounds and the just-dealt
candidate set, never the current round's interactions, future targets, final
profile, reading or image pixels. Numeric options are sorted under opaque IDs;
this does not offer a slot-context prediction. The current context-free deck is
supported; a deck with a position layer is explicitly refused. Only a configured
server-held key enables the switch. Opening X-Ray, dragging the slider or
replaying a cached answer sends no new prediction request.

The Jev sidecar uses pinned `jev-1.13.0`, a 15-second total response deadline
and zero automatic retries. Each browser actor has at most eight prediction
attempts across its active, absolutely expiring 30-minute capabilities. A
separate global admission bucket permits 100 attempts per UTC day and two
concurrent calls. A durable pending receipt precedes external I/O; an unknown
outcome is not dispatched again. Quota, timeout and invalid responses leave
that lane unavailable, while the two local lanes and ordinary choices continue.
Jev receipts are temporary actor state; its live probability outputs stay in
this page and are not added to the consented learning record. The round-feedback setting
and feedback exposures are recorded separately when collection is enabled. The API's `mode: "play"`
identifies this prediction sidecar, not a claim that the visit opted out of
collection.

Live Jev results are bound to their prefix and candidate set. Three-lane loss
curves compare only shared covered rounds. A prediction that arrives after an
interaction can remain available for replay but earns no live hit credit.
Missing results are labelled unavailable or not run, never assigned zero loss.
Neither an opt-in visit nor one lucky hit establishes a general Jev advantage.

### Offline comparisons remain available

An owner can later choose saved journals and run an explicitly selected Jev
batch with the comparison CLI. Only training-prefix choices and each held-out
candidate set enter the request; held-out targets, later choices, readings and
the final profile must not enter. Preserve provider/model identity,
request/input hashes, cutoff, response status and usage. Never retry an unknown
paid outcome automatically or replace failures with fabricated scores.

An imported offline report replaces the complete comparison view. A fixed-prefix
Jev batch is compared with baseline and v6 using that same fixed prefix; it is
never overlaid on the live, round-by-round updating model. The page identifies
the cutoff and verifies journal, deck and candidate identities. A missing Jev
lane is labelled as not run, not as a zero score. Import performs no inference.
These checks establish binding and integrity, not proof that a remote call
actually occurred.

Historical data support a later, explicitly versioned benchmark. They do not
establish what an unqueried provider would have returned during the original
visit, or how live Jev would change behavior. That requires a separately planned
online treatment; this opt-in does not establish it.

## Picture count and layout

Each round currently draws 4, 5 or 6 cards, approximately equally often. The
device changes arrangement, not total card count. The existing events already
let analysis derive:

| Quantity | Source | Limit |
| --- | --- | --- |
| Planned count | `round_started.card_ids.length` | The number dealt, not necessarily seen |
| Loaded at readiness | planned minus `round_ready.unloaded_card_ids.length` | A readiness snapshot; a timed-out image can load later |
| Ever seen in this round | `round_completed.seen_card_ids.length` | Cumulative ≥50% viewport exposure; null means unmeasured |
| Ready layout | `round_ready.layout`, aligned with card IDs | Rows/columns at readiness |
| Layout at a choice | selection/deselection `layout` | Does not capture every intermediate resize |
| Initial simultaneous exposure | Not recorded separately | Cannot be reconstructed from cumulative `seen_card_ids` |

The shipped context-free v6 already normalizes competition over the cards in
its comparison set. A dormant deck-level position layer is available, but it
is not enabled, and its prediction-comparison integration remains an activation
gate (see the deck contract). Current visits do not fit position coefficients
or a separate effect of option count on effort,
fatigue, stopping or abandonment. Recording a variable and learning its effect
are different stages.

The mixed policy also changes composition with total count: 4 = 2 predicted
plus popular/unpopular contrasts; 5 adds one random card; 6 = 3 predicted,
contrasts and one random card. Count effects must therefore be distinguished
from changes in recommendation share, role, exposure and layout.

No duplicate `card_count` fact field is needed now. A future measurement
protocol should consider the count-assignment rule/probability, first-screen
exposure and coarse layout changes, particularly before adapting the number of
cards to a visitor. This is a future protocol change, not information claimed
to exist in old records. The current frozen event/model files remain unchanged.

Current interfaces and usage are documented in [the comparison guide](fast-thinker-comparison.md)
and [the deck contract](research-deck.md).


## Engineering acceptance — 2026-10-22

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
