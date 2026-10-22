"""Public adapter for the same output contract used by legacy Bedrock."""

from .core import engine_module

_guardrails = engine_module("output_guardrails")
validate_output = _guardrails.validate_output
output_reason_codes = _guardrails.output_reason_codes
FALLBACK_TEXT = _guardrails.FALLBACK_TEXT
