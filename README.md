<div align="center">

# The Norn Machine · 命运织机

**Pick pictures you like. See if the loom can guess your next choice.**

[![CI](https://github.com/dubianche123/norn-context-engineering/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/dubianche123/norn-context-engineering/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![MIT License](https://img.shields.io/badge/License-MIT-769884)](LICENSE)
[![Cloudflare Workers](https://img.shields.io/badge/Hosted%20on-Cloudflare%20Workers-F38020?logo=cloudflare&logoColor=white)](https://norn-machine.dubianche123.workers.dev/)
[![No API key for local mock](https://img.shields.io/badge/Local%20mock-No%20API%20key-cfb57c)](#run-locally)

### [Play online →](https://norn-machine.dubianche123.workers.dev/) · [Run locally →](#run-locally)

**English** · [简体中文](README_CN.md) · [Architecture](#architecture) · [Model and deck](docs/research-deck.md)

<img src="docs/readme/cover.svg" alt="The Norn Machine: choose pictures, test predictions, and revisit your picks" width="100%" />

</div>

Keep one, several, or none of the pictures each round. When you finish, your **selection recap** brings together the pictures you kept, a short reading based on those choices, and the results of guesses made before you chose. You can then ask follow-up questions.

Play privately, or opt in to contribute a research record you can withdraw. Round feedback and the Jev comparison are both off by default, with separate switches before starting.

## See how it plays

The screenshots use the local demo: real fitting and replay, labelled template prose, no external API calls or research records. They show the interface, not visitor-study results.

<p align="center">
  <img src="docs/readme/features-en.svg" alt="Three parts of the experience: choose pictures, compare predictions, and revisit your picks" width="100%" />
</p>

**1 / Choose pictures.** Keep what you like, and undo or change your choices freely.

<p align="center">
  <img src="docs/images/readme-en-selection.jpg" alt="English desktop selection board with the full picture grid, selected states and controls" width="900" />
</p>

**2 / Revisit your picks.** Your selection recap puts the pictures, reading and guessing results together.

<p align="center">
  <img src="docs/images/readme-en-recap.jpg" alt="English selection recap showing retained pictures, the reading and guessing results" width="900" />
</p>

**3 / Open X-Ray.** Scrub the timeline to compare each round’s predictions with your choices and inspect cumulative prediction loss. Jev runs only if you opt in before starting.

<p align="center">
  <img src="docs/images/readme-en-xray.jpg" alt="Complete English X-Ray view with picture probabilities, actual choices and the cumulative loss chart" width="680" />
</p>

<details>
<summary>Play on a phone</summary>
<p align="center">
  <img src="docs/images/readme-en-mobile.jpg" alt="English mobile selection board with pictures and controls fitted to a narrow screen" width="360" />
</p>
</details>

**Research question.** When feedback is sparse and visitors can stop at any round, when does a system have enough evidence to personalize, when should it hold back, and what should it show next to balance immediate appeal with learning efficiency? Norn serves as both the public interactive experience and the consented observation instrument; offline datasets, simulations, and paper analyses live in a separate research workspace.

## Run locally

Requires **Python 3.12+**. From the repository root:

```bash
python3 -m norn doctor
python3 -m norn serve --mock
```

Open [localhost:8765](http://127.0.0.1:8765) (or pass `--port 8766`). All 256 WebP images from the PAM∃LA deck (`--deck pamela-v1`), visual feature vectors, and frontend assets are bundled—no network access, cloud account, or third-party Python packages required.

**`--mock` only replaces LLM prose generation.** Event validation, sequential model fitting, evidence gates, additive score breakdowns, and the X-Ray inspector all run the real engine. You can also install the package (`python3 -m pip install .`) to run `norn doctor` and `norn serve --mock` directly, or pass `--deck classic` to explore the earlier authored deck.

## What you can explore

- **Your picks, revisited.** Choose, undo, and revise freely. Before you interact with a round, the engine commits its top prediction from earlier rounds alone. When you finish, you get a selection recap of the images you kept, a round-by-round hit recap, and up to three follow-up turns.
- **Two entry paths, full control over data.** *“Weave your thread”* contributes an anonymous session to the research archive (with one-click withdrawal at any time); the private path leaves no archive record. Both deliver the exact same game, selection recap, and X-Ray replay.
- **Interactive X-Ray timeline.** Scrub across rounds to compare reference baseline probabilities, the learning model's predictions, your actual picks, and cumulative prediction loss. By default, predictions stay sealed until you finish choosing so they don't steer your picks.
- **Pre-entry X-Ray switches (Round Feedback & Jev).** Before starting a session, you can open X-Ray to opt into live round-by-round feedback or enable a side-by-side comparison with **Jev** (`jev-1.13.0` via Typesafe). Both switches default to off and lock once the first round deals. See the [X-Ray contract](docs/xray.md).
- **Bilingual & responsive.** Automatically matches browser language (`zh` / `en`) with a persistent toggle, and arranges 4–6 cards into balanced multi-row grids across mobile, tablet, and desktop viewports.
- **Verifiable session exports.** Export your session's event journal, frozen profile, and additive contribution ledger as JSON, then re-import it into X-Ray or verify it from the CLI:

```bash
python3 -m norn replay /path/to/norn-recording.json --json
```

> **Note on numerical replay:** The v6 engine uses `native-libm` floating-point math. Because CPython and Cloudflare Workers (Pyodide/WASM) can differ in the last bits of `libm` transcendental functions, recordings exported from the hosted Worker should be re-imported into the hosted X-Ray for exact hash verification.

## Architecture

<p align="center">
  <img src="docs/readme/architecture-en.svg" alt="Architecture: browser events feed the shared Python engine, predictions, reading context and consented archive" width="100%" />
</p>

The local server and the Cloudflare Python Worker run the exact same Python model and event validation code.

Under the hood, Norn is built around a strict division of labor: **deterministic Python owns the event log, sequential preference fitting, and statistical evidence gates; the language model only renders the reading from a frozen context.** Everything needed to play, fit preferences, and inspect round-by-round predictions works offline out of the box.

### 1. Three-layer sequential preference learning (`v6`)

Each card in the deck carries a human reference utility `u` (from dataset ratings) and an 8-dimensional content vector `x8`. Rather than jumping straight to a high-dimensional personal model on tiny samples, the v6 engine evaluates three nested layers:

| Layer | Card utility | What it captures |
| --- | --- | --- |
| **Reference (`L0`)** | `u` | Baseline popularity among the dataset's human raters. |
| **Sensitivity (`L1`)** | `a × u` | How strongly—or inversely—this visit aligns with the reference direction. |
| **Personal deviation (`L2`)** | `a × u + x8 · δ` | Specific content preferences beyond general popularity. |

At each round, a sequential Plackett–Luce choice model jointly scores picking among the remaining visible cards and choosing to stop, regularized by small-sample shrinkage priors. Before each round is played, each layer scores the upcoming choices out-of-sample; an **evidence gate** promotes the active explanation to `L1` or `L2` only when cumulative predictive evidence passes a strict threshold. When a visit is too short to separate personal deviation from noise, the reading stays grounded in the lower layer while the selection recap still highlights the exact images the visitor kept. See [deck, model, and display contract](docs/research-deck.md).

### 2. Mixed dealing for feedback and learning

Top-scoring recommendations aim for immediate appeal; contrast cards give the model more varied choices to learn from. Norn assigns each session to either random dealing or **mixed dealing**—combining personal candidate picks with high- and low-reference contrast cards in a shuffled screen layout. Whether this balances enjoyment and learning for real visitors remains a research question. Every card's dealing policy, deck version, and role are logged in the event journal for reproducibility.

### 3. Frozen reading context & privacy-preserving archive

Once selection ends, Python compiles the kept images and the gated model layer into a versioned, SHA-hashed context block shared across the initial reading and follow-up turns. The LLM never mutates model weights or invents unshown choices.

When a visitor opts into the research archive, Norn records the validated event journal (including lightweight non-blocking checkpoints for in-progress rounds so early exits can be studied without fabricating completion events), experiment cohort metadata, and an anonymous browser-scoped identifier that supports one-click withdrawal. It never stores IP addresses, user agents, or follow-up chat text.

## Providers and Cloudflare deployment

Local runs default to `--mock`. To connect a local or remote OpenAI-compatible model for live prose generation:

```bash
export NORN_BASE_URL='http://127.0.0.1:8000/v1'
export NORN_MODEL='your-model-name'
python3 -m norn serve --provider openai-compatible
```

Set `NORN_API_KEY` if required by your endpoint. Native routes for Anthropic, Gemini, Amazon Bedrock, and Docker are documented in [deployment](docs/deployment.md).

The live demo runs on Cloudflare Workers (`norn-machine`):

| Component | Responsibility |
| --- | --- |
| **Static Assets + Python Worker** | Frontend bundle, 256 WebP deck images, and same-origin API routing. |
| **`NornSession` (SQLite Durable Object)** | Per-browser session state, frozen reading context, and idempotent request receipts. |
| **`NornBudget` (SQLite Durable Object)** | Cross-session rate and concurrency admission for LLM and optional Jev calls. |
| **`NornArchive` (SQLite Durable Object)** | Consented research records, partial-round checkpoints, and user-initiated withdrawal. |
| **DeepSeek API (`deepseek-flash`)** | Fast bilingual prose rendering with server-held credentials. |

## Verification and code guide

```bash
python3 -m pip install '.[dev]'
python3 -m pytest
git diff --check
```

The test suite (~760 tests and 1,300+ subtests) covers event validation, the PAM∃LA deck contract, v6 sequential fitting, evidence gates, additive score reconstruction, frozen reading contexts, idempotent request recovery, archive cohort isolation, and frontend layout invariants.

| Where to look | What it covers |
| --- | --- |
| [docs/research-deck.md](docs/research-deck.md) | PAM∃LA reference ratings, content features, evidence gates, and mixed dealing rules. |
| [norn/events.py](norn/events.py) · [norn/service.py](norn/service.py) | Validated event journal and the reading / follow-up lifecycle. |
| [research_model.py](the-norn-machine/backend/research_model.py) · [research_deck.py](the-norn-machine/backend/research_deck.py) | Portable v6 sequential choice engine, evidence gates, and frozen context assembly. |
| [the-norn-machine/frontend/](the-norn-machine/frontend/) | Card board, bilingual reading view, and interactive X-Ray timeline. |
| [norn/collection.py](norn/collection.py) · [cloudflare/](cloudflare/) | Consented archive storage, partial-visit checkpoints, and Durable Object adapters. |
| [docs/learning.md](docs/learning.md) · [docs/measurement.md](docs/measurement.md) · [docs/fast-thinker-comparison.md](docs/fast-thinker-comparison.md) | Earlier model generations (`v1`–`v5`) and offline evaluation tools. |

PAM∃LA images are distributed under **CC BY 4.0** ([attribution details](the-norn-machine/frontend/assets/decks/pamela-v1/ATTRIBUTION.md)); project code is released under the [MIT License](LICENSE).
