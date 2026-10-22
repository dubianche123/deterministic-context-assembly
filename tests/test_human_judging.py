"""Zero-model checks for the human blind-judging set (scripts/human_judging.py)."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from scripts import human_judging as hj
from scripts.prose_eval import SCORES


RESULTS = Path(__file__).resolve().parents[1] / "benchmarks" / "results"
SESSIONS = RESULTS / "prose-eval-v2.json.sessions.json"
VERDICTS = RESULTS / "prose-eval-v3-mimo-consistency.json"


def reading(model, version, fixture, status="ok"):
    return {"model": model, "prompt_version": version, "fixture": fixture,
            "turns": [{"role": "reading", "status": status, "text": f"{model}-{version}-{fixture}"}]}


def entry(fixture, v5_side):
    return {"blind_id": f"p{fixture}", "position": fixture + 1, "pair": f"m/fixture-{fixture}",
            "model": "m", "fixture": fixture, "v5_side": v5_side,
            "judge_cache_keys": {side: f"k{fixture}{side}" for side in "AB"}}


def blind_answer(row, prefer, overclaim=(False, False)):
    """A complete answer on the page's A/B sides that prefers one version; overclaim is (v4, v5)."""
    v5 = row["v5_side"]
    v4 = "B" if v5 == "A" else "A"
    if prefer == "tie":
        better, scores = "tie", {v5: 3, v4: 3}
    else:
        better = v5 if prefer == "v5" else v4
        scores = {better: 4, ("B" if better == "A" else "A"): 2}
    return {"pair": row["blind_id"], "better": better, **{name: dict(scores) for name in SCORES},
            "overclaim": {v4: overclaim[0], v5: overclaim[1]}, "reason": ""}


def judge_row(fixture, v4_position, score, overclaim=(False, False), issue=None, cache_key=None):
    return {"model": "m", "fixture": fixture, "v4_position": v4_position, "v5_score": score,
            "overclaim": {"v4": overclaim[0], "v5": overclaim[1]}, "issue": issue, "raw_better": None,
            "cache_key": cache_key or f"k{fixture}{v4_position}"}


def page_from_html_text(html):
    return json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S).group(1))


def page_from_html(path):
    return page_from_html_text(Path(path).read_text(encoding="utf-8"))


class PairBuildingTests(unittest.TestCase):
    def test_saved_sessions_give_every_pair_with_two_ok_readings(self):
        sessions = json.loads(SESSIONS.read_text(encoding="utf-8"))
        pairs = hj.build_pairs(sessions)
        self.assertEqual([pair["pair"] for pair in pairs], [
            "cn:deepseek-v4.1-flash/fixture-3", "cn:deepseek-v4.1-flash/fixture-4", "cn:deepseek-v4.1-flash/fixture-5",
            "cn:glm-5.3-flash/fixture-0", "cn:glm-5.3-flash/fixture-4", "cn:glm-5.3-flash/fixture-5"])
        saved = {(row["model"], row["prompt_version"], row["fixture"]): row["turns"][0]["text"] for row in sessions}
        for pair in pairs:
            self.assertEqual(pair["texts"]["v4"], saved[(pair["model"], hj.PROMPT_V4, pair["fixture"])])
            self.assertEqual(pair["texts"]["v5"], saved[(pair["model"], hj.PROMPT_V5, pair["fixture"])])

    def test_failed_or_missing_readings_are_skipped(self):
        rows = [reading("m", hj.PROMPT_V4, 0), reading("m", hj.PROMPT_V5, 0),
                reading("m", hj.PROMPT_V4, 1), reading("m", hj.PROMPT_V5, 1, status="provider_unavailable"),
                reading("m", hj.PROMPT_V5, 2)]
        self.assertEqual([pair["pair"] for pair in hj.build_pairs(rows)], ["m/fixture-0"])

    def test_duplicate_reading_is_rejected(self):
        with self.assertRaises(ValueError):
            hj.build_pairs([reading("m", hj.PROMPT_V4, 0), reading("m", hj.PROMPT_V4, 0)])

    def test_saved_six_pairs_keep_original_facts_and_twelve_judge_cache_keys(self):
        key_path = RESULTS / "human-judging-v1.key.json"
        original = {path: path.read_bytes() for path in (key_path, SESSIONS, VERDICTS)}
        key = json.loads(original[key_path])
        sessions = json.loads(original[SESSIONS])
        with patch.object(hj, "facts_for", wraps=hj.facts_for) as render_facts:
            assigned = hj.check_key(key, sessions)
        self.assertEqual(len(assigned), 6)
        self.assertEqual(render_facts.call_count, len(hj.FIXTURES))
        self.assertTrue(all(call.kwargs == {"algorithm_version": "norn.scoring.v4"}
                            for call in render_facts.call_args_list))
        # check_key compares reconstructed facts directly against the hashes
        # already stored with the original page; do not regenerate the key.
        verdict_keys = {row["cache_key"] for row in json.loads(original[VERDICTS])["verdicts"]}
        recovered_keys = [value for row in assigned.values() for value in row["judge_cache_keys"].values()]
        self.assertEqual(len(recovered_keys), 12)
        self.assertEqual(set(recovered_keys), verdict_keys)
        for path, before in original.items():
            self.assertEqual(path.read_bytes(), before)


