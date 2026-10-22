# Three fast thinkers, one frozen history

The current public product uses PAM∃LA/v6. Its live X-Ray compares a
reference-rater baseline with the **effective**, evidence-gated v6 predictor;
these are not the old heuristic and twelve-feature ML lanes described below.
Jev remains disabled during normal visits. Current-deck offline comparison uses
the same explicit, bounded remote opt-in rule. Imported frozen-prefix results
replace the whole X-Ray comparison rather than mixing their Jev lane with an
online predictor trained on a different history. See [the current X-Ray
contract](xray.md).

The experimental `norn compare` command compares fixed rules, personal ML, and an optional remote decision model. The ML lane switches between the unchanged v4 reference (`ml`) and the local v5 shrinkage/fallback policy (`ml_policy`, selected by default in new reports). It produces a standalone interactive page with shared candidate images, probability bars, an observed-choice marker and a loss curve that follows the step slider. Chinese/English switching is included; technical JSON is collapsed. Old reports without a policy remain readable.

The original-deck command below remains an independent local experiment. The
published Cloudflare experience now uses v6 and official DeepSeek prose. No
remote decision call is added to the ordinary visit or learning archive; an
explicitly imported offline result is a display artifact. Comparison does not
regenerate readings or alter an existing profile.

## Current PAM∃LA deck

The command detects a PAM∃LA journal or research replay export automatically:

```bash
python3 -m norn compare recording.json --train-rounds 8 \
  --output /tmp/pamela-comparison.json --html /tmp/pamela-comparison.html \
  --language zh
```

The baseline is the fixed reference-rater utility; the second lane is effective
v6 fitted on that same frozen prefix. These are not “no ML versus ML”: the
reference itself was learned offline, while v6 can adapt to this visitor.
The scored target is the first finally-kept image, conditional on choosing one;
no-pick rounds and sets with fewer than two eligible images are unscored. STOP
is not evaluated in this current-deck comparison. Candidate visibility follows
recorded cumulative exposure, so this is retrospective conditional replay.

The standalone page includes its pictures, a Chinese/English switch, shared
probability bars, actual-choice markers and a round-linked loss curve. To see
it inside X-Ray, import the journal first, then import the offline report under
Technical details. The whole comparison changes to its stated frozen cutoff;
Jev is never mixed with a rolling model trained on a different history.

Default execution reads no credential and makes no network request. To opt in
to a bounded, potentially paid Jev batch, use an explicit provider:

```bash
# Requires TYPESAFE_API_KEY; at most one batch, no automatic retries.
python3 -m norn compare recording.json --train-rounds 8 \
  --decision-provider typesafe --decision-model jev-1.13.0 \
  --max-decisions 16 --output /tmp/pamela-jev.json \
  --html /tmp/pamela-jev.html
```

Only the numerical reference/content features and frozen prior choices enter
this remote comparison. Anonymous option IDs are sorted, rather than preserving
screen order as an extra Jev-only feature. The adapter includes STOP in its wire
contract; returned probabilities are conditioned onto the cards, and zero card
mass is a failure. Invalid or failed results are not replaced. No scorable test
rounds means no remote dispatch. A signed-in account or saved key alone starts
nothing.

A research replay input supplies **validated journal facts only**: its saved
profile is neither used nor claimed verified by this command. Strict `norn
replay` retains its separate native-libm identity requirements. Imported report
hashes verify content and binding to this journal/deck/prefix, not whether a
remote provider actually issued the claimed response. A different-runtime
local-lane display check uses its documented numerical tolerance; frozen-profile
replay checks are unchanged.

The remainder of this guide describes the historical authored deck and its
independent v4/v5 comparison, including earlier recorded API experiments.

## Try it without an account

From the repository root:

```bash
python3 -m norn compare --fixture --train-rounds 8 \
  --output /tmp/norn-fast-comparison.json \
  --html /tmp/norn-fast-comparison.html
```

Open the HTML file directly. It contains its thumbnails, data, styles and script; viewing or dragging the report sends no requests. Output paths must be new. Without `--fixture`, supply an explicit complete journal or replay export:

```bash
python3 -m norn compare recording.json --train-rounds 8 \
  --output /tmp/my-fast-comparison.json --html /tmp/my-fast-comparison.html
```

No archive or credential is discovered automatically. The remote lane remains **unavailable**, with no substituted scores, when no provider is selected.

## The three systems

| Lane | Frozen predictor | Interpretation |
| --- | --- | --- |
| Fixed rules | Historical `fast_think` coordinates, computed using only training-prefix selections, known durations and prefix round count; fixed axis-dot-product softmax, temperature 1, STOP utility 0 | A **new probability adapter** around the old rule. Its probabilities are not historical confidence values or calibrated personality scores. No fitting or tuning is performed. |
| Personal ML | Unchanged scoring-v4 sequential MNL plus an independently identified scoring-v5 guarded variant: four axes and eight pixel components, plus STOP | Both use only the visitor's earlier prefix. The new policy strengthens small-sample shrinkage and requires prequential content evidence; otherwise it predicts exactly the training-only stop baseline. Candidate posterior and effective parameters are distinct. |
| Decision model | OpenAI Decisions (`gpt-6-luna`) or TypeSafe Jev (`jev-1.13.0`, with explicit optional `jev-1.14.0`) | A pretrained model conditions on the supplied history. This does not fine-tune its weights on the visitor. Provider/model identity is preserved. |

