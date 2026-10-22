# Measurement

Norn is a product people can play, read, question and share. Measurement keeps
its entertainment outcomes, predictive outcomes and research conclusions
separate. The README states the research questions; this page records the
interpretation and sampling boundaries.

## Current Last Card product

The public path is `norn.last-card.v1` on the PAM∃LA deck and frozen v6 engine.
Exploration uses four pictures per round, followed by one held-out finale with
four previously undealt pictures. A game has no fixed five-round threshold.
After each completed exploration round, `norn.pacing.v1` may advise continuing
or offer the finale; visitors can choose it themselves after one completed
round. The ceiling of 12 rounds is a product/resource limit. The first pacing
policy is exploratory, not trained, and does not certify that a personal
preference has been learned.

| Outcome | What the observation answers | Interpretation limit |
| --- | --- | --- |
| Completed exploration and finale | Did a contributing visitor stay for the reveal? | Private play leaves no archive, so this is not a denominator of every person who opened the site. |
| Final favourite hit / conditional loss | Did a frozen predictor anticipate the favourite among four new candidates? | Score only settled, correctly bound challenges; compare all predictors on the same candidates and outcomes. |
| Continue / offer-finale trace | What did the policy advise, with what probability and available history? | Advice acceptance is voluntary; a logged propensity alone does not establish optimal stopping or remove self-selection. |
| Character rating and follow-up | Did the authored entertainment interpretation resonate or invite questions? | A story rating is not personality accuracy or validation of image-model weights. |
| Local share-card download | Did this browser request a shareable file? | A download does not prove a message was sent, a friend saw it or a new player arrived. |
| A later game | Did the same consenting browser return? | Browser identity is neither a person nor proof of stable preferences. |

### What is frozen and what is held out

The learning journal describes exploration only. The server freezes that
prefix/profile, then seals the local prediction before receiving the finale
answer. The final favourite lives in `norn.finale.result.v1` outside the journal;
it is not fed back into the model that predicted it. Uniformly sampled unseen
candidates prevent the final set from being chosen using the eventual answer.
The reference and effective v6 probabilities describe the same four pictures.
When v6 falls back to the reference, their agreement is disclosed rather than
counted as independent support. A salted tie-break creates an honest game
choice; it does not establish personal information.

The finale target differs from the historical rolling target, **first finally
kept picture in an exploration round**, and from the post-reading “closest
picture” answer. They must not share labels or denominators. Predictions that
were unavailable, arrived after the answer, or lack an authentic pre-choice
binding cannot be credited as timely guesses. A replay calculation cannot
retroactively become a live prediction. No single game proves population
accuracy, stable personality or a causal recommendation gain.

### Pacing observations

The product carries exactly
`{schema_version: "norn.last-card.v1", pacing_policy: "norn.pacing.v1"}`.
A separately stored `norn.pacing.v1` trace binds the true completed history and
records the policy's action, reason, offer probability, reproducible draw,
current evidence layer, selection count and active time. A passed predictive
gate may trigger an offer; otherwise randomized offers explore possible
stopping opportunities. The server recomputes the trace. It never reads the
future favourite or LLM story to decide an earlier offer.

This data permits later, versioned work on the value of another round. A new
study must predefine outcomes, account for voluntary stopping and selective
completion, and distinguish an offer's effect from a player's own decision.
The current policy is not presented as learned or optimal. Existing frozen
research code and locks remain unchanged. See [the offline pacing report](pacing.md)
for grouped holdout evaluation, censored visits and a candidate that requires
separate review before activation.

### Jev, feedback and consent

Last Card defaults Jev on where it is configured and available; a pre-entry
control lets the visitor decline it. It is used only for one finale attempt,
not every exploration round. Earlier choices and numerical candidate features
are sent to Typesafe. Its own sealed result must arrive before the final
choice to count. The 15-second/zero-auto-retry and shared actor/global limits
remain in force. A configured DeepSeek call writes the character prose
separately. Default availability and self-selected opt-outs do not establish
a causal Jev benefit.

Exploration round feedback remains an independent pre-entry switch, off by
default. `norn.experience.v1` records its immutable setting and genuinely
visible feedback exposures, with `after_round`, category and `elapsed_ms`.
A rendered exposure is not proof of attention. Missing historical settings
remain unspecified. Collection consent is independent of both model and
feedback controls; a private game gets the same rewards and leaves no
research record.