class BlindingTests(unittest.TestCase):
    def setUp(self):
        self.pairs = hj.build_pairs(json.loads(SESSIONS.read_text(encoding="utf-8")))

    def test_assignment_is_seeded_balanced_and_independent_of_order(self):
        first, again = hj.assign(self.pairs, hj.SEED), hj.assign(self.pairs, hj.SEED)
        self.assertEqual(first, again)
        self.assertEqual(sorted(row["v5_side"] for row in first), ["A", "A", "A", "B", "B", "B"])
        self.assertEqual([row["position"] for row in first], [1, 2, 3, 4, 5, 6])
        self.assertEqual(len({row["blind_id"] for row in first}), 6)
        other = hj.assign(self.pairs, "another-seed")
        self.assertNotEqual([(row["pair"], row["v5_side"]) for row in first],
                            [(row["pair"], row["v5_side"]) for row in other])
        odd = hj.assign(self.pairs[:5], hj.SEED)
        self.assertEqual(sum(row["v5_side"] == "A" for row in odd), 3)

    def test_page_shows_only_blind_labels_and_needs_no_network(self):
        assigned = hj.assign(self.pairs, hj.SEED)
        facts = {row["pair"]: f"事实 {row['fixture']}" for row in assigned}
        page = hj.page_data(assigned, facts, hj.SEED)
        html = hj.render_page(page)
        for leak in ("v4", "v5", "prompt", "glm", "deepseek", "fixture", "v5_side", "http://", "https://", " src="):
            self.assertNotIn(leak, html)
        shown = page_from_html_text(html)
        self.assertEqual(shown, page)
        for row, pair in zip(assigned, shown["pairs"]):
            self.assertEqual(list(pair["texts"]), ["A", "B"])  # key order must not follow the assignment
            self.assertEqual(pair["texts"][row["v5_side"]], row["texts"]["v5"])
            self.assertEqual(pair["texts"][hj._other(row["v5_side"])], row["texts"]["v4"])

    def test_unblinding_restores_versions_and_keeps_contradictions(self):
        for v5_side in ("A", "B"):
            row = entry(0, v5_side)
            for prefer in ("v5", "v4", "tie"):
                verdict = hj.unblind(blind_answer(row, prefer, overclaim=(True, False)), row)
                self.assertEqual(hj.human_outcome(verdict), prefer)
                self.assertEqual(verdict["v4_position"], hj._other(v5_side))
                self.assertEqual(verdict["overclaim"], {"v4": True, "v5": False})
                if prefer != "tie":
                    self.assertGreater(verdict["grounded"][prefer], verdict["grounded"]["v4" if prefer == "v5" else "v5"])
                self.assertIsNone(verdict["issue"])
        row = entry(0, "A")
        contradiction = {**blind_answer(row, "v5"), "better": "B"}
        verdict = hj.unblind(contradiction, row)
        self.assertEqual((hj.human_outcome(verdict), verdict["issue"]), ("v4", "winner_dominated_by_loser"))
        self.assertIsNone(hj.unblind({**blind_answer(row, "v5"), "better": None}, row))

    def test_edited_key_is_rejected(self):
        sessions = json.loads(SESSIONS.read_text(encoding="utf-8"))
        assigned = hj.assign(self.pairs, hj.SEED)
        rendered = [hj.facts_for(journal) for journal in hj.journals()]
        facts = {row["pair"]: rendered[row["fixture"]] for row in assigned}
        page = hj.page_data(assigned, facts, hj.SEED)
        key = hj.key_record(assigned, page, facts, [], sessions_path=SESSIONS, verdicts_path=VERDICTS)
        hj.check_key(key, sessions)
        key["pairs"][0]["v5_side"] = hj._other(key["pairs"][0]["v5_side"])
        with self.assertRaises(ValueError):
            hj.check_key(key, sessions)