Jev itself is ML too. The distinction is fixed rules, per-visitor fitting, and a pretrained decision system. A hidden stop-only reference helps identify gains due only to knowing how often someone stops.

The rules use four axes and prefix timing; ML uses twelve numeric features. The default remote `numeric` mode sees those same twelve numeric card features and completed-choice history. These are common-history systems, not identical-input algorithms. Explicit `--decision-input semantic` also supplies the existing scene/motif descriptions, excluding personality rationale and trait labels. Any resulting gain includes that extra semantic representation and pretraining; it cannot isolate an algorithm advantage. Catalog descriptions are preserved in their original language.

## Prediction and leakage contract

- The default split freezes the first 80% of completed rounds. `--train-rounds` fixes an explicit cutoff; at least five total rounds and one training/test round are required. Nothing is fitted on the later rounds.
- **The scored target is one first finally-kept card per held-out round, or STOP when no card was kept.** Training keeps the existing sequential final-kept choices and stop observations. This is not a prediction of the raw next click; withdrawal and visibility retain their authoritative event semantics.
- Each test question contains the initial eligible visible options plus STOP. One question per round avoids leaking a target through later shrinking option sets in a shared remote batch. Raw held-out targets, timings, hover, full profiles and readings are never in the remote request. The report stores targets locally to score the answers.
- Card IDs sent remotely are opaque prefix-bound hashes, removing the synthetic fixture's sequential identifier cues. Each lane predicts the same available options. Model predictions do not update the frozen personal state with test outcomes.
- Metrics are mean NLL including STOP, cards-conditional NLL, within-type NLL where at least two comparable cards exist, tie-adjusted top-1, Brier and coverage. A `1e-12` probability floor makes finite display possible; zero-probability targets are separately counted. Invalid outputs fail explicitly and are not repaired or replaced with another model.
- These **first-choice metrics differ from the full sequential metrics in `records evaluate`**. This command is descriptive: it has no automatic population-level winner or inherited sample-size gate. A human visitor-level paired protocol and uncertainty analysis are needed before choosing a replacement.

Preparation time and prediction time are separate. Remote latency is a whole shared batch, not a fabricated per-question measurement. One fixture run does not establish p50/p95 latency, production Worker CPU or a performance improvement.

The unlabeled new-image probe is not added here: old four-axis rules cannot score it without independent annotations. Do not invent axes or fill them with zero. The existing frozen pixel-transfer evaluation remains authoritative for that pilot.

## Optional real decision services

Both adapters use only the standard library, fixed official endpoints, a bounded request/response, a maximum of one batch call, strict typed probability validation and zero automatic retries. They never use the DeepSeek key or a ChatGPT/Codex token. Set the selected provider's key outside the report, then opt in:

```bash
# Requires OPENAI_API_KEY in the process environment.
python3 -m norn compare --fixture --train-rounds 8 \
  --decision-provider openai --decision-model gpt-6-luna \
  --output /tmp/openai-fast-comparison.json --html /tmp/openai-fast-comparison.html

# Requires TYPESAFE_API_KEY instead.
python3 -m norn compare --fixture --train-rounds 8 \
  --decision-provider typesafe --decision-model jev-1.13.0 \
  --output /tmp/jev-fast-comparison.json --html /tmp/jev-fast-comparison.html
```

