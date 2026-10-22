"""Command line entry points with no mandatory third-party imports."""

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit
import webbrowser

from . import __version__
from .core import engine_module, web_root


def doctor_report():
    """Inspect local files only. Never read credentials, contact models, or mutate."""
    checks = []

    def check(name, function):
        try:
            detail = function()
            checks.append({"name": name, "ok": True, "detail": detail})
        except Exception as exc:
            checks.append({"name": name, "ok": False, "detail": str(exc)})

    def engine():
        cards = engine_module("fast_thinker")._load_cards()
        engine_module("template_router")._load_templates()
        engine_module("dialogue_thinker")._load_template()
        if not cards:
            raise ValueError("Card metadata is empty")
        return f"{len(cards)} card profiles and all prompt templates available"

    def frontend():
        root = web_root()
        for name in ("index.html", "app.js", "style.css"):
            if not (root / name).is_file():
                raise ValueError(f"Missing frontend asset: {name}")
        return "Local frontend available"

    def images():
        root = web_root()
        cards = json.loads((root / "data" / "cards.json").read_text(encoding="utf-8"))
        engine_ids = set(engine_module("fast_thinker")._load_cards())
        if {card["id"] for card in cards} != engine_ids:
            raise ValueError("Card manifest does not match engine metadata")
        for card in cards:
            url = urlsplit(card["url"])
            if url.scheme or url.netloc:
                raise ValueError(f"Card image requires a network connection: {card['id']}")
            path = (root / unquote(url.path).lstrip("/")).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"Missing local card image: {card['id']}")
        return f"All {len(cards)} card images available offline"

    def preference_features():
        model = engine_module("preference_model")
        catalog = model.load_catalog()
        if set(catalog["cards"]) != set(engine_module("fast_thinker")._load_cards()):
            raise ValueError("Preference descriptors do not match the card catalog")
        return f"Legacy replay / learn / rank: {len(catalog['cards'])} images, {len(catalog['feature_names'])} preference features"

    def choice_features():
        model = engine_module("choice_model")
        catalog = model.load_catalog()
        if set(catalog.ids) != set(engine_module("fast_thinker")._load_cards()):
            raise ValueError("Choice features do not match the card catalog")
        return f"Classic scoring v5: {len(catalog.ids)} images, {model.FEATURES} card features + STOP; retained for explicit comparisons"

    def active_deck():
        deck = engine_module("research_deck").load_deck("pamela-v1")
        root = web_root()
        browser = json.loads((root / "data/decks/pamela-v1.json").read_text(encoding="utf-8"))
        if browser["deck_version"] != deck.version or {c["id"] for c in browser["cards"]} != set(deck.ids):
            raise ValueError("PAMELA browser deck differs from engine deck")
        for card in browser["cards"]:
            url = urlsplit(card["url"])
            path = (root / unquote(url.path).lstrip("/")).resolve()
            if url.scheme or url.netloc or not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"Missing offline PAMELA image: {card['id']}")
        if not (root / "assets/decks/pamela-v1/ATTRIBUTION.md").is_file():
            raise ValueError("Missing PAMELA attribution")
        return f"Default v6: {len(deck.ids)} offline PAMELA images; population / sensitivity / personal evidence layers"

    check("engine", engine)
    check("frontend", frontend)
    check("offline_images", images)
    check("active_deck", active_deck)
    check("choice_features", choice_features)
    check("preference_features", preference_features)
    return {"version": __version__, "ok": all(item["ok"] for item in checks),
            "provider": "mock", "network_used": False, "checks": checks}


def _default_data_dir():
    return Path(os.environ.get("NORN_DATA_DIR") or Path.home() / ".norn")


def records_main(args):
    from .collection import SQLiteCollector
    if args.action in ("report", "evaluate"):
        return report_main(args)
    path = args.data_dir.expanduser() / "records.sqlite3"
    if not path.is_file():
        print(f"No records yet at {path}", file=sys.stderr)
        return 1
    store = SQLiteCollector(path)
    try:
        if args.action == "stats":
            print(json.dumps({"path": str(path), **store.stats()}, ensure_ascii=False, indent=2))
            return 0
        handle = args.output.open("x", encoding="utf-8") if args.output else sys.stdout
        count, after = 0, ""
        try:
            while True:
                page = store.page(after)
                if not page:
                    break
                for record in page:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                count += len(page)
                after = page[-1]["record_id"]
        finally:
            if args.output:
                handle.close()
        print(f"Exported {count} records", file=sys.stderr)
        return 0
    finally:
        store.close()


