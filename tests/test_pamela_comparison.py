"""Honest conditional comparisons over completed-round exposure sets."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from norn.core import engine_module
from norn.events import EVENTS_SCHEMA_VERSION, content_hash, replay_events
from norn.pamela_comparison import (SCOPE, attach_to_trajectory, options_hash, prediction,
                                    prepare_comparison, round_metadata)
from norn.research_inspection import trajectory
from norn.service import NornService, RequestError


RESEARCH = engine_module("research_deck")
MODEL = engine_module("research_model")
DECK = RESEARCH.load_deck("pamela-v1")


def journal(*, count=4, choices=((0, 1), (), (2,)), measured=True, ready=True, seen=None, unloaded=()):
    events = []

    def add(kind, **values):
        number = len(events) + 1
        events.append({"event_id": f"evt-{number:06d}", "sequence": number, "type": kind, **values})

    for number, chosen in enumerate(choices, 1):
        cards = DECK.ids[(number - 1) * count:number * count]
        layout = [[i // 2, i % 2] for i in range(count)] if measured else None
        add("round_started", round=number, card_ids=cards,
            display={"policy": RESEARCH.RANDOM, "deck": DECK.label, "roles": ["random"] * count})
        if ready:
            add("round_ready", round=number, ready_offset_ms=0,
                unloaded_card_ids=[cards[i] for i in unloaded], layout=layout)
        for order, index in enumerate(chosen, 1):
            add("card_selected", round=number, card_id=cards[index], t_ms=100 * order,
                input="mouse", layout=layout)
        add("round_completed", round=number, duration_ms=1000, hidden_ms=0,
            seen_card_ids=[cards[i] for i in seen] if seen is not None else None, hover=None)
    add("session_completed")
    return {"schema_version": EVENTS_SCHEMA_VERSION, "events": events}


class PamelaComparisonTests(unittest.TestCase):
    def test_prefix_state_and_options_are_shared_without_target_leakage(self):
        source = journal()
        replay = replay_events(source, cards=DECK.cards)
        rows = RESEARCH.observations(source)
        # Give each prefix a different effective state. The helper must pick
        # the preceding state, including when the current target is changed.
        states = []
        for i in range(len(rows) + 1):
            weights = [1.0] + [float(i) * 0.7] + [0.0] * 7
            states.append({"weights": weights, "layer": "population" if not i else "personal",
                           "scores": [sum(w * x for w, x in zip(weights, DECK.x2[card])) for card in DECK.ids]})
        path = {"states": states, "cards": [{"id": card} for card in DECK.ids],
                "events_hash": replay["events_hash"], "algorithm_version": RESEARCH.ALGORITHM_VERSION}
        with patch.object(MODEL, "fit", side_effect=AssertionError("comparison refitted history")):
            result = attach_to_trajectory(deepcopy(path), source, DECK)
        case = result["comparison"]["rounds"][2]
        expected = prediction(states[2]["weights"], rows[2]["shown"], DECK, rows[2]["picks"][0])
        after = prediction(states[3]["weights"], rows[2]["shown"], DECK, rows[2]["picks"][0])
        self.assertEqual(case["lanes"]["learning"], expected)
        self.assertNotEqual(case["lanes"]["learning"]["scores"], after["scores"])
        self.assertEqual(case["cutoff_rounds"], 2)
        self.assertEqual(case["options_hash"], options_hash(rows[2]["shown"], DECK))
        changed = deepcopy(source)
        chosen = next(e for e in changed["events"] if e["type"] == "card_selected" and e["round"] == 3)
        chosen["card_id"] = rows[2]["shown"][0]
        other = attach_to_trajectory(deepcopy(path), changed, DECK)["comparison"]["rounds"][2]
        for lane in ("baseline", "learning"):
            for field in ("scores", "probabilities", "ranks", "order"):
                self.assertEqual(case["lanes"][lane][field], other["lanes"][lane][field])
        self.assertNotEqual(case["target"], other["target"])
        self.assertEqual(case["prefix_hash"], other["prefix_hash"])

    def test_none_multi_and_gate_fallback_do_not_invent_ml_advantage(self):
        comparison = prepare_comparison(journal(), DECK)
        first, empty, third = comparison["rounds"]
        self.assertEqual(comparison["scope"], SCOPE)
        self.assertEqual(comparison["provider_calls"], 0)
        self.assertEqual(comparison["lanes"]["jev"]["status"], "not_run")
        self.assertEqual(first["picks"], DECK.ids[:2])
        self.assertEqual(first["target"], DECK.ids[0])
        self.assertTrue(first["same_predictions"])
        self.assertEqual(first["layer_before"], "population")
        self.assertEqual(first["lanes"]["baseline"]["probabilities"],
                         first["lanes"]["learning"]["probabilities"])
        self.assertFalse(empty["scored"])
        self.assertEqual(empty["skipped_reason"], "no_pick")
        self.assertIsNone(empty["lanes"]["learning"]["target_nll"])
        self.assertEqual(empty["cumulative"]["n"], 1)
        self.assertEqual(third["cumulative"]["n"], comparison["summary"]["n"])
        self.assertEqual(comparison["summary"]["n"], 2)

    def test_counts_distinguish_readiness_cumulative_seen_and_missing_layout(self):
        source = journal(count=6, choices=((0, 2),), seen=(0, 2, 3), unloaded=(4, 5))
        case = prepare_comparison(source, DECK)["rounds"][0]
        self.assertEqual(case["candidate_ids"], [DECK.ids[i] for i in (0, 2, 3)])
        self.assertEqual(case["display"], {"planned_count": 6, "ready_loaded_count": 4,
            "ever_seen_count": 3, "ready_columns": 2, "eligible_count": 3,
            "eligibility_source": "cumulative_seen"})
        missing = prepare_comparison(journal(measured=False, choices=((),)), DECK)["rounds"][0]
        self.assertIsNone(missing["display"]["ready_columns"])
        self.assertIsNone(missing["display"]["ever_seen_count"])
        self.assertEqual(missing["display"]["eligibility_source"], "planned_fallback")
        self.assertNotIn("first_screen_count", case["display"])

    def test_fewer_than_two_eligible_cards_are_unscored_and_ties_share_rank(self):
        sparse = prepare_comparison(journal(choices=((0,),), seen=(0,)), DECK)["rounds"][0]
        self.assertFalse(sparse["scored"])
        self.assertEqual(sparse["skipped_reason"], "no_alternative")
        self.assertIsNone(sparse["lanes"]["baseline"]["target_nll"])
        tied = prediction([0.0] * 9, DECK.ids[:4], DECK, DECK.ids[2])
        self.assertEqual(tied["ranks"], [1] * 4)
        self.assertEqual(tied["probabilities"], [0.25] * 4)
        self.assertEqual(tied["top1_credit"], 0.25)

    def test_one_trajectory_fit_path_and_unshown_recommendations(self):
        source = journal()
        replay = replay_events(source, cards=DECK.cards)
        with patch.object(MODEL, "fit", wraps=MODEL.fit) as fit:
            path = trajectory(replay, DECK)
        self.assertEqual(fit.call_count, 2 * (len(path["states"])))
        self.assertIsNone(path["states"][0]["comparison"])
        shown = set()
        metadata = round_metadata(source)
        for state in path["states"][1:]:
            shown.update(metadata[state["round"]]["planned_ids"])
            self.assertFalse(set(state["unseen_top"]) & shown)
            self.assertEqual(state["comparison"]["round"], state["round"])
        prefix = deepcopy(source)
        prefix["events"].pop()
        count = len(prefix["events"]) + 1
        prefix["events"].append({"event_id": f"evt-{count:06d}", "sequence": count,
            "type": "round_started", "round": 4, "card_ids": DECK.ids[20:24],
            "display": {"policy": RESEARCH.RANDOM, "deck": DECK.label, "roles": ["random"] * 4}})
        open_path = trajectory(replay_events(prefix, cards=DECK.cards, require_complete=False), DECK)
        self.assertEqual(open_path["comparison"]["rounds"], path["comparison"]["rounds"])

    def test_saved_comparison_import_is_bound_pure_and_not_provider_authentication(self):
        from norn.pamela_compare_cli import compare_pamela
        source = journal(choices=((0,), (), (2,), (1,), (0, 2)))
        report = compare_pamela(source, train_rounds=3)
        calls = []

        def render(*args, **kwargs):
            calls.append(1)
            return {"text": "A mock reading.", "status": "ok", "provider": "mock", "provider_calls": 0}

        service = NornService(deck="pamela-v1", generate=render)
        reading = service.analyze({"journal": source})
        before = service.export_session(reading["session_id"])
        view = service.trajectory({"journal": source, "comparison_report": report})
        self.assertEqual(view["offline_comparison"], report)
        self.assertEqual(view["comparison"]["mode"], "prequential")
        self.assertEqual(view["offline_comparison"]["mode"], "frozen_prefix")
        self.assertEqual(view["provider_calls"], 0)
        self.assertFalse(view["offline_comparison_verification"]["provider_call_authenticated"])
        self.assertTrue(view["offline_comparison_verification"]["record_binding_verified"])
        self.assertEqual(service.export_session(reading["session_id"]), before)
        self.assertEqual(len(calls), 1, "Pure offline import must not regenerate a reading")
        self.assertNotIn("offline_comparison", service.trajectory({"journal": source}))
        with self.assertRaises(RequestError):
            service.trajectory({"journal": source, "comparison_report": None})
        with self.assertRaises(RequestError):
            service.trajectory({"journal": source, "comparison_report": report, "provider": "typesafe"})
        stale = deepcopy(report)
        stale["events_hash"] = "sha256:" + "0" * 64
        stale["report_hash"] = content_hash({key: value for key, value in stale.items() if key != "report_hash"})
        with self.assertRaises(RequestError):
            service.trajectory({"journal": source, "comparison_report": stale})


if __name__ == "__main__":
    unittest.main()