The [OpenAI Decisions API](https://developers.openai.com/api/docs/guides/decisions) is currently public beta, with `gpt-6-luna` and native choice probabilities. Its published base rate is US$0.10 per million input tokens, with no output charge. API billing is separate from subscription usage; the documented [ChatGPT subscription-sharing flow](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference) supports Responses, and does not establish Decisions access. This project does not use that OAuth flow. Account access, promotional credits and actual charges have not been checked.

The [TypeSafe model documentation](https://docs.typesafe.ai/models) distinguishes the stable and preview aliases; explicit Jev versions avoid silently moving between them. Its [Choice confidence](https://docs.typesafe.ai/confidence) measures the concentration of its returned distribution. Neither provider's confidence establishes prediction accuracy on Norn visitors.

### macOS private Jev launcher

The Jev key is saved and a **real `jev-1.13.0` batch succeeded** on 2026-10-21.
The earlier metadata-only check confirmed the current user's default Keychain
contains service `Norn.typesafe-api-key`; the subsequently authorized real run
read it privately through the launcher. There is no need to enter it again.

[`scripts/jev_keychain.py`](../scripts/jev_keychain.py) is a standalone local
launcher with no Axiom runtime dependency. `status` only checks metadata:

```bash
python3 scripts/jev_keychain.py status
```

Only an explicit `compare` action reads the key privately, injects
`TYPESAFE_API_KEY` into the trusted child environment and runs the existing
official adapter. The key is absent from argv, report files and launcher output.
The comparison process and its descendants can access the environment; this
launcher is not an isolation boundary. Its model is pinned to `jev-1.13.0`,
input is numerical, and the limit is one batch of at most 16 questions with no
automatic retry. JSON and HTML outputs must be new files. This example is an
explicit, potentially paid run; rerunning it makes another request:

```bash
python3 scripts/jev_keychain.py compare --fixture --train-rounds 8 \
  --max-decisions 16 --output /tmp/jev-private-comparison.json \
  --html /tmp/jev-private-comparison.html
```

Existence alone does not establish API access or credit. A sandbox metadata failure
can mean the item is inaccessible there; unlock/authorize locally instead of
automatically replacing the saved item. Credentials being ready does not
start an API batch.

## Recorded engineering evidence — 2026-10-21

The [saved offline report](../benchmarks/results/fast-thinker-comparison-fixture-20261021/index.html) uses the existing synthetic 24-round fixture, freezes after eight rounds, and scores the next sixteen first choices. Fixed-rule NLL is **1.7162** and personal-ML NLL **2.3147**; the remote model is unavailable. This is a pipeline fixture with a deterministic catalog progression and a distribution change across its cutoff, not a sampled person. Do not promote either ranking to a quality claim, or select a more flattering fixture after looking at the score.

The subsequent [offline diagnosis](fast-thinker-diagnosis.md) preserves that result. The fit converges correctly, but the fixture shifts from all-N training targets to an all-S block, and its training pixel ranking does not generalize. Full sequential test NLL is rule **1.5678**, ML **1.5739**, stop-only **1.1475**: first-choice scoring magnifies the displayed gap, and both content systems lose to baselines here. Seven predeclared simulated controls and a fixed-test learning curve show recovery under stable represented preferences, small-sample noise fitting, and limits under motif taste or preference reversal. They are engineering diagnostics, not human validation.

The [follow-up policy implementation and validation](choice-policy.md) adds shrinkage/fallback without overwriting those scores. Its original-fixture first-choice NLL is **1.6516**, exactly the declared stop-only fallback; uniform still wins at1.3863. The ML switch, probability bars and curve disclose which variant is shown. The full 840-input validation preserves all hashes and includes the new policy's slower learning on some stable-signal controls, rather than retuning the threshold to win every cell.

The [JSON report](../benchmarks/results/fast-thinker-comparison-fixture-20261021/report.json) preserves the prefix/input/feature identities, actual probabilities, timing and absence of a remote result. The adapters have mocked native-wire tests for both APIs, including refusal, malformed distributions, call limits and safe errors. The original comparison made **zero real decision-model or prose-generation calls**: TypeSafe access was unavailable then and there was no OpenAI API key. The new separately authorized Jev run below preserves that historical report.

The final shared wire-probability sum tolerance is `1e-6`; outputs are validated, never silently renormalized. The saved probabilities were rechecked under this contract without refitting. Independent review also found and fixed output-path failures being discovered after dispatch, and reused-client counters attributing an old attempt to a new report. Destinations are reserved before dispatch; failure accounting records only the current comparison's attempt delta.

## Real Jev check — 2026-10-21

After the user explicitly requested a real test, the private launcher sent
**one numeric-input batch of 16 questions** to the official endpoint, with
`jev-1.13.0` requested and returned. All 16 probability distributions passed the
strict adapter contract; coverage was 100%, with no zero-probability targets.
There were no retries or prose-generation calls. The original 24-round fixture,
eight-round training prefix, held-out targets and local probabilities are unchanged.

| Predictor | Held-out first-choice NLL, lower is better |
| --- | ---: |
| Uniform reference (four options) | 1.3863 |
| Guarded ML, exactly stop-only here | 1.6516 |
| Fixed rules | 1.7162 |
| **Jev 1.13.0, real API** | **1.9221** |
| Original personal ML | 2.3147 |

Jev's top-1 was 3/16 (18.75%). Its whole batch took **8,607.681 ms**;
this is not single-question latency and cannot be compared to local prediction
time as though it were. Reported usage was **21,856 input / 1,725 output tokens**.
Actual monetary cost, cache savings and remaining credit were not inspected.

Jev improves on original ML on this fixture but loses to fixed rules and the
guarded policy; uniform still has the lowest NLL. The fixture's known content
distribution shift and position confounding remain. This validates real API
access and integration, not a human ranking or population benefit.
No prompt, model, input features, split or labels were changed to favor Jev.

[Interactive report](../benchmarks/results/jev-live-fixture-20261021/index.html) ·
[Full typed results](../benchmarks/results/jev-live-fixture-20261021/report.json) ·
[Live acceptance and resource receipt](../benchmarks/results/jev-live-fixture-20261021/acceptance.json).
The saved HTML is self-contained and makes no API requests when opened or dragged.
