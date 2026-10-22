"""Public adapters for the shared, standalone-compatible prompt projection."""

from .core import engine_module

_context = engine_module("session_context")
CONTEXT_SCHEMA_VERSION = _context.CONTEXT_SCHEMA_VERSION
PROMPT_VERSION = _context.PROMPT_VERSION
PROMPT_V4 = _context.PROMPT_V4
PROMPT_V5 = _context.PROMPT_V5
PROMPT_VERSIONS = _context.PROMPT_VERSIONS
COMMON_SYSTEM = _context.COMMON_SYSTEM
MAX_SELECTION_EVIDENCE = _context.MAX_SELECTION_EVIDENCE
MAX_REVISION_EVIDENCE = _context.MAX_REVISION_EVIDENCE
build_session_context = _context.build_session_context


def build_analysis_prompt(context):
    block = context.get("block") if isinstance(context, dict) else None
    if isinstance(block, dict) and "reading_intent" in block:
        from .portrait import build_analysis_prompt as build_product_prompt
        return build_product_prompt(context)
    return _context.build_analysis_prompt(context)


def build_dialogue_prompt(context, history, user_message, turn_count, max_turns):
    block = context.get("block") if isinstance(context, dict) else None
    if isinstance(block, dict) and "reading_intent" in block:
        from .portrait import build_dialogue_prompt as build_product_dialogue
        return build_product_dialogue(context, history, user_message, turn_count, max_turns)
    return _context.build_dialogue_prompt(context, history, user_message, turn_count, max_turns)
