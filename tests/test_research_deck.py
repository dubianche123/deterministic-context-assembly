"""The PAM∃LA research deck: deck integrity, display v2 rules and reference, research-v6 reading, service path."""

from copy import deepcopy
import json
from pathlib import Path
import random
import unittest

from norn.core import engine_module
from norn.events import EVENTS_SCHEMA_VERSION, replay_events
from norn.service import NornService, RequestError

research = engine_module("research_deck")
session_context = engine_module("session_context")
DECK = research.load_deck("pamela-v1")
ROOT = Path(__file__).resolve().parents[1]


def session(policy, rounds=10, seed=5, keep=lambda x2: x2[1] > 0):
    """A v5 research-deck journal drawn by the reference rule; the visitor keeps cards that satisfy keep."""
    rng, events, history = random.Random(seed), [], []

    def add(kind, **fields):
        events.append({"event_id": f"evt-{len(events) + 1:06d}", "sequence": len(events) + 1, "type": kind, **fields})

    for number in range(1, rounds + 1):
        count = rng.randint(4, 6)
        rows = research.observations({"events": events})
        available = engine_module("display_policy").available_cards(DECK.ids, history, count)
        cards, roles = research.draw(policy, rows, available, count, rng, DECK)
        history.append(cards)
        layout = [[i // 2, i % 2] for i in range(len(cards))]
        add("round_started", round=number, card_ids=cards, display={"policy": policy, "deck": DECK.label, "roles": roles})
        add("round_ready", round=number, ready_offset_ms=0, unloaded_card_ids=[], layout=layout)
        moment = 0
        for card in cards:
            if keep(DECK.x2[card]):
                moment += 100
                add("card_selected", round=number, card_id=card, t_ms=moment, input="mouse", layout=layout)
        add("round_completed", round=number, duration_ms=moment + 500, hidden_ms=0, seen_card_ids=None, hover=None)
    add("session_completed")
    return {"schema_version": EVENTS_SCHEMA_VERSION, "events": events}


class DeckTests(unittest.TestCase):
    def test_deck_integrity_and_browser_copy(self):
        self.assertEqual(len(DECK.ids), 256)
        self.assertTrue(all(len(v) == 9 for v in DECK.x2.values()))
        self.assertEqual(DECK.document["source"]["license"], "CC BY 4.0")
        tampered = deepcopy(DECK.document)
        tampered["cards"]["PAM_0001"]["u"] += 1
        with self.assertRaisesRegex(ValueError, "hash"):
            research.Deck(tampered)
        shipped = json.loads((ROOT / "the-norn-machine" / "frontend" / "data" / "decks" / "pamela-v1.json").read_text())
        self.assertEqual(shipped["deck_version"], DECK.version)
        self.assertEqual({c["id"]: c["x2"] for c in shipped["cards"]}, DECK.x2)
        self.assertTrue(all((ROOT / "the-norn-machine" / "frontend" / c["url"]).is_file() for c in shipped["cards"]))


class DisplayV2Tests(unittest.TestCase):
    def test_reference_sessions_replay_and_verify(self):
        for policy in research.POLICIES:
            with self.subTest(policy=policy):
                journal = replay_events(session(policy), cards=DECK.cards)["journal"]
                check = research.verify(journal, DECK)
                self.assertEqual(check["failures"], [])
                self.assertEqual(check["mixed_rounds_checked"], 10 if policy == research.MIXED else 0)
                self.assertEqual(research.arm(journal), research.ARMS[policy])
        with self.assertRaises(ValueError):
            replay_events(session(research.MIXED))  # Norn's own deck does not know these cards

    def test_role_rules_and_tampering(self):
        journal = session(research.MIXED, rounds=8)
        broken = {
            "too few predicted": lambda d: d["roles"].__setitem__(d["roles"].index("predicted"), "random"),
            "two popular": lambda d: d["roles"].__setitem__(d["roles"].index("contrast_unpopular"), "contrast_popular"),
            "unknown deck": lambda d: d.update(deck="lapis-v1@sha256:0"),
            "missing deck": lambda d: d.pop("deck"),
        }
        for name, change in broken.items():
            with self.subTest(case=name):
                copy = deepcopy(journal)
                change(copy["events"][0]["display"])
                with self.assertRaises(ValueError):
                    replay_events(copy, cards=DECK.cards)
        fallback = deepcopy(journal)
        fallback["events"][0]["display"]["roles"] = ["random"] * len(fallback["events"][0]["card_ids"])
        replay_events(fallback, cards=DECK.cards)
        swapped = deepcopy(journal)
        start = [e for e in swapped["events"] if e["type"] == "round_started"][5]
        roles = start["display"]["roles"]
        popular, unpopular = roles.index("contrast_popular"), roles.index("contrast_unpopular")
        roles[popular], roles[unpopular] = "contrast_unpopular", "contrast_popular"
        self.assertIn({"round": 6, "reason": "contrast_outside_quarter"}, research.verify(swapped, DECK)["failures"])
        stale = deepcopy(journal)
        for event in stale["events"]:
            if event["type"] == "round_started":
                event["display"]["deck"] = "pamela-v1@sha256:" + "0" * 64
        self.assertFalse(research.verify(stale, DECK)["verified"])


class ReadingTests(unittest.TestCase):
    def test_profile_and_context_follow_the_gate(self):
        strong = replay_events(session(research.MIXED, rounds=16, keep=lambda x2: x2[1] > 0.3), cards=DECK.cards)
        profile = research.build_profile(strong["journal"], DECK, strong["events_hash"])
        preference = profile["analysis"]["preference"]
        self.assertIn(preference["status"], ("sensitivity", "personal"))
        for language in ("zh", "en"):
            context = research.build_context(profile, DECK, language=language)
            prompt = session_context.build_analysis_prompt(context)
            self.assertIn("SessionProfileBlock", prompt["system"])
            self.assertTrue(context["block"]["preference"]["drawn_to"])
        quiet = replay_events(session(research.RANDOM, rounds=3, keep=lambda x2: False), cards=DECK.cards)
        empty = research.build_profile(quiet["journal"], DECK, quiet["events_hash"])
        self.assertEqual(empty["analysis"]["preference"]["status"], "population")
        block = research.build_context(empty, DECK)["block"]
        self.assertEqual((block["preference"]["drawn_to"], block["preference"]["next_to_try"]), ([], []))


class ServiceTests(unittest.TestCase):
    def test_a_research_service_reads_only_its_deck(self):
        service = NornService(provider="mock", deck="pamela-v1")
        result = service.analyze({"journal": session(research.MIXED, rounds=6)})
        self.assertTrue(result["reading"] and result["session_id"])
        self.assertEqual(result["algorithm_version"], research.ALGORITHM_VERSION)
        plan = service._prepare_analysis({"journal": session(research.RANDOM, rounds=6)})
        self.assertEqual({k: plan["experiments"][k] for k in ("display", "deck")}, {"display": "random", "deck": "pamela-v1"})
        exported = service.export_session(result["session_id"])
        self.assertEqual(exported["profile"]["deck"], DECK.label)
        norn_journal = json.loads((ROOT / "tests" / "fixtures" / "replay-session.json").read_text())
        with self.assertRaises(RequestError):
            service.analyze({"journal": norn_journal})
        with self.assertRaises(RequestError):
            service.preview({"journal": norn_journal})
        with self.assertRaises(RequestError):
            NornService(provider="mock").analyze({"journal": session(research.MIXED, rounds=3)})


class ExplanationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = NornService(provider="mock", deck="pamela-v1")
        cls.journal = session(research.RANDOM, rounds=4)
        cls.reading = cls.service.analyze({"journal": cls.journal})
        cls.bundle = cls.service.export_session(cls.reading["session_id"])

    def test_export_has_lossless_independent_contribution_ledger(self):
        from norn.events import content_hash
        from norn.replay import replay_document
        ledger = self.bundle["contribution_ledger"]
        self.assertEqual(ledger["schema_version"], "norn.research-contributions.v1")
        self.assertEqual(len(ledger["cards"]), len(DECK.ids))
        self.assertEqual(ledger["ledger_hash"], content_hash({k: v for k, v in ledger.items() if k != "ledger_hash"}))
        for row in ledger["cards"]:
            self.assertEqual(row["terms"], [w * x for w, x in zip(ledger["weights"], row["features"])])
            self.assertEqual(sum(row["terms"]), row["score"])
        reference = research.build_profile(self.bundle["journal"], DECK, self.bundle["profile"]["events_hash"])
        self.assertEqual(self.bundle["profile"], reference)  # Frozen profile is not changed by explanations.
        self.assertEqual(replay_document(self.bundle), self.bundle)
        self.assertEqual(replay_document(self.journal), self.bundle)
        corrupted = deepcopy(self.bundle)
        corrupted["contribution_ledger"]["cards"][0]["terms"][0] += 1
        # Rehashing fabricated terms must not turn them into verified evidence.
        bad = corrupted["contribution_ledger"]
        bad["ledger_hash"] = content_hash({k: v for k, v in bad.items() if k != "ledger_hash"})
        with self.assertRaisesRegex(ValueError, "Contribution ledger"):
            replay_document(corrupted)
        corrupted = deepcopy(self.bundle)
        corrupted["profile"]["analysis"]["preference"]["effective"][0] += .1
        with self.assertRaisesRegex(ValueError, "profile"):
            replay_document(corrupted)

    def test_product_context_scopes_reference_raters_in_both_languages(self):
        from norn.events import canonical_json, content_hash
        from norn.research_inspection import build_context
        for language in ("zh", "en"):
            for relation in ("not_established", "follows_more", "follows_less", "against", "personal"):
                with self.subTest(language=language, relation=relation):
                    profile = deepcopy(self.bundle["profile"])
                    profile["analysis"]["preference"]["relation"] = relation
                    original = research.build_context(profile, DECK, language=language)
                    context = build_context(profile, DECK, language=language)
                    block = context["block"]
                    self.assertEqual(block["evidence"], original["block"]["evidence"])
                    self.assertEqual(context["profile_hash"], profile["profile_hash"])
                    self.assertEqual(context["canonical_block"], canonical_json(block))
                    self.assertEqual(context["context_hash"], content_hash(block))
                    text = canonical_json(block)
                    self.assertNotIn("大多数人", text)
                    self.assertNotIn("most people", text)
                    self.assertNotIn("大家普遍", text)
                    self.assertIn("reference raters" if language == "en" else "参考评分者", text)
            plan = self.service._prepare_analysis({"journal": self.journal, "language": language})
            self.assertEqual(plan["context"], build_context(plan["bundle"]["profile"], DECK, language=language))
        full = self.service.inspect_session(self.reading["session_id"])
        self.assertNotIn("大多数人", full["context"]["canonical_block"])
        preview = self.service.preview({"journal": self.journal})
        self.assertNotIn("大多数人", preview["context"]["canonical_block"])

    def test_live_session_and_import_share_one_read_only_trajectory(self):
        service = self.service
        live = service.trajectory({"journal": self.journal})
        imported = service.trajectory(self.bundle)
        sealed = service.session_trajectory(self.reading["session_id"])
        self.assertEqual(live["states"], imported["states"])
        self.assertEqual(live["states"], sealed["states"])
        self.assertEqual(live["schema_version"], "norn.research-trajectory.v1")
        self.assertEqual(len(live["states"]), 5)
        self.assertEqual(live["provider_calls"], 0)
        self.assertTrue(imported["verified"])
        for state in live["states"]:
            self.assertEqual(len(state["scores"]), 256)
            if state["layer"] == "population":
                self.assertEqual(state["weights"], [1.0] + [0.0] * 8)
            for row in state["contributions"]:
                self.assertEqual(sum(row["terms"]), row["score"])
                self.assertEqual(row["score"], state["scores"][DECK.ids.index(row["card_id"])])
        frozen = self.bundle["profile"]["analysis"]["preference"]
        final = live["states"][-1]
        self.assertEqual(final["weights"], frozen["effective"])
        self.assertEqual(final["log_evalues"], frozen["log_evalues"])
        self.assertEqual(service.replay(self.bundle)["contribution_ledger"], self.bundle["contribution_ledger"])
        self.assertEqual(service.inspect_session(self.reading["session_id"])["profile"], self.bundle["profile"])

    def test_open_round_cannot_change_the_fitted_trajectory(self):
        # Use a real validated prefix ending after a click, before round_completed.
        events = self.journal["events"]
        last_start = max(i for i, event in enumerate(events) if event["type"] == "round_started")
        prefix = {"schema_version": EVENTS_SCHEMA_VERSION, "events": events[:last_start]}
        open_round = {"schema_version": EVENTS_SCHEMA_VERSION, "events": events[:-2]}
        before = self.service.trajectory({"journal": prefix})
        after = self.service.trajectory({"journal": open_round})
        self.assertTrue(after["open_round"])
        self.assertFalse(after["complete"])
        self.assertEqual(before["states"], after["states"])
        self.assertFalse(self.service.preview({"journal": open_round, "compact": True})["complete"])

    def test_another_deck_version_is_rejected_by_every_entry(self):
        journal = deepcopy(self.journal)
        for event in journal["events"]:
            if event["type"] == "round_started":
                event["display"]["deck"] = "pamela-v1@sha256:" + "0" * 64
        for method in (self.service.preview, self.service.trajectory):
            with self.subTest(method=method.__name__), self.assertRaises(RequestError):
                method({"journal": journal})

    def test_active_gate_scores_reconstruct_and_cli_replays_offline(self):
        from contextlib import redirect_stdout
        import io
        import tempfile
        from unittest.mock import patch
        from norn.cli import main
        from norn.research_inspection import build_bundle
        journal = session(research.MIXED, rounds=16, keep=lambda x2: x2[1] > .3)
        bundle = build_bundle(journal, DECK)
        ledger = bundle["contribution_ledger"]
        self.assertIn(ledger["layer"], ("personal", "sensitivity"))
        path = self.service.trajectory(bundle)
        self.assertEqual(path["states"][-1]["weights"], ledger["weights"])
        scores = dict(zip(DECK.ids, path["states"][-1]["scores"]))
        self.assertTrue(all(sum(row["terms"]) == scores[row["card_id"]] for row in ledger["cards"]))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "v6.json"
            source.write_text(json.dumps(bundle), encoding="utf-8")
            output = io.StringIO()
            with patch("norn.providers.generate", side_effect=AssertionError("model call")), \
                    patch("socket.create_connection", side_effect=AssertionError("network")), redirect_stdout(output):
                self.assertEqual(main(["replay", str(source), "--json"]), 0)
            summary = json.loads(output.getvalue())
            self.assertTrue(summary["verified"])
            self.assertEqual(summary["contribution_ledger_hash"], ledger["ledger_hash"])

    def test_consented_pamela_rating_export_replay_and_withdrawal(self):
        import tempfile
        from norn.actor_state import dump_actor_state, new_actor_service
        from norn.collection import CONSENT_VERSION, SQLiteCollector, visitor_id
        from norn.events import canonical_json
        from norn.replay import replay_document
        visitor = "pamela-integration-visitor-00000001"
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteCollector(Path(directory) / "records.sqlite3")
            self.addCleanup(store.close)
            service = new_actor_service(clock=lambda: 1000, deck="pamela-v1")
            service.collection = store
            result = service.analyze({"journal": self.journal, "consent": CONSENT_VERSION,
                                      "visitor": visitor, "request_id": "pamela-collected"})
            key = result["session_id"]
            before = service.export_session(key)
            closest = before["profile"]["analysis"]["attribution"]["selections"][0]["card_id"]
            self.assertTrue(closest.startswith("PAM_"))
            feedback = service.record_feedback({"session_id": key, "reading_rating": 4,
                                                "closest_card_id": closest})
            self.assertTrue(feedback["saved"])
            [record] = store.page()
            self.assertEqual(record["visitor_id"], visitor_id(visitor))
            self.assertEqual(record["experiments"]["deck"], "pamela-v1")
            self.assertEqual(record["journal"], before["journal"])
            self.assertEqual(record["annotations"]["post_session"], feedback["post_session"])
            state = json.loads(canonical_json(dump_actor_state(service)))
            restored = new_actor_service(state, clock=lambda: 1000, deck="pamela-v1")
            restored.collection = store
            exported = restored.export_session(key)
            self.assertEqual(exported["profile"], before["profile"])
            self.assertEqual(exported["contribution_ledger"], before["contribution_ledger"])
            self.assertEqual(exported["post_session"]["closest_card_id"], closest)
            self.assertEqual(replay_document(exported), exported)
            self.assertTrue(restored.replay(exported)["verified"])
            selected = {row["card_id"] for row in before["profile"]["analysis"]["attribution"]["selections"]}
            unselected = next(card for card in DECK.ids if card not in selected)
            corrupted = deepcopy(exported)
            corrupted["post_session"]["closest_card_id"] = unselected
            with self.assertRaisesRegex(ValueError, "selected cards"):
                replay_document(corrupted)
            receipt = result["record"]
            with self.assertRaises(RequestError) as caught:
                restored.withdraw_record({"visitor": "another-pamela-visitor-00000002", **receipt})
            self.assertEqual(caught.exception.status, 403)
            self.assertEqual(store.stats()["records"], 1)
            self.assertEqual(restored.withdraw_record({"visitor": visitor, **receipt}), {"withdrawn": True})
            self.assertEqual(store.stats()["records"], 0)
            self.assertEqual(restored.withdraw_record({"visitor": visitor, **receipt}), {"withdrawn": True})


if __name__ == "__main__":
    unittest.main()