class RoundTripTests(unittest.TestCase):
    """Build the page from saved readings, answer it, import: no model or network calls."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.page_path, self.key_path = self.root / "page.html", self.root / "key.json"
        page, self.key, states = hj.build(SESSIONS, VERDICTS, page_path=self.page_path, key_path=self.key_path)
        self.assertEqual(states, {"page": "written", "key": "written"})
        self.page = page_from_html(self.page_path)
        self.assertEqual(self.page, page)

    def tearDown(self):
        self.directory.cleanup()

    def export(self, prefer, name="answers.json", **changes):
        entries = {row["blind_id"]: row for row in self.key["pairs"]}
        rows = [{**blind_answer(entries[pair["id"]], prefer), "position": pair["position"],
                 "inputs": pair["inputs"], "updated_at": None} for pair in self.page["pairs"]]
        payload = {"schema_version": "norn.human-judging.answers.v1", "seed": self.page["seed"],
                   "page_digest": self.page["page_digest"], "sides": hj.LABELS, "exported_at": "2026-10-21T00:00:00Z",
                   "complete": len(rows), "pairs": len(rows), "rows": rows, **changes}
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def invoke(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = hj.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_build_records_the_assignment_and_matches_the_judge_inputs(self):
        self.assertEqual(len(self.page["pairs"]), 6)
        self.assertTrue(all(row["judge_inputs_match"] for row in self.key["pairs"]))
        _, _, states = hj.build(SESSIONS, VERDICTS, page_path=self.page_path, key_path=self.key_path)
        self.assertEqual(states, {"page": "unchanged", "key": "unchanged"})
        with self.assertRaises(FileExistsError):
            hj.build(SESSIONS, VERDICTS, seed="another-seed", page_path=self.page_path, key_path=self.key_path)

    def test_import_unblinds_answers_and_compares_with_saved_judge(self):
        output = self.root / "report.json"
        code, printed, _ = self.invoke("--import", str(self.export("v5")), "--key", str(self.key_path),
                                       "--output", str(output))
        self.assertEqual(code, 0)
        self.assertIn("pair agreement 3/3", printed)
        report = json.loads(output.read_text(encoding="utf-8"))
        summary = report["summary"]
        self.assertEqual(summary["human_outcomes"], {"v5": 6, "v4": 0, "tie": 0})
        self.assertEqual(summary["judge_outcomes"]["v5"], 3)
        self.assertEqual(summary["agreement"]["judge_order_disagreement"], 3)
        self.assertEqual((summary["agreement"]["agree"], summary["agreement"]["both_decided"]), (3, 3))
        # Each order disagreement has exactly one judge order on the human's side.
        self.assertEqual(summary["order_agreement"], {"judge_orders": 12, "agree_with_human": 9})
        for row in report["verdicts"]:
            self.assertEqual((row["judge"], row["v5_score"], row["issue"]), ("human", 1.0, None))
            self.assertGreater(row["grounded"]["v5"], row["grounded"]["v4"])
        # The import never replaces an earlier report.
        code, _, error = self.invoke("--import", str(self.export("v4", "later.json")), "--key", str(self.key_path),
                                     "--output", str(output))
        self.assertEqual(code, 2)
        self.assertIn("already exists", error)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["summary"]["human_outcomes"]["v5"], 6)

    def test_answers_for_another_page_or_other_readings_are_rejected(self):
        with self.assertRaises(ValueError):
            hj.import_answers(self.export("v5", page_digest="sha256:" + "0" * 64), key_path=self.key_path)
        path = self.export("v5")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["rows"][0]["inputs"] = {**payload["rows"][0]["inputs"], "A": payload["rows"][0]["inputs"]["B"]}
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            hj.import_answers(path, key_path=self.key_path)

    def test_verdicts_on_other_inputs_are_not_counted_as_agreement(self):
        # Same pairs by model and fixture, but judged on other facts or readings.
        report = json.loads(VERDICTS.read_text(encoding="utf-8"))
        for row in report["verdicts"]:
            row["cache_key"] = "sha256:" + "0" * 64
        swapped = self.root / "other-verdicts.json"
        swapped.write_text(json.dumps(report), encoding="utf-8")
        result = hj.import_answers(self.export("v5"), verdicts_path=swapped, key_path=self.key_path)
        self.assertEqual(result["summary"]["judge_outcomes"]["input_mismatch"], 6)
        self.assertEqual(result["summary"]["agreement"]["both_decided"], 0)
        self.assertEqual(result["summary"]["order_agreement"], {"judge_orders": 0, "agree_with_human": 0})
        self.assertTrue(all(pair["judge_inputs_match"] is False for pair in result["pairs"]))
        self.assertFalse(result["inputs"]["verdicts"]["same_file_as_key"])
        genuine = hj.import_answers(self.export("v5", "again.json"), key_path=self.key_path)
        self.assertTrue(all(pair["judge_inputs_match"] for pair in genuine["pairs"]))
        self.assertTrue(genuine["inputs"]["verdicts"]["same_file_as_key"])

    def test_facts_changed_since_the_page_was_built_are_rejected(self):
        with patch.object(hj, "facts_for", lambda journal, **kwargs: "other facts"):
            with self.assertRaisesRegex(ValueError, "other facts"):
                hj.import_answers(self.export("v5"), key_path=self.key_path)


class AgreementTests(unittest.TestCase):
    def setUp(self):
        self.entries = [entry(fixture, "AB"[fixture % 2]) for fixture in range(6)]
        answers = [("v5", (True, False)), ("v4", (False, True)), ("v5", (False, False)),
                   ("v5", (False, False)), ("tie", (False, False))]
        self.human = {row["blind_id"]: hj.unblind(blind_answer(row, prefer, flags), row)
                      for row, (prefer, flags) in zip(self.entries, answers)}
        self.human["p5"] = None  # left incomplete
        self.judge = [
            judge_row(0, "A", 1.0, (True, False)), judge_row(0, "B", 1.0, (True, False)),     # agrees
            judge_row(1, "A", 1.0, (True, False)), judge_row(1, "B", 1.0, (False, False)),    # disagrees
            judge_row(2, "A", 0.0), judge_row(2, "B", 1.0),                                   # position-A twice
            judge_row(3, "A", None, (False, True), "winner_dominated_by_loser"),
            judge_row(3, "B", 1.0, (False, True)),                                            # abstains
            judge_row(5, "A", 1.0), judge_row(5, "B", 1.0),                                   # fixture 4: none
        ]

    def test_pair_agreement_counts_abstentions_and_order_disagreements_apart(self):
        pairs = hj.compare(self.entries, self.human, self.judge)
        self.assertEqual([pair["judge"] for pair in pairs],
                         ["v5", "v5", "order_disagreement", "abstain", "missing", "v5"])
        self.assertEqual([pair["agreement"] for pair in pairs],
                         ["agree", "disagree", "judge_order_disagreement", "judge_abstained", "judge_missing",
                          "human_incomplete"])
        self.assertEqual([pair["orders_agreeing"] for pair in pairs], [2, 0, 1, 1, 0, None])
        summary = hj.summarize(pairs, self.human)
        self.assertEqual(summary["agreement"], {
            "both_decided": 2, "agree": 1, "disagree": 1, "rate": 0.5, "judge_order_disagreement": 1,
            "judge_split": 0, "judge_abstained": 1, "judge_missing": 1, "judge_input_mismatch": 0,
            "human_incomplete": 1})
        self.assertEqual(summary["human_outcomes"], {"v5": 3, "v4": 1, "tie": 1})
        self.assertAlmostEqual(summary["human_v5_win_rate"], 0.7)
        self.assertEqual(summary["judge_outcomes"], {"v5": 3, "v4": 0, "tie": 0, "order_disagreement": 1,
                                                     "split": 0, "abstain": 1, "missing": 1,
                                                     "input_mismatch": 0})
        self.assertEqual(summary["order_agreement"], {"judge_orders": 7, "agree_with_human": 4})

    def test_overclaim_agreement_needs_both_judge_orders_to_agree(self):
        pairs = hj.compare(self.entries, self.human, self.judge)
        self.assertEqual(pairs[1]["overclaim"], {"v4": {"human": False, "judge": "inconsistent"},
                                                 "v5": {"human": True, "judge": False}})
        self.assertEqual(pairs[4]["overclaim"]["v4"], {"human": False, "judge": None})
        overclaim = hj.summarize(pairs, self.human)["overclaim_agreement"]
        self.assertEqual({key: overclaim[key] for key in ("compared", "agree", "judge_inconsistent", "judge_missing")},
                         {"compared": 7, "agree": 5, "judge_inconsistent": 1, "judge_missing": 2})
        self.assertAlmostEqual(overclaim["rate"], 5 / 7)
        self.assertEqual(overclaim["table"], {"human_yes_judge_yes": 1, "human_yes_judge_no": 1,
                                              "human_no_judge_yes": 1, "human_no_judge_no": 4})

    def test_a_pair_with_any_verdict_on_other_inputs_is_excluded(self):
        human = {"p0": hj.unblind(blind_answer(self.entries[0], "v5"), self.entries[0])}
        for rows in ([judge_row(0, "A", 1.0, cache_key="stale"), judge_row(0, "B", 1.0, cache_key="stale")],
                     [judge_row(0, "A", 1.0), judge_row(0, "B", 1.0, cache_key="stale")]):
            [pair] = hj.compare(self.entries[:1], human, rows)
            self.assertEqual((pair["judge"], pair["agreement"], pair["judge_inputs_match"], pair["orders_counted"]),
                             ("input_mismatch", "judge_input_mismatch", False, 0))
        with self.assertRaises(ValueError):
            hj.compare(self.entries[:1], human, [judge_row(0, "A", 1.0), judge_row(0, "A", 0.0)])

    def test_saved_mimo_verdicts_against_a_person_who_always_prefers_v5(self):
        assigned = hj.assign(hj.build_pairs(json.loads(SESSIONS.read_text(encoding="utf-8"))), hj.SEED)
        rendered = [hj.facts_for(journal) for journal in hj.journals()]
        for row in assigned:
            row["judge_cache_keys"] = hj.judge_cache_keys(row, rendered[row["fixture"]])
        human = {row["blind_id"]: hj.unblind(blind_answer(row, "v5"), row) for row in assigned}
        verdicts = json.loads(VERDICTS.read_text(encoding="utf-8"))["verdicts"]
        outcomes = {pair["pair"]: pair["judge"] for pair in hj.compare(assigned, human, verdicts)}
        self.assertEqual(outcomes, {
            "cn:glm-5.3-flash/fixture-0": "v5", "cn:glm-5.3-flash/fixture-4": "order_disagreement",
            "cn:glm-5.3-flash/fixture-5": "order_disagreement", "cn:deepseek-v4.1-flash/fixture-3": "v5",
            "cn:deepseek-v4.1-flash/fixture-4": "order_disagreement", "cn:deepseek-v4.1-flash/fixture-5": "v5"})


if __name__ == "__main__":
    unittest.main()
