"""Frozen-prefix PAMELA/Jev reports: no default network, leakage or fake lanes."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from norn.cli import main
from norn.core import engine_module
from norn.events import EVENTS_SCHEMA_VERSION, content_hash
from norn.fast_compare import compare_journal
from norn.pamela_compare_cli import (compare_pamela, render_pamela_comparison,
                                     validate_saved_report)


research = engine_module("research_deck")
DECK = research.load_deck("pamela-v1")


def journal(rounds=8, keep=lambda number, cards: cards[:1], count=4):
    events = []

    def add(kind, **fields):
        events.append({"event_id": f"evt-{len(events) + 1:06d}", "sequence": len(events) + 1,
                       "type": kind, **fields})

    for number in range(1, rounds + 1):
        cards = DECK.ids[(number - 1) * count:number * count]
        layout = [[i // 2, i % 2] for i in range(count)]
        add("round_started", round=number, card_ids=cards,
            display={"policy": research.RANDOM, "deck": DECK.label, "roles": ["random"] * count})
        add("round_ready", round=number, ready_offset_ms=0, unloaded_card_ids=[], layout=layout)
        for index, card in enumerate(keep(number, cards), 1):
            add("card_selected", round=number, card_id=card, t_ms=index * 100, input="mouse", layout=layout)
        add("round_completed", round=number, duration_ms=1000, hidden_ms=0, seen_card_ids=None, hover=None)
    add("session_completed")
    return {"schema_version": EVENTS_SCHEMA_VERSION, "events": events}


class FakeJev:
    provider, model, label = "typesafe", "jev-1.13.0", "Jev"

    def __init__(self, *, stop_only=False):
        self.calls = 0
        self.stop_only = stop_only

    def decide(self, state, questions):
        self.calls += 1
        self.state, self.questions = deepcopy(state), deepcopy(questions)
        answers = {}
        for key, question in questions.items():
            probabilities = dict.fromkeys(question["criteria"], 0 if self.stop_only else 1 / len(question["criteria"]))
            if self.stop_only:
                probabilities["__STOP__"] = 1.0
            answers[key] = {"probabilities": probabilities, "confidence": 0.5}
        return {"model": self.model, "answers": answers, "provider_calls": 1, "elapsed_ms": 12,
                "usage": {"input_tokens": 10, "output_tokens": 2}}


def rehash(report):
    report["report_hash"] = content_hash({key: value for key, value in report.items() if key != "report_hash"})
    return report


class PamelaComparisonTests(unittest.TestCase):
    def test_default_is_offline_and_saved_report_is_bound_and_detached(self):
        source = journal()
        before = deepcopy(source)
        with patch("socket.create_connection", side_effect=AssertionError("Must stay offline")), \
                patch("norn.jev.JevClient", side_effect=AssertionError("Must not create a remote client")):
            report = compare_journal(source, train_rounds=4)
        self.assertEqual(source, before)
        self.assertEqual(report["mode"], "frozen_prefix")
        self.assertEqual(report["lanes"]["jev"]["status"], "not_run")
        self.assertEqual(report["provider_calls"], 0)
        self.assertEqual(report["training"]["cutoff"], 4)
        self.assertEqual([case["round"] for case in report["rounds"]], [5, 6, 7, 8])
        self.assertTrue(all(case["cutoff_rounds"] == 4 for case in report["rounds"]))
        restored = validate_saved_report(report, source, DECK)
        self.assertEqual(restored, report)
        self.assertEqual(validate_saved_report(json.dumps(report), source, DECK), report)
        restored["training"]["effective_weights"][0] += 1
        self.assertNotEqual(restored, report)

    def test_raw_text_import_rejects_duplicate_fields_and_nonfinite_numbers(self):
        source = journal()
        report = compare_pamela(source, train_rounds=4)
        text = json.dumps(report)
        with self.assertRaises(ValueError):
            validate_saved_report('{"mode":"prequential",' + text[1:], source, DECK)
        with self.assertRaises(ValueError):
            validate_saved_report(text.replace('"provider_calls": 0', '"provider_calls": NaN', 1), source, DECK)

    def test_heldout_choices_never_change_training_or_remote_request(self):
        first = journal()
        second = journal(keep=lambda number, cards: cards[:1] if number <= 4 else cards[1:2])
        left, right = FakeJev(), FakeJev()
        one = compare_pamela(first, train_rounds=4, decision_client=left)
        two = compare_pamela(second, train_rounds=4, decision_client=right)
        self.assertEqual(one["training"], two["training"])
        self.assertEqual(left.state, right.state)
        self.assertEqual(left.questions, right.questions)
        self.assertEqual(len(left.state["past_completed_rounds"]), 4)
        self.assertEqual(len(left.state["feature_names"]), 9)
        wire = json.dumps({"state": left.state, "questions": left.questions})
        for forbidden in ('"target":', '"duration_ms":', '"t_ms":', '"profile":', '"PAM_'):
            self.assertNotIn(forbidden, wire)
        for a, b in zip(one["rounds"], two["rounds"]):
            self.assertNotEqual(a["target"], b["target"])
            self.assertEqual(a["lanes"]["learning"]["probabilities"], b["lanes"]["learning"]["probabilities"])
        self.assertEqual(left.calls, 1)

    def test_remote_stop_is_conditioned_out_and_zero_card_mass_is_not_repaired(self):
        source = journal()
        client = FakeJev()
        report = compare_pamela(source, train_rounds=4, decision_client=client)
        self.assertEqual(report["lanes"]["jev"]["status"], "ok")
        self.assertEqual(report["provider_calls"], 1)
        self.assertEqual(validate_saved_report(report, source, DECK), report)
        for case in report["rounds"]:
            self.assertAlmostEqual(sum(case["lanes"]["jev"]["probabilities"]), 1)
            self.assertEqual(len(case["lanes"]["jev"]["probabilities"]), len(case["candidate_ids"]))
            self.assertNotIn("__STOP__", case["candidate_ids"])
        failed = FakeJev(stop_only=True)
        report = compare_pamela(source, train_rounds=4, decision_client=failed)
        self.assertEqual(report["lanes"]["jev"]["status"], "failed")
        self.assertTrue(all(case["lanes"]["jev"] == {"status": "failed"} for case in report["rounds"]))
        self.assertIsNone(report["summary"]["jev_nll"])
        self.assertEqual(failed.calls, 1)

    def test_none_and_no_alternative_are_unscored_without_remote_dispatch(self):
        source = journal(keep=lambda number, cards: cards[:1] if number <= 4 else [])
        client = FakeJev()
        report = compare_pamela(source, train_rounds=4, decision_client=client)
        self.assertEqual(client.calls, 0)
        self.assertEqual(report["summary"]["n"], 0)
        self.assertIsNone(report["summary"]["learning_nll"])
        self.assertTrue(all(case["skipped_reason"] == "no_pick" for case in report["rounds"]))
        self.assertEqual(validate_saved_report(report, source, DECK), report)
        report = compare_pamela(journal(count=1), train_rounds=4, decision_client=client)
        self.assertEqual(client.calls, 0)
        self.assertTrue(all(case["skipped_reason"] == "no_alternative" for case in report["rounds"]))

    def test_multiselect_target_is_first_finally_kept_and_all_choices_stay_local(self):
        source = journal(keep=lambda number, cards: [cards[2], cards[0]])
        report = compare_pamela(source, train_rounds=4)
        for case in report["rounds"]:
            self.assertEqual(case["target"], case["candidate_ids"][2])
            self.assertEqual(case["picks"], [case["candidate_ids"][2], case["candidate_ids"][0]])
        self.assertEqual(validate_saved_report(report, source, DECK), report)

    def test_tampered_and_unknown_saved_fields_are_rejected_even_after_rehash(self):
        source = journal()
        original = compare_pamela(source, train_rounds=4)
        mutations = {
            "other event record": lambda r: r.update(events_hash="sha256:" + "0" * 64),
            "different deck": lambda r: r.update(deck="pamela-v1@sha256:" + "0" * 64),
            "different cutoff": lambda r: r["training"].update(cutoff=3),
            "different candidates": lambda r: r["rounds"][0]["candidate_ids"].reverse(),
            "different target": lambda r: r["rounds"][0].update(target=r["rounds"][0]["candidate_ids"][1]),
            "fake unrun lane": lambda r: r["rounds"][0]["lanes"]["jev"].update(probabilities=[.25] * 4),
            "unknown report field": lambda r: r.update(extra=True),
            "unknown round field": lambda r: r["rounds"][0].update(extra=True),
            "unknown prediction field": lambda r: r["rounds"][0]["lanes"]["learning"].update(extra=True),
            "wrong summary": lambda r: r["summary"].update(learning_nll=0),
            "wrong probabilities": lambda r: r["rounds"][0]["lanes"]["learning"].update(probabilities=[0.25] * 4),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                value = deepcopy(original)
                mutation(value)
                rehash(value)
                with self.assertRaises(ValueError):
                    validate_saved_report(value, source, DECK)

    def test_cli_accepts_journal_facts_from_unverified_cloud_profile_and_writes_new_html(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, html = (root / name for name in ("source.json", "report.json", "report.html"))
            source.write_text(json.dumps({"schema_version": "norn.research-replay.v1", "journal": journal(),
                                          "profile": {"profile_hash": "intentionally not a verified saved profile"}}))
            with redirect_stdout(io.StringIO()), patch("socket.create_connection", side_effect=AssertionError("offline")):
                self.assertEqual(main(["compare", str(source), "--train-rounds", "4", "--output", str(output),
                                       "--html", str(html), "--language", "en"]), 0)
            report = json.loads(output.read_text())
            self.assertFalse(report["provenance"]["saved_profile_used"])
            self.assertFalse(report["provenance"]["saved_profile_verified"])
            page = html.read_text()
            self.assertIn("One history. Three predictions.", page)
            self.assertIn("Reference baseline", page)
            self.assertIn("参考基线", page)
            self.assertNotIn("Sensing", page)
            self.assertNotIn("Fixed rules", page)
            self.assertIn("data:image/webp;base64,", page)
            self.assertNotIn('src="https://', page)
            self.assertNotIn('<script src=', page)
            original = output.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["compare", str(source), "--output", str(output)]), 2)
            self.assertEqual(original, output.read_bytes())

    def test_html_user_text_cannot_end_embedded_json(self):
        report = compare_pamela(journal(), train_rounds=4)
        report["provenance"]["note"] = '</script><script>alert("bad")</script>'
        html = render_pamela_comparison(report)
        self.assertIn("\\u003c/script\\u003e", html)
        self.assertNotIn('</script><script>alert("bad")', html)


if __name__ == "__main__":
    unittest.main()
