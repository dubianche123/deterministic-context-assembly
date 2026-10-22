"""Normalize optional JS values at the Python/Workers boundary only."""

try:
    from pyodide.ffi import jsnull as _JS_NULL
except ImportError:
    _JS_NULL = object()


def optional_js(value):
    # Modern Pyodide maps JavaScript null to jsnull, not Python None.
    return None if value is _JS_NULL else value
