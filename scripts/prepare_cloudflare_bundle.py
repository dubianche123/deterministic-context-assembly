#!/usr/bin/env python3
"""Package the authoritative Python sources and offline assets for Workers.

Only allowlisted runtime files enter the generated bundle. No repository history,
local recordings, credentials, build tools or original image archive is uploaded.

The main deployment defaults to the PAM∃LA deck and v6 engine. The Worker reads
the same deck from its NORN_DECK binding. `--deck classic` keeps the original
demo cards available for local checks. `--deployment research` is an optional
isolated build target, not a separate public product.
"""

import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import shutil
import zipfile


ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "cloudflare" / ".generated"
RESEARCH_TARGET = ROOT / "cloudflare-research" / ".generated"
DECKS = ("pamela-v1",)


def prepare(deck="pamela-v1", *, deployment="main"):
    if deck == "classic":
        deck = None
    if deck is not None and deck not in DECKS:
        raise ValueError("Unknown research deck")
    if deployment not in ("main", "research"):
        raise ValueError("Unknown deployment target")
    generated = TARGET if deployment == "main" else RESEARCH_TARGET
    worker = generated / "worker"
    assets = generated / "assets"
    # Remove only our generated output, so deleted source assets cannot linger
    # into a later deployment and bypass the allowlist below.
    for directory in (worker, assets):
        if directory.exists():
            shutil.rmtree(directory)
    worker.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    files = {}
    for path in sorted((ROOT / "norn").glob("*.py")):
        files["norn/" + path.name] = path.read_bytes()
    engine = ROOT / "the-norn-machine" / "backend"
    for path in sorted(engine.glob("*.py")):
        files["norn/engine/" + path.name] = path.read_bytes()
    for path in sorted((engine / "config").rglob("*.json")):
        files["norn/engine/" + path.relative_to(engine).as_posix()] = path.read_bytes()
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    bootstrap = '''"""Generated runtime package; rebuild with prepare_cloudflare_bundle.py."""
import base64
from io import BytesIO
from pathlib import Path
import sys
import zipfile

_DATA = """%s"""
_ready = False

def bootstrap():
    global _ready
    root = Path("/tmp/norn-runtime")
    # Deployment snapshots retain Python memory, while the temporary filesystem
    # may be recreated. Never trust the snapshotted flag without checking data.
    if _ready and (root / "norn" / "engine" / "config" / "preference_features.json").is_file():
        return
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(base64.b64decode(_DATA))) as archive:
        archive.extractall(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    _ready = True
''' % encoded
    entry = ROOT / "cloudflare" / "src" / "entry.py"
    if entry.exists():
        # cf beta prefixes additional module names with "./". Python's deployed
        # import resolver did not find that sibling module during validation.
        # Embed the same generated bootstrap once in the entrypoint instead;
        # the shared engine still comes from the single allowlisted ZIP above.
        source = entry.read_text(encoding="utf-8")
        marker = "from norn_bundle import bootstrap"
        if source.count(marker) != 1:
            raise ValueError("Worker entrypoint must have one bootstrap import marker")
        (worker / "entry.py").write_text(source.replace(marker, bootstrap), encoding="utf-8")
    frontend = ROOT / "the-norn-machine" / "frontend"
    allowed = {"index.html", "style.css", "app.js", "i18n.js", "play-comparison.js", "last-card.js", "runtime-config.js"}
    for path in sorted(frontend.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(frontend)
        # The pilot's new images ship too; the page shows them only with ?pilot=… and consent.
        # The display features let mixed rounds rank cards in the browser. A research
        # build ships its own deck instead, and no build ships another deck.
        norn_files = ("data/cards.json", "data/probe-new-material-v1.json", "data/display-features-v1.json")
        if (relative.as_posix() in allowed or path.suffix == ".mp3"
                or deck is None and (relative.as_posix() in norn_files
                                     or relative.parts[:2] in (("assets", "cards"), ("assets", "probe"))
                                     and path.suffix == ".webp")
                or deck is not None and (relative.as_posix() == f"data/decks/{deck}.json"
                                         or relative.parts[:3] == ("assets", "decks", deck)
                                         and (path.suffix == ".webp" or path.name == "ATTRIBUTION.md"))):
            target = assets / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    import ast
    consent = next(node.value.value for node in ast.parse((ROOT / "norn" / "collection.py").read_text()).body
                   if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CONSENT_VERSION")
    # The archive Durable Object is part of the deployment, so the welcome
    # screen offers the weaving choice there too.
    # displayMixedShare is the display experiment's randomization (engine display_policy);
    # 0 is the fallback switch back to random rounds only.
    config = {"apiEndpoint": "/api", "provider": "openai-compatible", "model": "deepseek-flash",
              "offline": False, "hosting": "cloudflare", "collect": True, "consentVersion": consent,
              "displayMixedShare": 0.5, "lastCard": bool(deck), **({"deck": deck} if deck else {})}
    (assets / "runtime-config.js").write_text(
        "window.__NORN_CONFIG__ = " + json.dumps(config) + ";\n", encoding="utf-8")
    asset_headers = (
        "/*\n"
        "  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: no-referrer\n"
        "  X-Frame-Options: DENY\n"
        "  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; media-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'\n"
    )
    # Matching _headers rules append duplicate header values. A global no-cache
    # would defeat card caching even when a more specific immutable rule exists.
    for path in sorted(assets.rglob("*")):
        if path.is_file() and path.suffix != ".webp" and path.name != "_headers":
            route = "/" + path.relative_to(assets).as_posix()
            asset_headers += route + "\n  Cache-Control: no-cache\n"
            if route == "/index.html":
                asset_headers += "/\n  Cache-Control: no-cache\n"
    asset_headers += "/assets/cards/*\n  Cache-Control: public, max-age=604800, immutable\n"
    asset_headers += "/assets/probe/*\n  Cache-Control: public, max-age=604800, immutable\n"
    asset_headers += "/assets/decks/*\n  Cache-Control: public, max-age=604800, immutable\n"
    (assets / "_headers").write_text(asset_headers, encoding="utf-8")
    return {"python_files": len(files), "python_zip_bytes": len(output.getvalue()),
            "image_count": len(list((assets / "assets" / "cards").glob("*.webp"))),
            "probe_image_count": len(list((assets / "assets" / "probe").glob("*.webp"))),
            "deck_image_count": len(list((assets / "assets" / "decks").rglob("*.webp"))),
            "generated": str(generated)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--deck", choices=(*DECKS, "classic"), default="pamela-v1",
                        help="deck to ship (default: pamela-v1)")
    parser.add_argument("--deployment", choices=("main", "research"), default="main",
                        help="build destination (default: existing main Worker)")
    args = parser.parse_args()
    print(json.dumps(prepare(args.deck, deployment=args.deployment), ensure_ascii=False))
