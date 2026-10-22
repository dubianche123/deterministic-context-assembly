# Norn's current deck and v6 engine

The PAM∃LA deck and v6 engine are the current Norn product path. The main
`cloudflare/` build targets the existing `norn-machine` Worker and public URL,
with its existing session, budget and archive bindings. There is one public
experience; paper experiments and raw research results remain in the local
research workspace. `cloudflare-research/` is an optional isolated build target,
not a second product entry.

**Released 2026-10-22:** the main PAM∃LA/v6 build is deployed. The
[official DeepSeek integration check](../benchmarks/results/norn-v6-release-live-20261022.json)
passed with synthetic choices; this is engineering acceptance, not human
preference evidence. The final version is
`5f8cd99b-33e6-4e45-bc61-2ccebd827048`; post-deploy session and final changed
asset checks also passed. Full engineering evidence is recorded in
[deployment](deployment.md).

## Why this deck

The original authored cards were generated to match type labels. Those labels, and the
scenes and motifs written with them, were never checked against people's
judgements. The current deck uses 256 images from
[PAM∃LA](https://huggingface.co/datasets/bethgelab/PAMELA) (CC BY 4.0). Every one
of them was rated by many people. That gives each card a population reference
before the first visitor arrives. Here, population means the dataset's reference
raters for these images; it does not stand for the general public. The supplied
scene labels are descriptions from the dataset, not validated personality traits.

## What is in the deck

`the-norn-machine/backend/config/decks/pamela-v1.json` is content-hashed
(`deck_version`) and built in the research workspace from the dataset. For each
card it holds:
- the source image ID;
- type, group and style;
- the generation prompt;
- a short scene (the prompt's first sentence);
- motifs: group, style or type, plus deck-wide tertile descriptors of brightness,
  saturation, warmth and detail;
- the population utility `u`;
- eight content components `x8`.

**Selection** used only the number of raters and the type × group strata, never a
score.

**The browser copy** (`frontend/data/decks/pamela-v1.json`) carries only card IDs,
image paths, `[u] + x8` and the credit. It is regenerated with
`scripts/prepare_research_deck.py pamela-v1` (`--check` fails when stale).

**Images** are 480 px WebP files in `frontend/assets/decks/pamela-v1/`, with an
`ATTRIBUTION.md`.

## Model: v6 (`backend/research_model.py`)

The deck reads choices with three nested models:
- **population:** utility `u`;
- **sensitivity:** `a·u`, how closely this visit follows the reference ratings;
- **deviation:** `a·u + x8·δ`, a personal direction.

The model uses the small-sample priors of the research studies. Two prequential
e-values, each with threshold 20, decide what the reading may say:
- **sensitivity** is claimed only when it predicts this visitor's picks better than
  the population model;
- **a personal deviation** is claimed only when it beats the best sensitivity-only
  model in hindsight.

Otherwise the reading describes the actual choices and says that a reliable
personal difference has not been established. Failing a gate does not prove that
the visitor has average taste. The engine is portable Python, and tests in the research workspace
match it against the NumPy research code.

## Display v2 (`backend/research_deck.py`)

- **Randomization:** each session is randomized once, by `displayMixedShare`, to
  `norn.display.mixed.v2` or `norn.display.random.v2`.
- **Mixed round:**
  - half the cards, rounded down, are the top of the visitor's deviation candidate,
    fitted on completed rounds only;
  - one card comes from the most popular quarter of the remaining cards, one from
    the least popular quarter;
  - any further card is uniform;
  - screen order is a uniform permutation.
- **Random round:** uniform from the cards not yet shown in this pass.
- **Logging:** each `round_started` logs
  `display: {policy, deck: "<name>@<version>", roles}` (`norn.events.v5`), with
  roles `predicted`, `contrast_popular`, `contrast_unpopular` or `random`.
- **Verification:** `research_deck.verify(journal, deck)` recomputes every role. A
  page fixture, run in both browser and engine, checks that they agree.
- **Fallback:** if the candidate cannot be fitted, the round is drawn uniformly and
  every role is logged as `random`.

## What changes for the visitor

- X-Ray compares the reference baseline and effective v6 predictor on each
  completed round's shared pictures, with the actual choices and a cumulative
  card-conditional loss curve. It uses only earlier rounds to predict each
  target. Consented visits unlock predictions after choices are sealed; private
  demos can inspect completed rounds live. The whole-deck constellation, v6
  evidence layers and technical JSON are collapsed.
- Jev is disabled during visits. An explicitly generated offline report can
  replace the whole comparison with three frozen-prefix lanes. Importing or
  scrubbing makes no remote model calls. See [the X-Ray contract](xray.md).
- The original generated-image probe is off for this deck.
- The entry screen credits the dataset.
- The reading uses the same prompts, frozen context, follow-ups, optional rating,
  export and withdrawal as Norn. Its evidence comes from the deck's scenes and
  motifs and from the established layer of research v6.
- The consent text (v4, for every build) says four things beyond the stored fields:
  - the data are also used to study how the loom learns from a few choices;
  - each visit uses one of two ways of dealing the cards, at random;
  - results are published only in aggregate;
  - a visit that ends before the reveal still keeps the rounds played (a partial
    record, replaced as each round starts and superseded by the full record; see
    [deployment](deployment.md)).

## Population slot layer (dormant)

Each round deals 4–6 cards in a uniformly random screen order, and visitors may
favour some screen positions. A deck may therefore carry a population `context`
(schema `norn.slot-context.v1`):
- `offsets`: per number of cards dealt, one centred offset per reading-order slot,
  added to that card's utility;
- `gamma`: a stop slope, γ·log(cards remaining), added to the stop option;
- `provenance`: how and on what it was fitted.

The context is fitted on earlier visitors' sessions in the research workspace,
never on the session it serves, and it is fixed for a whole session. It is part
of the deck document, so the deck version every round logs names it; the engine
(`research_model.Session(x2, context)`), the display reference, the browser fit
and X-Ray's trajectory all use it. The shipped deck has **no** context: the
engine is then research v6 bit for bit, with the same profile hashes. Switching
it on is a new deck version, made only when the frozen live analysis finds a slot
effect that a pooled layer predicts better, and deployed only with the owner's
confirmation. **Activation is also blocked until the per-round comparison is
context-aware.** The current X-Ray comparison, offline v6 comparison and Jev
inputs still describe intrinsic taste only. Before shipping a nonzero context,
add the same per-round offsets to the common candidate probabilities, make the
remote information contract explicit, and verify all lanes against that deck
version. Whole-deck intrinsic scores and the nine-term taste ledger remain
separate from position-specific round utilities. None of these gaps affects the
currently shipped context-free deck.

## Server behaviour

A current-deck service (`NornService(deck="pamela-v1")`, the default `norn serve`,
or `NORN_DECK=pamela-v1` on the Worker):
- accepts only complete v5 journals of its deck and deck version for readings;
- with consent, keeps a partial record of an unfinished visit (`/api/record/partial`);
- refuses journals of Norn's own deck;
- supports preview, trajectory, session inspection and replay import without
  model calls;
- returns `norn.research-trajectory.v1`, with an initial state and each completed
  round; an open round does not enter fitting. Its `comparison` projection
  reuses preceding fitted states without extra fits, and `unseen_top` separately
  ranks pictures not dealt yet. `POST /api/trajectory` also accepts a
  `{journal, comparison_report}` envelope for validated offline comparison.
  Report-content integrity and record binding do not authenticate a provider
  request. Offline report display never weakens strict frozen-profile replay.

Exports use `norn.research-replay.v1`. The original frozen profile and its hash
are unchanged. A separate `contribution_ledger` (`norn.research-contributions.v1`)
binds that profile and the journal by hash and contains nine additive terms for
each of the 256 cards. Their sum reconstructs the effective card score. Import
and `norn replay` recompute the profile and ledger rather than trusting uploaded
contributions. v6 uses the versioned native-libm contract. Strict replay requires
a compatible numerical runtime: CPython and Workers/WASM can differ in fitted
values and profile hashes, so a valid hosted export can be rejected locally.
Importing a hosted export into hosted X-Ray verifies it in that runtime. Hash
checks are not relaxed, and earlier Decimal34 checks do not establish exact
cross-runtime replay for v6.

Records carry `experiments.display` (`mixed` or `random`) and `experiments.deck`.

## Run and deploy

```bash
python3 -m norn serve --mock
# Explicit deck selection is equivalent:
python3 -m norn serve --mock --deck pamela-v1
```

```bash
cd cloudflare
npm ci
npm run build
```

Deployment follows [deployment](deployment.md) using `cloudflare/`:
- keep `NORN_DECK=pamela-v1` consistent with `deck` in the generated browser config;
- preserve `NORN_API_KEY` and `NORN_ARCHIVE_TOKEN` as server-held secrets on
  `norn-machine`;
- preserve its existing session, budget and archive bindings;
- check `displayMixedShare` and consent v4 in `runtime-config.js`;
- after deploying, run synthetic acceptance sessions, verify exports and
  contributions, and withdraw those test records or exclude them from analysis.

There are no real legacy users requiring a session migration for this upgrade.
Historical tools remain explicitly versioned; journals of different decks are
not silently merged or rescored as the current deck.

## Frozen research contract

The live analysis plan v3 in the local research workspace (it superseded display
v2 before any data, when the engine gained the optional context) binds
`research_model.py`, `research_deck.py`, `display_policy.py`, `choice_model.py`,
`numeric.py`, `norn/events.py` and the base deck version; later deck versions are
registered there with the context they carry. Product explanations live
outside these frozen files. Changing the bound mathematics, event contract or
deck still requires a new research protocol version; changing the product's
public entry point does not create a new statistical result.
