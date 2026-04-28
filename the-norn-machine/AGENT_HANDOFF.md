# Next Agent Handoff

Read this file first.

## 1. Current State

- Project: The Norn Machine
- Scope: serverless, stateless, cloud-native prompt engine with a static frontend and a Python Lambda backend
- Current runtime model:
  - Browser -> CloudFront/S3 -> API Gateway -> Lambda `fast_thinker` / `template_router` / `slow_thinker` -> Bedrock
  - Dialogue follow-up is handled separately by `dialogue_thinker`
- Current branch state: `main` and `feature-my-update` are kept in sync
- Current docs assets:
  - `the-norn-machine/docs/c4-dataflow.svg`
  - `the-norn-machine/Norn-machine.drawio.svg`

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
- Keep guardrails and fallback text on the code side, not in the model prompt.
- For SVG or layout work, prefer centered labels, transparent label backgrounds when possible, and no overflow beyond the viewbox.
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

## 5. What To Watch

- Keep public docs free of secrets.
- Be careful with real account IDs, bucket names, distribution IDs, and endpoint URLs in deployment scripts.
- If you touch the frontend config flow, verify both local `file://` usage and CloudFront-hosted usage.
- If you touch dialogue behavior, preserve the hard turn cap and the 100-character input limit.
- If you touch the prompt assembly layer, re-check the benchmark assumptions in `backend/benchmark/`.

## 6. Planned Deployment Targets

These are the most likely next things to ship, based on the current codebase:

1. Make runtime config generation more automatic during deployment instead of editing config stubs by hand.
2. Keep refining the final dialogue path, especially lockout text, turn caps, and fallback replies.
3. Add more benchmark scenarios and keep the summary artifacts reproducible.
4. Continue tightening prompt wording so the model receives a behavioral skeleton, not a card dump.
5. Keep polishing the frontend runtime experience, especially background treatment, audio defaults, and timing of overlays.
6. Expand the asset pipeline only if the new outputs can still be deployed statelessly through the existing S3 + CloudFront setup.

## 7. Safe Edit Order

1. Read the relevant README and the backend or frontend entrypoint for the area you are changing.
2. Edit the smallest possible file set.
3. Run the narrowest useful validation:
   - `git diff --check`
   - backend tests in `the-norn-machine/backend/tests/`
   - SVG validation with `xmllint --noout`
4. Re-check that the stateless boundary still holds.
5. Update both READMEs if the user-facing behavior changed.