def report_main(args):
    """Measurement report or held-out model comparison, from an export file
    (e.g. the Cloudflare archive) or the local store."""
    from .report import build_report, format_report, read_records
    if args.input:
        records = read_records(args.input)
    else:
        from .collection import SQLiteCollector
        path = args.data_dir.expanduser() / "records.sqlite3"
        if not path.is_file():
            print(f"No records yet at {path}", file=sys.stderr)
            return 1
        store, records, after = SQLiteCollector(path), [], ""
        try:
            while page := store.page(after):
                records += page
                after = page[-1]["record_id"]
        finally:
            store.close()
    if args.action == "evaluate":
        from .collection import PARTIAL_SCHEMA_VERSION
        from .evaluate import build_evaluation, format_evaluation as text
        # A visit without a reading (a partial record) has nothing to evaluate here.
        records = [r for r in records if not (isinstance(r, dict) and r.get("schema_version") == PARTIAL_SCHEMA_VERSION)]
        report = build_evaluation(records, by=args.by, seed=args.seed, draws=args.draws)
    else:
        text = format_report
        report = build_report(records, by=args.by, control=args.control, seed=args.seed, draws=args.draws)
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(text(report) if args.output or not args.as_json
          else json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="norn", description="Run The Norn Machine on your computer.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Serve the app on 127.0.0.1 (mock by default)")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--host", choices=("127.0.0.1", "localhost", "0.0.0.0"), default="127.0.0.1")
    serve.add_argument("--allowed-host", action="append", default=[], help="Exact Host, optionally with port; required for 0.0.0.0")
    serve.add_argument("--max-model-calls", type=int, default=100, help="Real dispatch attempts per process; resets on restart")
    serve.add_argument("--max-concurrency", type=int, default=2, help="Concurrent rendering requests per process")
    providers = serve.add_mutually_exclusive_group()
    providers.add_argument("--mock", action="store_true", help="Use deterministic offline rendering (default)")
    from .providers import PROVIDERS
    providers.add_argument("--provider", choices=PROVIDERS, default="mock")
    serve.add_argument("--open", action="store_true", dest="open_browser", help="Open the local URL in a browser")
    serve.add_argument("--data-dir", type=Path, default=_default_data_dir(),
                       help="Where consented anonymous records are stored (default: $NORN_DATA_DIR or ~/.norn)")
    serve.add_argument("--no-collect", action="store_true", help="Never store records; the welcome screen offers no choice")
    serve.add_argument("--deck", choices=("pamela-v1", "classic"), default="pamela-v1",
                       help="Image deck (default: pamela-v1 with v6; classic keeps the original demo)")
    serve.add_argument("--log-level", choices=("debug", "info", "warning", "error"), default="info")
    serve.add_argument("--log-format", choices=("text", "json"), default="text")
    records = commands.add_parser("records", help="Inspect or export consented anonymous records")
    records.add_argument("action", choices=("stats", "export", "report", "evaluate"),
                         help="evaluate: compare card-feature models on held-out choices (needs numpy)")
    records.add_argument("--data-dir", type=Path, default=_default_data_dir())
    records.add_argument("--output", type=Path, help="New file: JSON Lines for export, JSON for report")
    records.add_argument("--input", type=Path, help="report: read an NDJSON export instead of the local store")
    records.add_argument("--by", help="report: split outcomes by a record field, e.g. experiments.<name>")
    records.add_argument("--control", help="report: the reference value of --by")
    records.add_argument("--seed", type=int, default=0, help="report: bootstrap seed")
    records.add_argument("--draws", type=int, default=1000, help="report: bootstrap resamples")
    records.add_argument("--json", action="store_true", dest="as_json", help="report: print JSON instead of text")
    doctor = commands.add_parser("doctor", help="Check local files without network or credentials")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    replay = commands.add_parser("replay", help="Verify an event journal or exported recording without a model")
    replay.add_argument("path", type=Path, help="JSON event journal or norn.replay.v1 bundle")
    replay.add_argument("--json", action="store_true", dest="as_json")
    replay.add_argument("--output", type=Path, help="Save the verified bundle to a new file")
    from .benchmark import add_benchmark_arguments, benchmark_main
    add_benchmark_arguments(commands.add_parser("benchmark", help="Offline context/session comparison; live requires explicit limits"))
    from .fast_compare import add_compare_arguments, compare_main
    add_compare_arguments(commands.add_parser("compare", help="Frozen-prefix rules / personal ML / optional decision API comparison"))
    from .learning import add_learning_arguments, add_rank_arguments, learning_main, rank_main
    add_learning_arguments(commands.add_parser("learn", help="Train a personal preference model from explicit local recordings"))
    add_rank_arguments(commands.add_parser("rank", help="Verify a learned checkpoint and inspect additive card rankings"))
    args = parser.parse_args(argv)
    if args.command == "records":
        return records_main(args)
    if args.command == "learn":
        return learning_main(args)
    if args.command == "rank":
        return rank_main(args)
    if args.command == "benchmark":
        return benchmark_main(args)
    if args.command == "compare":
        return compare_main(args)
    if args.command == "replay":
        from .replay import replay_document, replay_summary
        from .server import MAX_REQUEST_BYTES, _no_duplicates, _no_constant
        try:
            with args.path.open("rb") as handle:
                raw = handle.read(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("Replay file exceeds the 8 MiB limit")
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates,
                                  parse_constant=_no_constant)
            bundle = replay_document(document)
            summary = replay_summary(bundle)
            if args.output:
                with args.output.open("x", encoding="utf-8") as handle:
                    json.dump(bundle, handle, ensure_ascii=False, allow_nan=False, indent=2)
                    handle.write("\n")
        except (OSError, ValueError, RecursionError) as exc:
            print(f"Replay failed: {exc}", file=sys.stderr)
            return 1
        if args.as_json:
            print(json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2))
        else:
            print(f"Verified {summary['source']}: {summary['profile_hash']}")
            print(f"Profile {summary['profile_version']}; scoring {summary['algorithm_version']}")
            if summary["reconstructed_coords"] is not None:
                print("Reconstructed coordinates: " + json.dumps(summary["reconstructed_coords"]))
            print("Model calls: 0")
        return 0
    if args.command == "doctor":
        report = doctor_report()
        if args.as_json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            for item in report["checks"]:
                print(f"{'OK' if item['ok'] else 'FAIL'} {item['name']}: {item['detail']}")
            print("Ready: python3 -m norn serve --mock" if report["ok"] else "Repair the missing local files, then run doctor again.")
        return 0 if report["ok"] else 1
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    from .collection import open_collector
    from .logs import configure
    from .server import make_server
    configure(args.log_level, args.log_format)
    collector = open_collector(None if args.no_collect else args.data_dir)
    try:
        server = make_server(port=args.port, provider=args.provider,
                             host=args.host, allowed_hosts=args.allowed_host,
                             max_model_calls=args.max_model_calls, max_concurrency=args.max_concurrency,
                             collector=collector, deck=None if args.deck == "classic" else args.deck)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Could not start Norn: {exc}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Norn is running at {url}", flush=True)
    if args.provider == "mock":
        print("Offline mock: real profile calculation, deterministic simulated text. No API key or network required.", flush=True)
    else:
        print(f"Provider: {args.provider}. Model requests may use network and incur charges.", flush=True)
        print(f"Process limits: {args.max_model_calls} model attempts, concurrency {args.max_concurrency}. Restart resets limits.", flush=True)
    if collector.enabled:
        print(f"Anonymous records (only with visitor consent): {collector.path}", flush=True)
    else:
        print("Record collection is off.", flush=True)
    print("Press Ctrl+C to stop. Temporary sessions disappear when the server stops.", flush=True)
    try:
        if args.open_browser:
            webbrowser.open(url)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
