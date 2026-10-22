# Learning when to offer the Last Card

`norn.pacing.v1` is an **untrained exploration policy**. It records a decision
after each completed exploration round and sometimes offers the finale. The
visitor can choose when to finish. Twelve rounds is a product/resource ceiling;
neither twelve nor five means that a preference has been learned. See the
[gameplay contract](last-card.md) and [measurement boundaries](measurement.md).

## Run the offline report

Use one fresh, current [consented archive export](deployment.md#consented-anonymous-records),
including partial records. From this checkout, with Python 3.12 or later:

```bash
python3 scripts/evaluate_pacing.py \
  --input /private/path/current-export.jsonl \
  --output /private/path/pacing-summary.json \
  --policy-output /private/path/pacing-candidate.json
```

The standard-library tool reads a JSON array, `{ "records": [...] }`, one JSON
record, or NDJSON. Input is limited to 64 MiB and 100,000 rows. It does not call
models, contact the archive, change production, or modify the research checkout
or its locks. `--policy-output` is optional. Outputs contain aggregates,
parameters and the input's SHA-256 digest, without visitor keys, participant
identifiers, journals, image choices or reading prose.

Use a current export rather than concatenating successive snapshots: identical
evaluation facts deduplicate; conflicting records with the same ID are excluded
instead of guessing which snapshot is authoritative. Withdrawn records must not
be reintroduced from an older export.

Only `norn.last-card.v1` records with the supported product/cohort routing, a
valid exploration journal and a reproducible top-level pacing trace enter the
visit denominator. A finale additionally has to pass the production receipt
validator, including its held-out candidates, frozen-profile probabilities and
commitment. Legacy records and invalid traces have separate exclusion counts.
Validation is exact: a native numeric-runtime difference between Worker and
local Python can make a trace or receipt fail reconstruction. Use a matching
runtime or investigate these exclusions before interpreting the report; the
tool does not silently weaken validation with numeric tolerances.

## Read the denominators

`by_actual_exploration_rounds` groups visits by completed exploration rounds,
including unfinished visits. Each row reports valid finales, missing finales at
export, invalid finales, partial records, and the number of visitor groups.
Missing finales are censored observations, not wrong predictions and not proof
of permanent dropout. A private game is absent from the archive altogether.

For valid finales, baseline and local metrics describe the same four candidates
and recorded favourite: hit rate and mean negative log likelihood (NLL, natural
logarithms; lower is better). Paired NLL gain is `baseline NLL - model NLL`;
positive values favour the model. Hit-rate gain uses the same paired outcomes.
A zero probability gives infinite NLL, represented by `nll_status: "infinite"`
and an explicit count rather than a clipped score. Finite paired gains disclose
their own denominator. The candidate refuses training if any paired finale is
nonfinite, rather than silently excluding a catastrophic zero-probability guess
from the fit; handling such scores needs a rule defined in advance.

Local hits use the live sealed prediction. The baseline hit is reconstructed
offline using the first maximum in recorded candidate order; it is not a
retroactive live guess. Jev is scored only when its fixed finale status is
`ok`. Off, pending and failed calls are counted separately, and the
`jev_common_coverage` section compares all three lanes on that exact subset.
Jev's availability and opt-outs can select this subset, so its results cannot
be compared with another lane's larger denominator as if coverage were random.

`decision_behavior` summarizes offer propensities and what was observed next:
another round started, a valid finale at that round, or an unknown ending at
export. These are follow-up categories, not verified exposure or attention to
an offer. A missing choice is not inferred from the policy's recommendation.

## What the candidate learns

The optional `norn.pacing-candidate.v1` artifact is an **observational round
lookup**, not an optimal stopping rule. Its target is the local model's held-out
finale gain over baseline, not the visitor's selected exit round. Records with
only an exit or a partial history provide missingness/decision counts but no
training label.

Before looking at outcomes, a seeded hash assigns each group to training or
test (default test fraction 0.25). The group is `participant_id` when explicitly
mapped, otherwise the anonymous browser `visitor_id`. Every visit from the same
visitor stays together; explicit participant mappings can link browsers.
Conflicting mappings are excluded. Anonymous browsers cannot establish that
two devices belong to the same human. Repeated visits are averaged within
group and exact round, then groups receive equal weight in fitting.

For each supported training round `r`, the lookup computes:

```text
smoothed_gain(r) = sum(group_mean_gain(r)) / (group_count(r) + shrinkage_groups)
product_utility(r) = smoothed_gain(r) - round_cost * r
```

The default shrinkage is five zero-gain pseudo-groups. The default
`--round-cost 0.005` is a **chosen product utility weight in NLL-nats per round**,
not a measured fatigue penalty or research truth. The training-best round wins;
ties favour the earlier round. Adjacent observed rounds within the default
utility tolerance of 0.01 form its suggested interval; unsupported gaps are
never filled in.

Default admission guards require 20 scored training groups, eight scored test
groups, at least two supported training round values, and five groups at every
eligible/validated round. The independent test groups validate only the exact
training-selected rounds. They never select another interval. Insufficient
same-round coverage or nonpositive held-out mean product utility leaves
`trained: false`, with explicit refusal reasons. These defaults are engineering
guards, not a powered or preregistered significance test. All thresholds and the
split seed are configurable and saved in the report; choose them before opening
test outcomes. Repeated tuning on the same holdout invalidates independence.

Even `trained: true` means only that this lookup passed those offline guards.
People who stop at different rounds can differ in preference strength, patience,
feedback exposure and other factors. A grouped holdout reduces identity leakage;
it does not remove this selection. There are no causal confidence intervals or
claim that any number of rounds is enough to learn a person's preference.

## Path to a future pacing v2

The report and candidate are preparation artifacts. The service does not load
them, automatically deploy them, or replace `norn.pacing.v1`. Insufficient data
therefore leaves the current exploration strategy in place.

Before a future `norn.pacing.v2`, freeze the export digest, numeric runtime,
grouping/split, outcome, cost, coverage rules, missingness treatment and validation
decision in a versioned protocol. Preserve a fresh test set for any subsequent
model change. A product integration would need an explicitly versioned policy
loader, artifact hash and separately reviewed rollout; this tool does not supply
that loader.

Randomized offer propensities are recorded for future protocol-defined analyses
such as an intention-to-treat offer effect or an eligible IPS estimand with
specified assignment, exposure, overlap and censoring assumptions. They do not
make voluntary stopping random, and applying inverse weights alone cannot
recover unobserved favourites from visitors who never reached a finale. A future
randomized policy comparison is needed to estimate what changing the offer rule
does to prediction and completion.

`tests/test_pacing_evaluation.py` checks replay validation, denominators, known
metric arithmetic, no identity leakage, grouped holdout and refusal behavior.
Its artificial probability fixtures validate the tool, not real user preferences
or an empirically best number of rounds.
