# Next Agent Handoff

Read this file first.

## 0. Path Recovery After Move

If the repository has been moved or renamed, do not trust old absolute paths.

1. Find the git root first.
2. Confirm the repo still contains `README.md`, `README_CN.md`, and `the-norn-machine/`.
3. Rebuild any absolute filesystem references from the current workspace, not from previous chats.
4. If the folder layout changed, keep the same internal structure inside `the-norn-machine/`:
   - `backend/`
   - `frontend/`
   - `infra/`
   - `image_manager/`
   - `docs/`

Useful reorientation commands:

- `pwd`
- `git rev-parse --show-toplevel`
- `rg --files | rg 'AGENT_HANDOFF|README(_CN)?|runtime-config|deploy_backend|deploy_to_s3|benchmark_summary|c4-dataflow'`

## 1. Current State

- Project: The Norn Machine
- Scope: serverless, stateless, cloud-native prompt engine with a static frontend and a Python Lambda backend
- Current runtime model:
  - Browser -> CloudFront/S3 -> API Gateway -> Lambda `fast_thinker` / `template_router` / `slow_thinker` -> Bedrock
  - Dialogue follow-up is handled separately by `dialogue_thinker`
- Current branch state at last verification: `main` and `feature-my-update` are kept in sync
- Current docs assets:
  - `the-norn-machine/docs/c4-dataflow.svg`
  - `the-norn-machine/Norn-machine.drawio.svg`
- Last verified handoff commit: `b94a925`

## 2. Repository Map

| Path | Role |
| --- | --- |
| `README.md` | English project overview, architecture, benchmark evidence, and deployment notes |
| `README_CN.md` | Chinese mirror of the project overview |
| `the-norn-machine/frontend/` | Static frontend (`index.html`, `style.css`, `app.js`, `runtime-config.js`) |
| `the-norn-machine/backend/` | Lambda handler, Fast Thinker, router, Slow Thinker, dialogue guardrails, benchmark artifacts |
| `the-norn-machine/infra/` | Deployment scripts for backend packaging and frontend S3 sync |
| `the-norn-machine/image_manager/` | Card/image metadata pipeline and asset generation tools |
| `the-norn-machine/backend/tests/` | Unit tests for the backend pipeline |
| `the-norn-machine/backend/benchmark/` | Scenario generator, benchmark runner, prompt fixtures, results, and summary chart |

## 2.1 High-Signal Entry Files

If you only read a few files after the move, start here:

- `the-norn-machine/backend/lambda_function.py`
- `the-norn-machine/backend/fast_thinker.py`
- `the-norn-machine/backend/template_router.py`
- `the-norn-machine/backend/dialogue_thinker.py`
- `the-norn-machine/backend/slow_thinker.py`
- `the-norn-machine/frontend/app.js`
- `the-norn-machine/frontend/runtime-config.js`
- `the-norn-machine/infra/deploy_backend.sh`
- `the-norn-machine/frontend/deploy_to_s3.sh`

## 3. Code Conventions

- Default to ASCII when editing code or docs unless an existing file clearly uses Chinese or other Unicode text.
- Use `apply_patch` for manual file edits.
- Keep the system stateless by design:
  - no database-backed session store
  - no cross-user memory
  - no long-lived profile persistence
- Keep deterministic logic in Python and leave language rendering to the model.
- Treat prompt text as product copy, not literal object listing:
  - prefer behavioral, emotional, and relational summaries
  - avoid over-explaining selected cards or raw internal metrics
- Keep README prose aligned with the current runtime behavior; if behavior moves, update the docs in the same change.
- Keep guardrails and fallback text on the code side, not in the model prompt.
- For SVG or layout work, prefer centered labels, transparent label backgrounds when possible, and no overflow beyond the viewbox.
- Ignore macOS metadata files such as `.DS_Store` unless the user explicitly asks to clean them up.
- Do not delete user-added assets or deployment artifacts unless the user explicitly asks.

## 4. Runtime Architecture

### Backend flow

1. `lambda_function.py` is the single Lambda entry point.
2. `fast_thinker.py` compresses selections, durations, deselection events, and card metadata into a bounded behavioral context.
3. `template_router.py` chooses the prompt mode and assembles the final Bedrock payload.
4. `slow_thinker.py` calls Bedrock and applies output fallback / guardrail logic.
5. `dialogue_thinker.py` enforces the follow-up dialogue turn limits and the 100-character user input cap.

### Frontend flow

1. `app.js` drives the whole static experience.
2. `runtime-config.js` is the local runtime config stub.
3. `index.html` and `style.css` are deployed as static assets through S3 + CloudFront.
4. Audio, particle effects, and dialogue UI are all client-side concerns.

### Deployment surface

- `infra/deploy_backend.sh`
  - packages the Lambda bundle
  - creates or updates the Lambda, API Gateway, API key, usage plan, and IAM role
  - keeps the backend stateless
- `frontend/deploy_to_s3.sh`
  - syncs the static frontend to S3
  - preserves `runtime-config.js`
  - invalidates CloudFront after upload
- Public S3 website access is intentionally disabled; CloudFront is the public HTTPS entry.

### Environment reminders

- Frontend runtime config is still file-based for local development and deployment syncing.
- The backend expects the Lambda package to include `fast_thinker.py`, `template_router.py`, `slow_thinker.py`, `dialogue_thinker.py`, and the `config/` directory.
- The image manager output directory contains generated assets and prompt batches; treat them as derived artifacts unless the user asks to promote them.

## 5. What To Watch

- Keep public docs free of secrets.
- Be careful with real account IDs, bucket names, distribution IDs, and endpoint URLs in deployment scripts.
- If you touch the frontend config flow, verify both local `file://` usage and CloudFront-hosted usage.
- If you touch dialogue behavior, preserve the hard turn cap and the 100-character input limit.
- If you touch the prompt assembly layer, re-check the benchmark assumptions in `backend/benchmark/`.
- If you move files again, update this handoff first so the next agent does not have to guess the new map.

## 6. Planned Deployment Targets

These are the most likely next things to ship, based on the current codebase:

1. Make runtime config generation more automatic during deployment instead of editing config stubs by hand.
2. Keep refining the final dialogue path, especially lockout text, turn caps, and fallback replies.
3. Add more benchmark scenarios and keep the summary artifacts reproducible.
4. Continue tightening prompt wording so the model receives a behavioral skeleton, not a card dump.
5. Keep polishing the frontend runtime experience, especially background treatment, audio defaults, and timing of overlays.
6. Expand the asset pipeline only if the new outputs can still be deployed statelessly through the existing S3 + CloudFront setup.
7. If deployment scripts change, preserve the ownership split: frontend sync stays in `frontend/deploy_to_s3.sh`, backend packaging stays in `infra/deploy_backend.sh`.

## 7. Safe Edit Order

1. Read the relevant README and the backend or frontend entrypoint for the area you are changing.
2. Edit the smallest possible file set.
3. Run the narrowest useful validation:
   - `git diff --check`
   - backend tests in `the-norn-machine/backend/tests/`
   - SVG validation with `xmllint --noout`
4. Re-check that the stateless boundary still holds.
5. Update both READMEs if the user-facing behavior changed.
