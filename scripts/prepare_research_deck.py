#!/usr/bin/env python3
"""Write the browser's copy of a research deck: card IDs, image paths and display features.

The engine deck (the-norn-machine/backend/config/decks/<deck>.json) is built in
the research workspace from the dataset. The browser needs only each card's
image and its features [u] + x8, copied exactly, plus the dataset credit and,
when the deck has one, its population context (screen-slot offsets and stop slope):

    python3 scripts/prepare_research_deck.py pamela-v1 [--check]
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from norn.core import engine_module  # noqa: E402

SCHEMA_VERSION = "norn.research-deck.browser.v1"


def output(deck):
    return ROOT / "the-norn-machine" / "frontend" / "data" / "decks" / f"{deck}.json"


def render(name):
    deck = engine_module("research_deck").load_deck(name)
    source = deck.document["source"]
    document = {"schema_version": SCHEMA_VERSION, "deck": deck.name, "deck_version": deck.version,
                "credit": f"{source['dataset']}, {source['license']}",
                "cards": [{"id": card, "url": f"./assets/decks/{deck.name}/{card}.webp", "x2": deck.x2[card]}
                          for card in deck.ids]}
    if deck.context is not None:
        document["context"] = deck.context
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("deck", choices=engine_module("research_deck").DECKS)
    parser.add_argument("--check", action="store_true", help="fail if the shipped file differs")
    args = parser.parse_args()
    path = output(args.deck)
    if args.check:
        if not path.is_file() or path.read_text(encoding="utf-8") != render(args.deck):
            sys.exit(f"{path.relative_to(ROOT)} is stale; rerun scripts/prepare_research_deck.py {args.deck}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(args.deck), encoding="utf-8")
    print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