### Archive and protocol boundary

Current product records carry `product`, `pacing` and any settled `finale`
sidecars outside the frozen event/profile contract. They route to
`<source>.last-card.v1`, or
`<source>.last-card.v1.round-feedback.v1` with exploration feedback on;
`source_deployment` retains the actual host. Both partial and full records
follow the same cohort rule. SQL archive validation refuses conflicting
product/version/cohort metadata, and a late request cannot resurrect a
withdrawn record. The older frozen visitor reader excludes these new cohorts.
Its old protocol is not reinterpreted as a study of the new game.

Consent v4 continues to support `norn.partial.v1` checkpoints: the current
round's real readiness, choices and revisions are saved without inventing a
completion or STOP. Provisional `open_round` attention includes elapsed/hidden
time, seen cards, readiness and hover. It is not a final duration. Choice
changes are debounced by 500 ms, active rounds sampled every 10 seconds, and
hiding/leaving attempts a keepalive flush no larger than 60 KiB. Collection
never blocks a choice. Browser termination, offline state or oversized payloads
can lose a last interaction. The final favourite may be stored before prose
returns; the full reading upgrades the same visit. See [the gameplay contract](last-card.md)
and [storage details](deployment.md#consented-anonymous-records).

Exploration count, readiness count, ever-seen count and layout remain distinct.
Current four-picture play does not create old measurements of simultaneous
first-screen exposure or train an option-count/fatigue effect. The optional
slot layer remains dormant on the current deck. Recording an observation and
learning its effect are different stages.

## Historical v4/v5 reporting tools

The commands and outcome definitions below describe the earlier card catalog and its historical evaluation contracts. They do not automatically evaluate Last Card pacing, finale choices or character stories. New product cohorts require their own explicitly versioned analysis.

Only consented records (`norn.record.v2`, see [deployment.md](deployment.md#consented-anonymous-records)) are measured. Each holds the raw event journal, the hashed browser `visitor_id` with a `visit` number, versions (scoring, prompt, model), experiment arms, and two optional annotations: the 1–5 rating with the "closest card" answer, and the number of follow-up turns. `records report` recomputes the historical **v4 raw reference**, with its model version explicit. `records evaluate` additionally compares the current local v5 policy as **`hybrid_guarded`**, fitted and gated only inside each training prefix; original `hybrid` still means v4. Existing reports and new-image rules are not relabeled. See [the guarded-policy validation](choice-policy.md).

```bash
norn records report                                # local store (~/.norn)
norn records report --input archive.jsonl          # Cloudflare archive export
norn records report --by generation.prompt_version # split outcomes by any record field
norn records report --json --output report.json    # full numbers for a write-up
```

Intervals are 95% bootstrap intervals over **visitors**: all records of one browser are resampled together, because a person's repeat visits are not independent evidence.

## What the report says

| Section | Metric | Reads as |
| --- | --- | --- |
| Volume | records, visitors, returning visitors, visits per visitor | Who came back. |
| Engagement | rounds (median), picks per round, share with withdrawals, share asking a follow-up | How people play. |
| Outcome | rating mean and distribution, response rate; closest-card answers | The only direct label of whether a reading landed. |
| Validity, within a visit | held-out choice gain over uniform and over a **stop-only** model (nats per choice); share of sessions beating each | Whether the model predicts the later rounds of the same visit. The stop-only model learns only how often this person presses stop; beating uniform can come from that alone, so only the gain over stop-only says anything about which pictures someone prefers. The report reads exactly the rows the visitor's profile reads, including rounds whose cards never reached the screen, so both draw the same chronological split and give the same numbers. |
| Validity, closest card | how often the visitor's closest card is the model's top pick among their own picks, against chance (1/k) | Whether the model's ordering agrees with what the person says. |
| Validity, across visits | next-visit log-loss of the visitor's own earlier model vs **another visitor's** picture weights under the visitor's own stop habit (`personal_minus_stranger`, with the median evidence gap), vs the visitor's own stop-only model (`personal_minus_stop_only`), vs a population model, vs uniform; same type and axis-sign agreement vs strangers; `person_specific_detected`, `stable_preference_detected` | Whether a stable, person-specific picture preference exists. This is the claim the visitor link exists to test. |
| Groups (`--by`) | per arm: records, visitors, rating, follow-up turns, held-out gain; difference to the control arm with its interval | An A/B comparison. |

**Reading the cross-visit numbers.** There are two claims, and each has its own flag. Never quote either from a point estimate.

| Flag | Requires (95% interval entirely below zero) | May be quoted as |
| --- | --- | --- |
| `person_specific_detected` | `personal_minus_stranger` | One visit's picture choices carry something specific to that browser that shows up again next visit. |
| `stable_preference_detected` | `personal_minus_stranger` **and** `personal_minus_stop_only` | That, and the picture model also predicts the next visit better than knowing only how often the person presses stop. |

A model that knows only someone's stop habit already beats chance, so the stop-only comparison is what shows the pictures are worth predicting from.

- **Stranger baseline.** The visitor's earlier model is compared with another returning browser's earlier visit. A single session's model overfits, so "own model vs population model" is dominated by that overfitting and barely ever detects a real person even when one exists (see below). An equally overfitted stranger avoids this.
- **Matched on history size.** A model fitted on more choices overfits less, so the stranger is the browser closest in number of choices; `stranger_evidence_gap_median` shows how close the match was.
- **The visitor's own stop habit.** The stranger keeps only its picture weights; its stop bias is refitted on the visitor's own earlier visit. Without this, a person with a lasting stop habit and no picture preference beats strangers "significantly". On 30 simulated visitors that difference was −0.11 nats per choice with the interval excluding zero; now it straddles zero.
- **Regression checks** in `tests/test_measurement.py` cover a lasting stop habit alone, unlinked visits and a stable person. Only the stable person is detected, under both flags.

## How many visitors

[`scripts/measurement_power.py`](../scripts/measurement_power.py) simulates visitors with known preference strength and visit-to-visit stability and counts how often the report's interval detects the truth (simulated only; [v1](../benchmarks/results/measurement-power-simulated-v1.json), [v2](../benchmarks/results/measurement-power-simulated-v2.json) with lasting stop habits and the current stranger). Readings:

- **A/B on the rating** (sd ≈ 0.9): a 0.5-point lift is found in 60–80% of runs with 60–120 rated readings in total and reliably by 240; a 0.3-point lift needs around 480. Until traffic reaches hundreds of rated readings, online A/B tests on the rating can only catch large effects.
- **Across visits** ([v2 results](../benchmarks/results/measurement-power-simulated-v2.json): 10 repeats per cell, every simulated browser with a lasting stop habit of sd 0.5; v1 is kept for reference). An earlier version compared the visitor's own model with a population model and detected nothing at realistic strengths even with 80 returning visitors, which is why the stranger baseline exists.
  - **No false detections.** With no picture preference at all, nothing was detected in any run at 20, 40 or 80 returning visitors.
  - **`person_specific_detected`.** Weak preferences (weight sd 0.5) that are half stable between visits were detected in 10%, 40%, 50% and 60% of runs at 10, 20, 40 and 80 returning visitors. At stability 0.8 the rates were 80% at 20 and 100% from 40 (sd 1.0: 100% from 20).
  - **`stable_preference_detected`.** It was never detected in any cell up to weight sd 1.0. In those cells a model fitted on one visit predicts the next visit about as well as the stop habit alone, for example −0.02 nats per choice [−0.11, 0.08] at sd 1.0, stability 0.8, 80 returning visitors. Its strongly negative `personal_minus_stranger` (−0.72) comes from the stranger's weights being confidently wrong, not from one's own being sharply right. Only strong, fully stable preferences pass (sd 1.5, the regression test).
  - **What to expect.** A few dozen returning browsers can show a person-specific signal. Showing a preference that predicts better than the stop habit needs stronger preferences, longer visits or a better model.

## Running an A/B test

1. Add an entry to `EXPERIMENTS` in [`norn/experiments.py`](../norn/experiments.py), for example `"reading_prompt": {"arms": ("norn.prompt.v4", "norn.prompt.v5")}`. Arms come from a hash of the experiment name and the visitor id: a browser keeps its arm across visits, and assignment needs no storage.
2. Read the arm where behaviour changes. `reading_prompt` is already wired: the service builds the reading context with the arm's prompt version. Other experiments read `plan["experiments"][name]`.
3. Compare with `norn records report --by experiments.reading_prompt --control norn.prompt.v4`.
4. Remove the entry when the decision is made, so new records stop carrying the arm.

Visitors who choose "只看看" also get an arm (they see the same product) but leave no record. Measured results therefore describe people who chose to weave in.

## Comparing models on held-out choices

`norn records evaluate` decides which card features the choice model should use, from real choices, before anyone argues about it ([`norn/evaluate.py`](../norn/evaluate.py), needs numpy: `pip install 'norn-machine[eval]'`).

```bash
norn records evaluate --input archive.jsonl            # text table and the decision
norn records evaluate --input archive.jsonl --json --output evaluation.json
```

**Method.**
- **Split.** Each candidate is fitted on the earlier rounds of a visit, frozen, and scored on the later rounds. The split is the profile's validation split (last 20% of completed rounds held out). A visit never repeats a card, so every held-out card is new to the fitted model.
- **Shared model.** All candidates use one sequential logit with a stop option and one prior, so differences come from the card features alone. Its `hybrid` is scoring v4 itself: its parameters and held-out log-loss equal `choice_model.fit` and the profile's validation to 1e-12, as does each baseline (`tests/test_evaluate.py`).
- **Intervals.** 95% bootstrap intervals over visitors, paired across models.
- **Learning curve.** Each candidate is refitted after 2, 4, 6 and 8 rounds and scored on the rest.

| Candidate | Card features |
| --- | --- |
| `uniform`, `stop_only` | none; the visitor's stop habit |
| `axes` | the four type axes (16 distinct values) |
| `type16` | one-hot type |
| `pixel` | the eight pixel components |
| `hybrid` | axes + pixel components (current) |
| `hybrid_shrunk` | the same with a four times stronger prior |
| `motif`, `hybrid_motif` | the 91 motifs on at least 3 cards (89 span several types), alone or added |

| Metric (held-out, per choice) | Reads as |
| --- | --- |
| `all` (nats) | every pick and the stop: overall prediction |
| `cards` (nats) | picks among the remaining cards only: picture preference without the stop habit |
| `within_type` (nats) | picks among remaining cards of the picked card's type: can the model tell same-type cards apart? `axes` and `type16` are at chance here by construction |
| `top1` (share) | the first pick of a held-out round is the model's best card |

**Decision rule, fixed before any real data** (`DECISION_RULE`, also written into every output). It needs at least 100 visitors.
- **Useful:** a model's `all` interval against `stop_only` lies entirely below zero.
- **Better:** a candidate's `all` interval against `hybrid` lies entirely below zero, and its `within_type` is no worse by more than 0.02 nats.
- **Verdicts:**
  - `candidate_better`: a candidate is both useful and better.
  - `keep_hybrid`: no candidate is, but `hybrid` is useful.
  - `no_picture_model_beats_stop_only`: no picture model is useful.
- **Changing the rule** after seeing data must be reported as such.

**Does the instrument work?** [`scripts/model_evaluation_sim.py`](../scripts/model_evaluation_sim.py) runs it on simulated visitors whose taste is deliberately not always the model's own ([results](../benchmarks/results/model-evaluation-simulated-v1.json); 200 single-visit visitors, 8–16 rounds per cell). Taste can be about the model's features, recurring motifs, the 64 raw pixel statistics, or a mix. Choices follow the model's logit rule or a threshold rule with random click order, with and without position bias and fatigue.

| Simulated taste | `stop_only` | `hybrid` | Verdict |
| --- | ---: | ---: | --- |
| none | 1.458 | 1.616 | no picture model beats stop-only |
| model features, weak | 1.469 | 1.357 | `hybrid_shrunk` |
| model features | 1.502 | 1.069 | keep hybrid |
| model features, threshold rule | 1.479 | 1.178 | keep hybrid |
| model features, position bias + fatigue | 1.506 | 1.082 | keep hybrid |
| motifs | 1.443 | 1.607 | no picture model beats stop-only |
| motifs, threshold rule | 1.430 | 1.623 | no picture model beats stop-only |
| raw pixels | 1.484 | 1.357 | `pixel` |
| model features + motifs | 1.466 | 1.084 | keep hybrid |

The rule switches only when the simulated truth says so. Two findings matter for real data:

- **When taste lies outside the 12 features, the current model is worse than no picture model at all.** With no taste, or a taste for subjects, it loses 0.16–0.19 nats per choice to stop-only, because a dozen rounds cannot pin down 12 weights. The motif candidates do not rescue this either: no single visitor's motif taste is learnable from one visit. Pooling across visitors (a population model with personal deviations) is the route for subject-level taste once data exist.
- **Prior strength matters.** Weak tastes favour the stronger prior, strong tastes the current one. With real data, the shrinkage can be tuned across visitors instead of fixed by hand.

These simulations check the instrument; they say nothing about which taste real people have. The v3 journal's hover, layout and input fields are not candidates yet. Each should enter this comparison as a feature and stay only if it lowers held-out loss.

## New images in a pilot

The [new-material pack](generalization.md) tests whether taste learned on the original deck carries to pictures that were never part of it. It runs only in a pilot.

**What a participant sees.** Open the site with `?pilot=new-material-v1` and choose "织入命运". Without consent nothing would be kept, so nothing extra is shown.
- The participant plays the normal rounds and presses "揭示命运". That seals the main journal, so the model behind the reading is fixed before any new image appears.
- Four sets of the 16 new images follow. Each set holds two scenes, each in its photographic and illustrated version, in random positions; scene order and pairing are random per visit.
- "揭示命运" ends the phase at any point, and only then is the reading generated and shown.

**What is stored.** The new-image choices are a separate `norn.probe.v1` journal ([`norn/probe.py`](../norn/probe.py)).
- It uses events pinned to `norn.events.v4`, with layout, input, hover and visibility, over the frozen probe catalog. The catalog is derived from the pack by [`scripts/prepare_probe_catalog.py`](../scripts/prepare_probe_catalog.py), bound to the pack and projection hashes, and carries no type or motif labels.
- The server validates it, including the design: whole scenes, both styles, no repeats. It then stores it in the consented record next to the main journal.
- It never enters the profile, the reading, the replay export or the reading's prompt.

**What `norn records evaluate` reports** under `new_material`, for every record with a probe:
- **Fit and score.** uniform, the visitor's stop-only and pixel-only are fitted on the whole main journal and scored on the probe's choices. The four-axis, type, hybrid and motif candidates cannot score these images; they are reported as unsupported, never filled with invented labels.

| | Rule |
| --- | --- |
| Primary (the frozen protocol's estimand) | pixel minus stop-only on `all`, held-out NLL including stop; verdict `pixel_taste_transfers` only if the 95% interval over visitors lies entirely below zero, otherwise `transfer_not_shown` |
| Secondary, reported only | `cards` minus uniform; `within_scene` (photograph vs illustration of one scene; a coin flip is 0.693); `top1`; photo share and per-visitor style consistency against binomial chance |
| Fixed by the evaluator on 2026-10-21, before any human outcome | at least 30 visitors (separate from the 100-visitor rule for the original deck); a record counts only with at least 5 completed main rounds and 2 completed probe rounds; unusable records are counted by reason |

**Simulated check.** In `tests/test_probe.py`, simulated pilots whose taste lies in pixel features get `pixel_taste_transfers`, and pilots whose taste is about subjects, which these images cannot carry, get `transfer_not_shown`. The coverage rule and broken probes are tested too.

**Reading a null.** The eight pixel components barely encode photograph vs illustration: across the eight scenes, the photo-minus-illustration directions have a mean cosine of −0.02. A null result therefore says these features do not capture what varies, not that taste fails to transfer. The style statistics measure style preference without any model.

## Offline prose comparison with real models

Prompt changes can be compared before any visitor sees them. [`scripts/prose_eval.py`](../scripts/prose_eval.py) reads the same synthetic sessions through the production service (prompt build, provider, guardrails) once per prompt version and model, asks two fixed follow-up questions, and runs rule checks on every output: length against the style range, type letters, technical terms and digits leaking into prose, real scenes cited, quoted phrases that match no card. A judge model from another family then compares the two readings of each session in both orders, so position bias cancels, scoring grounding, resonance, clarity and overclaiming. Reports go to `benchmarks/results/prose-eval-*.json`; they contain no keys and no visitor data.

Offline judging decides whether a variant is worth showing; the rating in an online A/B decides whether people prefer it.

**Three roles, kept apart.** *Production* is the official DeepSeek endpoint with `deepseek-flash` and `thinking: {type: disabled}` (`norn/cloud_provider.py`). *Candidates* are the models being read, here through the local proxy. *Judges* compare readings and never write them. `--wire production` sends the Worker's exact request, model name aside, so an acceptance run matches what visitors get; the default `--wire compatible` sends the proxy's `reasoning_effort: off`, which DeepSeek routes can raise to `low`. Every report records the three roles.

**Judge defaults** follow the [qualification](../benchmarks/results/judge-qualification-20261021.md): MiMo V2.6 Pro alone (`cn:mimo-v2.6-pro`), a 24,000-token output ceiling and a 360-second timeout, with the JSON-quoting instruction from the qualification runner. Neither MiMo nor HY4 qualified for independent automatic ranking, so there is no automatic fallback to HY4. An explicitly listed second judge is used only for transport failure, truncation or malformed JSON, and both display orders of one pair always come from the same judge.

**Verdicts that cannot count.** A verdict whose winner scores no higher than the loser on every rubric (lower on at least one) without overclaiming less, or a tie where one side is strictly better on every rubric, abstains with `issue: winner_dominated_by_loser` / `tie_with_strict_dominance`; it is kept for review and never flipped. The two orders of a pair are then normalised: the same winner in both decides the pair; opposite winners are an `order_disagreement` and a tie against a win is a `split`, both reported but excluded from the win rate and score means. The summary counts independent session pairs, with `coverage` = decided / all pairs. Saved qualification verdicts are regression tests: the contradictory GLM fixture-0 verdict, the GLM fixture-4 reversal and the malformed baseline JSON (`tests/test_prose_eval.py`).

**Facts the judge sees** are everything either prompt gave the writer, labelled by source: the picks with rounds, scenes, motifs and traits; v4's visual statistics (technical, so citing them is not fabrication but costs clarity); v5's inferred motifs with counts; the recommended scenes with their motifs; the evidence verdict. Earlier runs gave only the picks, so judges called system-provided material "fabricated".

**First run under these rules** ([report](../benchmarks/results/prose-eval-v3-mimo-consistency.json)): the 24 saved v2 readings (no new generation), 12 MiMo calls, all HTTP 200 and `stop`. Of 6 session pairs, 3 were decided and all 3 favour v5; the other 3 are order disagreements in which MiMo chose position A in both orders. No decided pair favours v4. Coverage is 50%, and a trusted ranking still needs human judgements on a small blind set. A manual HY4 second opinion needs `--judge cn:hy4-preview --judge-max-tokens 24000 --timeout 360 --attempts 1`.

### Human blind set

[`scripts/human_judging.py`](../scripts/human_judging.py) turns saved readings into a page a person judges, so MiMo can be calibrated against a human instead of trusted alone. It makes no model or network calls. Every pair whose v4 and v5 readings are both ok is included. From the v2 readings that is 6 pairs, the same 6 MiMo judged, shown with the facts MiMo saw (the build checks this against MiMo's verdict cache keys).

```bash
python3 scripts/human_judging.py
# open benchmarks/results/human-judging-v1.html, judge every pair, press 导出 JSON
python3 scripts/human_judging.py --import ~/Downloads/human-judging-answers.json
```

The page shows the facts and two readings labelled only 甲 and 乙. It asks which is better (甲/乙/差不多), 1–5 for grounded, resonance and clarity, overclaim yes/no for each side, and an optional note. Answers stay in the browser's `localStorage`; if the browser blocks it, the page says so, and 导出 / 载入 JSON resume the work. The v5 side is a seeded, balanced assignment (half the pairs each way, presentation order hashed separately) recorded in `human-judging-v1.key.json`. Do not open the key before judging. Blinding hides labels, not style: v4 readings may still cite visual statistics.

Exported rows use the judge's verdict shape over sides A (甲) and B (乙), so `valid_verdict` and `verdict_issue` apply unchanged. The import checks that the answers belong to this page and these readings, and that today's facts still match the page. A MiMo verdict counts only if its cache key matches this page's facts and readings in that display order. A verdicts file that names the same pairs but judged other inputs is marked `input_mismatch`: it is reported, but left out of agreement. `inputs.verdicts.same_file_as_key` records whether the verdicts file is the one the page was built against. The import then unblinds the answers into prose_eval verdict rows with `judge: "human"`, prints a short table and writes `benchmarks/results/human-judging-v1.json` (never replaced; pass `--output` for another). The report contains:

- human pair outcomes;
- agreement with MiMo on pairs MiMo decided, with order disagreements, splits and abstentions counted separately;
- how many single MiMo orders sided with the person;
- overclaim agreement per pair and version, counting MiMo's flag only when both orders agree.

A person's choice stands even when it contradicts their own scores; it is flagged as `issue`, and the page shows the same hint while judging. One labeller on six pairs calibrates MiMo on these readings; it is not a population preference.

### Concurrency and resuming an offline run

The evaluator defaults to **six independent session/pair workers** and **six HTTP requests in flight**. Generation, judges, fallbacks and retries share one transport limit; follow-ups within a session and the two directions within a pair remain sequential. This is separate from the public demo's paid-call limits. The local Workbuddy API for this setup is `http://127.0.0.1:17863/v1`; port 17864 is a management gateway with separate authentication. The proxy now allows **two concurrent requests per account and six across the three-account pool**. All three current accounts use the CN realm, so `pool.max_in_flight=2` supplies this capacity. `max_in_flight_global` controls each global-realm account, not the entire pool. The evaluator's shared limit of six matches the current pool capacity; account routing and per-account limits belong to Workbuddy. Other programs may share that pool.

```bash
python3 scripts/prose_eval.py --base-url http://127.0.0.1:17863/v1 \
  --model cn:deepseek-v4.1-flash --workers 6 --max-in-flight 6 \
  --key-file /path/to/private-config.json --output /tmp/prose-eval-new.json
# Repeat the same command with --resume after interruption.
# Add --dry-run to inspect the plan without reading credentials or making requests.
```

Each invocation allows at most 128 actual HTTP attempts by default (`--max-requests`), including retries; this is a per-invocation call-count limit, not a currency budget. The transport retries network/429/5xx failures at most twice in total, releases concurrency while waiting, and shares a cooldown for 429/503 with `Retry-After` and jitter. Authentication errors and truncated/invalid output are not transport retries. Judge output has its own budget (`--judge-max-tokens`, now 24,000 by default after the qualification); this does not raise the application's prose budgets or change model reasoning settings.

New runs refuse to overwrite an existing report or samples log. Their append-only `.samples.jsonl` uses `norn.eval.samples.v1` envelopes and a semantic configuration hash. Generation responses and each judge direction are saved as soon as they finish, so `--resume` reuses completed requests, even if the final report was never written. A process lock prevents two writers to the same run. Scheduling limits can change on resume; changed inputs, source code, model choices or judge settings require a new output. An interrupted request with a durable pending marker has an unknown outcome and is **not sent again automatically**. Confirmed zero-dispatch cancellation or budget exhaustion is recorded separately and remains safe to resume; reaching the request allowance pauses with exit code 75 instead of publishing a completed report, and the next invocation may raise `--max-requests`. Ctrl-C cancels queued work, interrupts transport cooldown/permit waits, and allows active HTTP requests to settle before releasing the run lock.

`--resume-sessions old.json.sessions.json` remains an explicit way to import previous generation results into a **new** evaluation; it does not import old judge results. Historical v1/v2 reports and their older flat sample logs remain unchanged. Attempt logs record status, request/output budgets, finish reasons and numeric token usage, without keys, headers or raw exception messages. Transport metrics describe the current invocation; reused result rows retain their historical observations. No run is started merely by changing these defaults.

The [six-request GLM smoke](../benchmarks/results/workbuddy-eval-concurrency-v3.json) completed all six requests without retries in 3.256 seconds. Proxy status observed six active requests split **2/2/2** across the accounts. The [earlier mixed-model probe](../benchmarks/results/workbuddy-eval-concurrency-v2.json) is retained with its three GLM successes and three DeepSeek 503 responses. The DeepSeek errors were traced to the local proxy's missing CN capability fallback on cold startup and its wrapping of upstream request errors as 503. After repairing the proxy, the [cold-start DeepSeek smoke](../benchmarks/results/workbuddy-deepseek-503-fixed-v1.json) completed all six requests without prewarming or retries, again observing **2/2/2** occupancy. See the [diagnosis and source patch](../benchmarks/results/workbuddy-deepseek-503-diagnosis-20261021.md). These short checks verify proxy behavior, not sustained throughput or prose quality.
