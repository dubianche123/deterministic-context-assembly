# Workbuddy DeepSeek request rejection

The local Workbuddy proxy returned HTTP 503 for a chat-completions request to `cn:deepseek-v4.1-flash`. Its nested upstream response was HTTP 400 / business code 11133 (`invalid_request_error`), not an observed concurrency or account-capacity rejection.

## Reproduction

Both synthetic probes used the same prompt (requesting exactly `OK`), `max_tokens=1000`, `temperature=0`, `reasoning_effort=off`, and `stream=false`, with one attempt and no retry:

- [Before model-list prewarming](workbuddy-deepseek-503-diagnostic-v1.json): HTTP 503 wrapping upstream 400 / 11133.
- [After GET /v1/models](workbuddy-deepseek-503-diagnostic-v2.json): HTTP 200, `deepseek-v4.1-flash`, `stop`, exact `OK`.

The only intervention between those requests was loading the model list. This sequential check supports a cache-dependent failure; it does not alone isolate every possible upstream change.

## Source findings

The proxy's request preparation injects DeepSeek thinking mode and normalizes reasoning effort using model capabilities. Before this fix, CN requests used only the in-memory capability cache. The cache starts empty and is populated by the model-list route. With an empty cache, `off` reached upstream unchanged; with the populated cache it became the supported `low` effort. The existing CN static catalog already lists `low`, `high`, and `max` for this model, but only global-realm request preparation used static fallback.

There was also an error-reporting defect: 11133 entered the generic client-error path, which could count an account failure and return `no_healthy_account` / 503. This misrepresented a rejected request as temporary account unavailability and encouraged unnecessary client retries.

The repair belongs in the local proxy. Norn's scoring, prompts, token limits, and public Cloudflare DeepSeek adapter need no change for these findings. The proxy still treats requested `off` as a supported minimum effort for this thinking model; this repair does not promise disabled reasoning.

## Repair and verification

The [portable source patch](workbuddy-deepseek-proxy-fix-20261021.patch) targets the local Workbuddy Manager v2.6.3 strict-smoke fork, with paths relative to its `upstream/` directory:

- Both realms use the existing static effort catalog when remote capabilities are absent, through one shared merge helper. Remote capabilities retain precedence; the global wrapper and existing behavior remain intact.
- HTTP 400 with exact structured code 11133 gets a request-error classification. Before either streaming or nonstreaming output starts, the handler returns JSON 400 / `model_param_invalid`, preserves the error body and hint, releases the lease, and leaves account health unchanged. Authentication, rate, credit and unrelated-error policies retain their existing behavior.

[Focused tests on the combined source](workbuddy-deepseek-proxy-tests-20261021.json) passed **48 top-level tests and 101 subtests**, including CN/global cold fallback, remote precedence, explicit disabled thinking, unchanged token budgets, exact-code/status guards, both stream settings, account health and lease cleanup, and existing strict-smoke regressions. The native binary built offline, replaced the local gateway atomically after an idle check, and was restarted through its existing launchd service. The prior binary was backed up privately; pool configuration remains two slots per account.

The [post-fix cold-start smoke](workbuddy-deepseek-503-fixed-v1.json) sent six requests without model-list prewarming by the harness. The gateway's inference receipts identify these as attempts 1–6 after restart, and six log records show `off → low` normalization. All six returned HTTP 200, exact `OK`, and `stop`, with no retries; total client wall time was 2.446 seconds. Proxy status observed simultaneous **2/2/2** occupancy, then zero in-flight and three healthy accounts. All six response-model fields report `deepseek-v4.1-flash`.

The 400 behavior is verified with local mock upstream errors, not by deliberately making another invalid live model request. The real smoke verifies the formerly failing request on the restarted proxy; it does not establish sustained reliability, upstream execution overlap, language quality or a speed advantage. Usage includes reasoning tokens despite requested `off`; prices and total monetary cost are unknown. The two diagnostic attempts and six post-fix attempts are retained, including the failed diagnostic. Historical prose evaluations and earlier concurrency probes are unchanged.

The [independent result review and main-agent response](workbuddy-deepseek-503-independent-review-20261021.md) support cold proxy effort normalization as the proximate cause of this reproduced incident. They do not establish that all historical 503s share this cause or that the provider universally rejects `off`.

## Kimi used by the existing evaluations

Both [prose-eval-v1.json](prose-eval-v1.json) and [prose-eval-v2.json](prose-eval-v2.json) request `cn:kimi-k2.7` as their main judge, with reasoning effort `low`. All 16 saved v1 verdicts and 12 saved v2 verdicts name K2.7 and record first-attempt success. No saved calls use the MiniMax M3 or Kimi K2.6 fallbacks. These historical artifacts omit `response_model`, so the evidence confirms the requested alias rather than independently identifying the upstream model. Current transport logs retain that field.
