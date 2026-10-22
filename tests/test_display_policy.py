"""Display policy v1: the norn.events.v5 contract, its engine reference and the recorded arm."""

from copy import deepcopy
import random
import unittest

from norn.core import engine_module
from norn.events import CHOICE_LAYOUT_EVENTS_SCHEMA_VERSION, EVENTS_SCHEMA_VERSION, replay_events
from norn.profile import build_event_profile
from norn.service import NornService

display = engine_module("display_policy")
model = engine_module("choice_model")


def session(policy, rounds=8, seed=3, keep=lambda features: features[0] > 0):
    """A v5 journal drawn by the reference rule; the visitor keeps cards that satisfy keep."""
    catalog = model.load_catalog()
    rng, events, history = random.Random(seed), [], []

    def add(kind, **fields):
        events.append({"event_id": f"evt-{len(events) + 1:06d}", "sequence": len(events) + 1, "type": kind, **fields})

    for number in range(1, rounds + 1):
        count = rng.randint(4, 6)
        rows = model.observations({"schema_version": EVENTS_SCHEMA_VERSION, "events": events}, catalog)
        cards, roles = display.draw(policy, rows, display.available_cards(catalog.ids, history, count), count, rng)
        history.append(cards)
        layout = [[i // 2, i % 2] for i in range(len(cards))]
        add("round_started", round=number, card_ids=cards, display={"policy": policy, "roles": roles})
        add("round_ready", round=number, ready_offset_ms=0, unloaded_card_ids=[], layout=layout)
        moment = 0
        for card in cards:
            if keep(catalog.features[catalog.index[card]]):
                moment += 100
                add("card_selected", round=number, card_id=card, t_ms=moment, input="mouse", layout=layout)
        add("round_completed", round=number, duration_ms=moment + 500, hidden_ms=0, seen_card_ids=None, hover=None)
    add("session_completed")
    return {"schema_version": EVENTS_SCHEMA_VERSION, "events": events}


def as_v4(journal):
    old = deepcopy(journal)
    old["schema_version"] = CHOICE_LAYOUT_EVENTS_SCHEMA_VERSION
    for event in old["events"]:
        event.pop("display", None)
    return old


class DisplayContractTests(unittest.TestCase):
    def test_display_roles_never_change_the_scoring_facts(self):
        for policy in display.POLICIES:
            with self.subTest(policy=policy):
                journal = session(policy)
                replay, old = replay_events(journal), replay_events(as_v4(journal))
                self.assertEqual(replay["input"], old["input"])
                self.assertEqual(model.observations(journal), model.observations(as_v4(journal)))
                profile = build_event_profile(journal)
                self.assertEqual(profile["analysis"]["preference"], build_event_profile(as_v4(journal))["analysis"]["preference"])
                observations = engine_module("preference_model").observations_from_journals([journal])
                self.assertEqual(len(observations), len(engine_module("preference_model").observations_from_journals([as_v4(journal)])))

    def test_malformed_display_records_are_rejected(self):
        journal = session(display.MIXED, rounds=3)
        first = journal["events"][0]
        cases = {
            "missing": lambda e: e.pop("display"),
            "unknown policy": lambda e: e["display"].update(policy="norn.display.greedy.v1"),
            "extra field": lambda e: e["display"].update(seed=1),
            "role count": lambda e: e["display"]["roles"].pop(),
            "unknown role": lambda e: e["display"]["roles"].__setitem__(0, "popular"),
            "too many predicted": lambda e: e["display"].update(roles=["predicted"] * len(e["card_ids"])),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                broken = deepcopy(journal)
                change(broken["events"][0])
                with self.assertRaises(ValueError):
                    replay_events(broken)
        switched = deepcopy(journal)
        later = next(e for e in switched["events"][1:] if e["type"] == "round_started")
        later["display"] = {"policy": display.RANDOM, "roles": ["random"] * len(later["card_ids"])}
        with self.assertRaisesRegex(ValueError, "one display policy"):
            replay_events(switched)
        random_with_prediction = session(display.RANDOM, rounds=1)
        random_with_prediction["events"][0]["display"]["roles"][0] = "predicted"
        with self.assertRaises(ValueError):
            replay_events(random_with_prediction)
        fallback = deepcopy(journal)
        fallback["events"][0]["display"]["roles"] = ["random"] * len(first["card_ids"])
        replay_events(fallback)  # a mixed round whose candidate failed is logged as all random

    def test_older_journals_keep_their_own_contract(self):
        journal = session(display.RANDOM, rounds=2)
        with self.assertRaises(ValueError):
            replay_events({**journal, "schema_version": CHOICE_LAYOUT_EVENTS_SCHEMA_VERSION})
        replay_events(as_v4(journal))
        self.assertIsNone(display.session_arm(as_v4(journal)))


class DisplayReferenceTests(unittest.TestCase):
    def test_reference_sessions_verify_and_tampering_is_caught(self):
        journal = session(display.MIXED, rounds=10)
        self.assertEqual(display.verify(journal)["failures"], [])
        self.assertEqual(display.verify(journal)["predicted_rounds_checked"], 10)
        self.assertTrue(display.verify(session(display.RANDOM))["verified"])
        catalog = model.load_catalog()
        tampered = deepcopy(journal)
        events = [e for e in tampered["events"] if e["type"] == "round_started"]
        target = events[6]
        prefix = tampered["events"][:tampered["events"].index(target)]
        scores = display.candidate_scores(model.observations({"schema_version": EVENTS_SCHEMA_VERSION,
                                                              "events": prefix}, catalog))
        roles = target["display"]["roles"]
        best = max((i for i, r in enumerate(roles) if r == "predicted"), key=lambda i: scores[target["card_ids"][i]])
        worst = min((i for i, r in enumerate(roles) if r == "random"), key=lambda i: scores[target["card_ids"][i]])
        roles[best], roles[worst] = "random", "predicted"
        self.assertEqual(display.verify(tampered)["failures"], [{"round": 7, "reason": "predicted_not_top"}])
        repeated = deepcopy(journal)
        starts = [e for e in repeated["events"] if e["type"] == "round_started"]
        starts[1]["card_ids"][0] = starts[0]["card_ids"][0]
        self.assertIn({"round": 2, "reason": "card_not_available"}, display.verify(repeated)["failures"])

    def test_predictions_follow_what_the_visitor_keeps(self):
        catalog = model.load_catalog()
        journal = session(display.MIXED, rounds=12, keep=lambda features: features[4] > 0)
        late = [card for e in journal["events"] if e["type"] == "round_started" and e["round"] > 6
                for card, role in zip(e["card_ids"], e["display"]["roles"]) if role == "predicted"]
        self.assertGreater(sum(catalog.features[catalog.index[c]][4] > 0 for c in late) / len(late), 0.8)

    def test_available_cards_restart_the_pass_like_the_browser(self):
        deck = [f"C{i}" for i in range(10)]
        self.assertEqual(display.available_cards(deck, [deck[:4], deck[4:8]], 4), deck)
        self.assertEqual(display.available_cards(deck, [deck[:4], deck[4:8]], 2), deck[8:])
        self.assertEqual(display.available_cards(deck, [deck[:4], deck[4:8], deck[:4]], 4), deck[4:])

    def test_the_browser_features_are_the_scoring_features(self):
        import json
        from pathlib import Path
        shipped = json.loads((Path(__file__).resolve().parents[1] / "the-norn-machine" / "frontend" / "data"
                              / "display-features-v1.json").read_text(encoding="utf-8"))
        catalog = model.load_catalog()
        self.assertEqual(shipped["feature_version"], catalog.feature_version)
        self.assertEqual(shipped["cards"], {card: list(catalog.features[i]) for i, card in enumerate(catalog.ids)})


class DisplayArmRecordTests(unittest.TestCase):
    def test_a_consented_record_carries_the_display_arm(self):
        for policy, arm in ((display.MIXED, "mixed"), (display.RANDOM, "random")):
            with self.subTest(arm=arm):
                service = NornService(provider="mock")
                plan = service._prepare_analysis({"journal": session(policy, rounds=3)})
                self.assertEqual(plan["experiments"]["display"], arm)
        old = NornService(provider="mock")._prepare_analysis({"journal": as_v4(session(display.RANDOM, rounds=3))})
        self.assertNotIn("display", old["experiments"])


if __name__ == "__main__":
    unittest.main()
