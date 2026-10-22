"""The pilot's new-image phase: a second, separate journal of choices among unseen pictures.

After the visitor reveals, their main journal is sealed, so the model behind
the reading is fixed by construction. In pilot mode (`?pilot=new-material-v1`,
consented visitors only) the page then shows the 16 frozen new images in four
rounds before the reading appears. Those choices are a probe journal:

    {"schema_version": "norn.probe.v1", "protocol": "new-material-v1",
     "catalog_version": "sha256:…", "events": [… norn.events.v4 events …]}

It is stored in the consented record next to the main journal and never enters
the profile, the replay bundle or the reading. Its events follow the same rules
as a norn.events.v4 journal (pinned, so a later browser schema cannot reinterpret
stored probes), over the probe deck, plus the frozen design: each round shows
two whole scenes (both styles of each), and no image appears twice.
"""

from copy import deepcopy
import json
from pathlib import Path

from .core import engine_module
from .events import content_hash, replay_events

PROBE_SCHEMA_VERSION = "norn.probe.v1"
PROBE_EVENTS_SCHEMA_VERSION = "norn.events.v4"
CATALOGS = {"new-material-v1": "probe_new_material_v1.json"}
_LOADED = {}


def load_probe_catalog(protocol):
    """The frozen probe catalog for a protocol, verified against its content hash."""
    if type(protocol) is not str or protocol not in CATALOGS:
        raise ValueError("Unknown probe protocol")
    if protocol not in _LOADED:
        path = Path(engine_module("choice_model").__file__).parent / "config" / CATALOGS[protocol]
        document = json.loads(path.read_text(encoding="utf-8"))
        version = document.get("catalog_version")
        if version != content_hash({key: value for key, value in document.items() if key != "catalog_version"}):
            raise ValueError("Probe catalog content hash mismatch")
        _LOADED[protocol] = document
    return _LOADED[protocol]


def validate_probe(probe):
    """Validate a probe journal; returns (detached probe, replay of its events)."""
    if type(probe) is not dict or set(probe) != {"schema_version", "protocol", "catalog_version", "events"}:
        raise ValueError("probe must contain exactly: catalog_version, events, protocol, schema_version")
    if probe["schema_version"] != PROBE_SCHEMA_VERSION:
        raise ValueError("Unsupported probe schema version")
    catalog = load_probe_catalog(probe["protocol"])
    if probe["catalog_version"] != catalog["catalog_version"]:
        raise ValueError("probe was recorded against another probe catalog")
    cards = catalog["cards"]
    replay = replay_events({"schema_version": PROBE_EVENTS_SCHEMA_VERSION, "events": probe["events"]}, cards=cards)
    design = catalog["design"]
    shown = set()
    rounds = [event["card_ids"] for event in probe["events"] if event["type"] == "round_started"]
    if len(rounds) > design["rounds"]:
        raise ValueError("probe has more rounds than its design")
    for card_ids in rounds:
        scenes = {}
        for card in card_ids:
            scenes.setdefault(cards[card]["scene"], set()).add(cards[card]["style"])
        if (len(scenes) != design["scenes_per_round"]
                or any(styles != set(design["styles"]) for styles in scenes.values())):
            raise ValueError("each probe round must show whole scenes, both styles of each")
        if shown & set(card_ids):
            raise ValueError("a probe image cannot be shown twice")
        shown |= set(card_ids)
    return deepcopy(probe), replay


def probe_rows(probe):
    """Choice rows of a validated probe, with the same semantics as choice_model.observations:
    final picks in first-click order among cards that reached the screen, then the stop."""
    model = engine_module("choice_model")
    rows, shown, order, active = [], None, [], set()
    for event in probe["events"]:
        kind = event["type"]
        if kind == "round_started":
            shown, order, active = list(event["card_ids"]), [], set()
        elif kind == "card_selected":
            active.add(event["card_id"])
            order.append(event["card_id"])
        elif kind == "card_deselected":
            active.discard(event["card_id"])
        elif kind == "round_completed":
            rows.append(model._row(event["round"], shown, order, active, True, event["duration_ms"],
                                   event.get("seen_card_ids")))
    return rows
