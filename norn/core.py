"""Locate the existing engine and web assets in a checkout or installed wheel."""

import importlib
import importlib.util
from pathlib import Path
import sys
import threading


_ENGINE_LOCK = threading.RLock()
_CHECKOUT = Path(__file__).resolve().parent.parent / "the-norn-machine"


def engine_module(name):
    """Import one engine module without changing the global import search path."""
    if not name.isidentifier():
        raise ValueError("Invalid engine module name")
    with _ENGINE_LOCK:
        if "norn.engine" not in sys.modules:
            try:
                importlib.import_module("norn.engine")
            except ModuleNotFoundError as exc:
                if exc.name != "norn.engine":
                    raise
                source = _CHECKOUT / "backend" / "__init__.py"
                if not source.is_file():
                    raise RuntimeError("Norn engine files are missing") from exc
                spec = importlib.util.spec_from_file_location(
                    "norn.engine", source,
                    submodule_search_locations=[str(source.parent)],
                )
                package = importlib.util.module_from_spec(spec)
                sys.modules["norn.engine"] = package
                try:
                    spec.loader.exec_module(package)
                except Exception:
                    sys.modules.pop("norn.engine", None)
                    raise
        return importlib.import_module("norn.engine." + name)


def web_root():
    """Return the static bundle belonging to this runtime."""
    spec = importlib.util.find_spec("norn.web")
    if spec and spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations))).resolve()
    root = _CHECKOUT / "frontend"
    if not root.is_dir():
        raise RuntimeError("Norn frontend files are missing")
    return root.resolve()
