"""Scoring v4: the Bayesian sequential choice model, its profile and trajectory API."""

import ast
from copy import deepcopy
import http.client
import importlib.util
import json
import math
from pathlib import Path
import threading
import unittest

from norn.benchmark import default_journal
from norn.core import engine_module
from norn.events import content_hash, replay_events
from norn.profile import (build_event_profile, build_replay_bundle, build_snapshot_profile, export_replay,
                          replay_bundle, verify_profile)
from norn.replay import replay_document
from norn.server import make_server
from norn.service import NornService, RequestError


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
# Produced by prototypes/xray-v2/build_trajectory.py with NumPy: its demo journal
# (seed 4) and the full-precision posterior after every prefix of rounds.
DEMO = json.loads((FIXTURES / "choice-model-demo.json").read_text(encoding="utf-8"))
model = engine_module("choice_model")
numeric = engine_module("numeric")


def journal(rounds, *, open_last=False, extra_event=False):
    """Round r shows four fresh cards; r % 3 decides none, one or all picks."""
    events = []
    def add(kind, **fields):
        events.append({"event_id": f"evt-{len(events) + 1:06d}", "sequence": len(events) + 1, "type": kind, **fields})
    for number in range(1, rounds + 1):
        ids = [f"NORN_{(number - 1) * 4 + index:04d}" for index in (1, 2, 3, 4)]
        add("round_started", round=number, card_ids=ids)
        for card in ids[:(0, 1, 4)[number % 3]]:
            add("card_selected", round=number, card_id=card)
        if not (open_last and number == rounds):
            add("round_completed", round=number, duration_ms=1000 + number)
    if not open_last:
        add("session_completed")
    return {"schema_version": "norn.events.v1", "events": events}


def rehash(profile):
    profile["profile_hash"] = content_hash({key: value for key, value in profile.items() if key != "profile_hash"})


class ChoiceModelTests(unittest.TestCase):
    def test_reproduces_the_numpy_prototype_on_its_demo_journal(self):
        for contract in (numeric.NATIVE_CONTRACT, numeric.PORTABLE_CONTRACT):
            with self.subTest(contract=contract), numeric.use(contract):
                states = model.path(model.observations(DEMO["journal"]))
                self.assertEqual(len(states), len(DEMO["states"]))
                for mine, reference in zip(states, DEMO["states"]):
                    covariance = mine["covariance"]
                    for actual, expected in zip(mine["theta"], reference["theta"]):
                        self.assertAlmostEqual(actual, expected, delta=1e-6)
                    for index, expected in enumerate(reference["cov_diag"]):
                        self.assertAlmostEqual(covariance[index][index], expected, delta=1e-6)
                    for row in range(4):
                        for column in range(4):
                            self.assertAlmostEqual(covariance[row][column], reference["cov4"][row][column], delta=1e-6)
                final = states[-1]["theta"]
                scores = model.card_scores(final)
                self.assertEqual(model.top_cards(scores, model.load_catalog()), DEMO["states"][-1]["top"])
                for card, expected in DEMO["final_scores"].items():
                    self.assertAlmostEqual(scores[model.load_catalog().index[card]], expected, delta=1e-6)
                # Type probabilities: 4096 fixed quasi-random draws vs the prototype's 6000 random ones.
                types = model.type_probabilities(final[:4], [row[:4] for row in states[-1]["covariance"][:4]])
                self.assertEqual(types[0]["type"], DEMO["states"][-1]["types"][0]["type"])
                self.assertAlmostEqual(types[0]["p"], DEMO["states"][-1]["types"][0]["p"], delta=0.01)

    def test_labels_are_final_picks_in_first_click_order_then_stop(self):
        ids = [f"NORN_{index:04d}" for index in range(1, 5)]
        events = [{"type": "round_started", "round": 1, "card_ids": ids}]
        for kind, card in (("card_selected", ids[0]), ("card_selected", ids[1]), ("card_deselected", ids[0]),
                           ("card_selected", ids[2]), ("card_selected", ids[0]), ("card_selected", ids[3]),
                           ("card_deselected", ids[3])):
            events.append({"type": kind, "round": 1, "card_id": card})
        events.append({"type": "round_completed", "round": 1, "duration_ms": 10})
        events.append({"type": "round_started", "round": 2, "card_ids": ["NORN_0010", "NORN_0011"]})
        events.append({"type": "round_completed", "round": 2, "duration_ms": 20})
        events.append({"type": "round_started", "round": 3, "card_ids": ["NORN_0020"]})
        events.append({"type": "card_selected", "round": 3, "card_id": "NORN_0020"})
        rows = model.observations({"events": events})
        self.assertEqual([row["picks"] for row in rows], [[ids[0], ids[1], ids[2]], []],
                         "Cards still selected at close keep their first click; withdrawals are not picks")
        self.assertTrue(all(row["stopped"] for row in rows))
        opened = model.observations({"events": events}, include_open=True)
        self.assertEqual((opened[-1]["picks"], opened[-1]["stopped"]), (["NORN_0020"], False))
        prepared = model.Prepared(opened, model.load_catalog())
        self.assertEqual([steps for _, _, steps, _ in prepared.rows], [[0, 1, 2, None], [None], [0]],
                         "A no-pick round is one stop choice; an open round has no stop yet")
        # Event types this model does not use (e.g. later journal versions) are ignored.
        events.insert(1, {"type": "card_seen", "round": 1, "card_id": ids[0]})
        self.assertEqual(model.observations({"events": events}), rows)

    def test_v2_cards_that_never_reached_the_screen_leave_the_choice_set(self):
        ids = [f"NORN_{index:04d}" for index in range(1, 5)]
        def events(seen):
            return {"events": [{"type": "round_started", "round": 1, "card_ids": ids},
                               {"type": "card_selected", "round": 1, "card_id": ids[0], "t_ms": 5},
                               {"type": "card_selected", "round": 1, "card_id": ids[1], "t_ms": 9},
                               {"type": "round_completed", "round": 1, "duration_ms": 30, "hidden_ms": 0,
                                "seen_card_ids": seen}]}
        row = model.observations(events([ids[2], ids[0]]))[0]
        self.assertEqual((row["shown"], row["picks"], row["unseen"]), ([ids[0], ids[2]], [ids[0]], [ids[1], ids[3]]))
        self.assertEqual(model.trajectory([row])["states"][1]["unseen"], [ids[1], ids[3]])
        blank = model.observations(events([]))[0]
        self.assertEqual((blank["shown"], blank["picks"]), ([], []))
        prior = model.fit(model.Prepared([], model.load_catalog()))[0]
        self.assertEqual(model.fit(model.Prepared([blank], model.load_catalog()))[0], prior,
                         "A round with nothing on screen carries no choice evidence")
        self.assertEqual(model.trajectory([blank])["rounds"], 1)
        self.assertEqual(model.validate([blank] * 6)["holdout_choice_count"], 0)
        summary = model.summarize([blank])
        self.assertEqual((summary["status"], summary["observation_count"], summary["round_count"]),
                         ("no_observations", 0, 1), "Unseen rounds are not reported as learning evidence")
        unknown = model.observations(events(None))[0]
        self.assertEqual((unknown["shown"], unknown["picks"]), (ids, ids[:2]))
        self.assertNotIn("unseen", unknown)

    def test_all_pick_round_still_ends_with_a_forced_stop_that_carries_no_evidence(self):
        catalog = model.load_catalog()
        full = model.Prepared([{"round": 1, "shown": ["NORN_0001"], "picks": ["NORN_0001"], "stopped": True}], catalog)
        pick_only = model.Prepared([{"round": 1, "shown": ["NORN_0001"], "picks": ["NORN_0001"], "stopped": False}], catalog)
        for left, right in zip(model.fit(full)[0], model.fit(pick_only)[0]):
            self.assertAlmostEqual(left, right, delta=1e-12)

    def test_prior_state_is_symmetric_and_deterministic(self):
        result = model.trajectory([])
        state = result["states"][0]
        self.assertEqual((result["rounds"], state["round"]), (0, 0))
        self.assertEqual(state["mean"], [0.0] * 4)
        self.assertEqual(state["sd"], [round(math.sqrt(1 / 1.5), 4)] * 4)
        self.assertEqual(state["stop_bias"], 0.0)
        self.assertEqual(state["top"], model.load_catalog().ids[:6], "Ties break by card ID")
        self.assertEqual(len(state["types"]), 4)
        for row in state["types"]:
            self.assertAlmostEqual(row["p"], 1 / 16, delta=0.01)
        self.assertEqual(model.trajectory([]), result)

    def test_trajectory_transport_has_every_round_and_a_full_tapestry(self):
        rows = model.observations(DEMO["journal"])
        result = model.trajectory(rows)
        self.assertEqual(result["schema_version"], "norn.trajectory.v1")
        self.assertEqual(result["rounds"], 20)
        cells = {tuple(card["cell"]) for card in result["cards"]}
        self.assertEqual(cells, {(column, row) for column in range(16) for row in range(16)})
        for card in result["cards"]:
            # S|N halves left/right, T|F halves top/bottom.
            self.assertEqual(card["cell"][0] >= 8, card["type"][1] == "N")
            self.assertEqual(card["cell"][1] >= 8, card["type"][2] == "F")
        previous = [0.0] * 4
        for index, state in enumerate(result["states"]):
            self.assertEqual(state["round"], index)
            self.assertEqual(len(state["scores"]), 256)
            self.assertEqual(len(state["top"]), 6)
            self.assertLessEqual(len(state["types"]), 4)
            self.assertLessEqual(sum(row["p"] for row in state["types"]), 1.0 + 1e-9)
            self.assertEqual(len(state["cov_sn_tf"]), 2)
            if index:
                row = rows[index - 1]
                self.assertEqual((state["shown"], state["picks"], state["duration_ms"]),
                                 (row["shown"], row["picks"], row["duration_ms"]))
                for delta, now, before in zip(state["delta"], state["mean"], previous):
                    self.assertAlmostEqual(delta, now - before, delta=2e-4)
            previous = state["mean"]
        self.assertEqual(result["states"][-1]["mean"], [round(value, 4) for value in DEMO["states"][-1]["theta"][:4]])

    def test_runtime_is_standard_library_only(self):
        tree = ast.parse((ROOT / "the-norn-machine" / "backend" / "choice_model.py").read_text(encoding="utf-8"))
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        self.assertLessEqual(imported, {"collections", "copy", "hashlib", "json", "math", "operator", "pathlib",
                                        "numeric", "fast_thinker"})

    def test_feature_catalog_is_tamper_evident_and_current(self):
        document = json.loads((ROOT / "the-norn-machine" / "backend" / "config" / "choice_features.json").read_text())
        self.assertEqual(len(document["cards"]), 256)
        self.assertEqual(len(document["components"]), 8)
        bad = deepcopy(document)
        bad["cards"]["NORN_0001"]["pixel"][0] += 0.5
        with self.assertRaisesRegex(ValueError, "hash"):
            model.Catalog(bad)
        if importlib.util.find_spec("numpy") is None:
            self.skipTest("NumPy regenerates the catalog; the runtime does not need it")
        spec = importlib.util.spec_from_file_location("prepare_choice_features", ROOT / "scripts" / "prepare_choice_features.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        self.assertEqual(script.build_catalog(), document)

    def test_validation_is_a_chronological_holdout_or_reports_insufficient(self):
        few = model.validate(model.observations(journal(4)))
        self.assertEqual((few["status"], few["log_loss"]), ("insufficient", None))
        measured = model.validate(model.observations(DEMO["journal"]))
        self.assertEqual(measured["status"], "measured")
        self.assertEqual((measured["train_observation_count"], measured["holdout_observation_count"]), (16, 4))
        self.assertGreater(measured["holdout_choice_count"], 0)
        self.assertTrue(math.isfinite(measured["log_loss"]))
        self.assertEqual(measured["beats_random"], measured["log_loss"] < measured["random_baseline"]["log_loss"])
        self.assertEqual(measured["beats_stop_only"], measured["log_loss"] < measured["stop_only_baseline"]["log_loss"])
        self.assertTrue(measured["beats_baselines"], "The demo's planted taste beats both baselines")
        self.assertIn("no personality accuracy", measured["claim"])

    def test_stop_rate_alone_beats_uniform_but_not_the_stop_only_baseline(self):
        # Picks are random with respect to every feature; only the stop rate is distinctive.
        import random
        for seed in (0, 8):
            rnd, pool, rows = random.Random(seed), list(model.load_catalog().ids), []
            rnd.shuffle(pool)
            for number in range(30):
                shown = pool[number * 6 % 250:number * 6 % 250 + 6]
                rows.append({"round": number + 1, "shown": shown, "stopped": True, "duration_ms": 1,
                             "picks": rnd.sample(shown, 0 if rnd.random() < 0.8 else 1)})
            with self.subTest(seed=seed):
                result = model.validate(rows)
                self.assertEqual(result["status"], "measured")
                self.assertTrue(result["beats_random"], "Uniform is fooled by the stop rate")
                self.assertFalse(result["beats_stop_only"])
                self.assertFalse(result["beats_baselines"])

    def test_contributions_reconstruct_every_listed_score_and_reject_tampering(self):
        rows = model.observations(DEMO["journal"])
        summary = model.summarize(rows, selected=["NORN_0006", "NORN_0104"])
        block = summary["contributions"]
        self.assertTrue(model.verify_contributions(block))
        self.assertEqual(block["feature_names"][:4], ["axis.E_I", "axis.S_N", "axis.T_F", "axis.J_P"])
        listed = {card["card_id"]: card for card in block["cards"]}
        self.assertEqual(set(listed), {row["card_id"] for row in summary["recommendations"]} | {"NORN_0006", "NORN_0104"})
        self.assertEqual(listed["NORN_0104"]["roles"], ["selected"])
        for row in summary["recommendations"] + summary["selected_evidence"]:
            card = listed[row["card_id"]]
            self.assertAlmostEqual(card["score"], row["score"], delta=1e-12)
            self.assertAlmostEqual(math.fsum(card["terms"]), card["score"], delta=1e-12)
            self.assertGreater(card["score_sd"], 0)
        for mutate in (lambda b: b["cards"][0]["terms"].__setitem__(0, 9.0),
                       lambda b: b["cards"][0]["values"].__setitem__(5, 0.0),
                       lambda b: b["weights"].__setitem__(4, 1.0),
                       lambda b: b["cards"][0].update(score=0.0),
                       lambda b: b["cards"][0].update(card_id="NOT_A_CARD")):
            forged = deepcopy(block)
            mutate(forged)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                model.verify_contributions(forged)
        # Weights stay out of the language context.
        from norn.context import build_analysis_prompt, build_session_context
        prompt = json.dumps(build_analysis_prompt(build_session_context(build_event_profile(DEMO["journal"]))),
                            ensure_ascii=False)
        for field in ("contributions", "weight_sd", "pixel_terms", '"weights"', "stop_only_baseline"):
            self.assertNotIn(field, prompt)

    def test_trajectory_states_carry_a_rounded_breakdown_for_guesses_and_picks(self):
        result = model.trajectory(model.observations(DEMO["journal"]))
        index = {card["id"]: position for position, card in enumerate(result["cards"])}
        self.assertEqual(result["feature_names"], list(model.FEATURE_NAMES))
        for state in result["states"]:
            block = state["contributions"]
            self.assertEqual([card["card_id"] for card in block["cards"]],
                             list(dict.fromkeys(state["top"] + state.get("picks", []))))
            for card in block["cards"]:
                self.assertEqual((len(card["axes"]), len(card["pixel_terms"])), (4, 8))
                self.assertAlmostEqual(sum(card["axes"]) + card["pixel"], card["score"], delta=0.004)
                self.assertAlmostEqual(card["score"], state["scores"][index[card["card_id"]]], delta=0.002)


class ProfileV4Tests(unittest.TestCase):
    def test_explicit_v4_profile_is_portable_and_replays(self):
        bundle = build_replay_bundle(build_event_profile(DEMO["journal"], algorithm_version="norn.scoring.v4"),
                                     DEMO["journal"])
        profile = bundle["profile"]
        self.assertEqual((profile["schema_version"], profile["algorithm_version"]), ("norn.profile.v4", "norn.scoring.v4"))
        self.assertEqual(profile["numerical_contract"], numeric.PORTABLE_CONTRACT)
        self.assertEqual(profile["feature_version"], model.load_catalog().feature_version)
        preference = profile["analysis"]["preference"]
        self.assertEqual(preference["model_version"], model.MODEL_VERSION)
        self.assertEqual((preference["feature_count"], preference["parameter_count"]), (12, 13))
        for actual, expected in zip(preference["posterior"]["mean"], DEMO["states"][-1]["theta"]):
            self.assertAlmostEqual(actual, expected, delta=1e-6)
        self.assertIn(len(preference["type_probabilities"]), (1, 2, 3, 4), "At most four types that received draws")
        self.assertEqual(profile["analysis"]["analysis_kind"], "learned_preference")
        self.assertEqual(profile["analysis"]["attribution"]["schema_version"], "norn.attribution.v3")
        for heuristic in ("mbti_type", "coords_avg", "confidence", "top_traits", "legacy_projection"):
            self.assertNotIn(heuristic, profile["analysis"], "v4 computes no four-axis heuristic")
        self.assertTrue(verify_profile(profile, DEMO["journal"]))
        self.assertEqual(replay_bundle(json.loads(json.dumps(bundle))), profile)

    def test_durations_are_recorded_but_never_scored(self):
        source = journal(8)
        slower = deepcopy(source)
        for event in slower["events"]:
            if event["type"] == "round_completed":
                event["duration_ms"] = 40_000 - event["duration_ms"] * 7
        fast, slow = (build_event_profile(item, algorithm_version="norn.scoring.v4") for item in (source, slower))
        self.assertNotEqual(fast["events_hash"], slow["events_hash"], "Durations stay in the journal")
        self.assertEqual(fast["analysis"], slow["analysis"], "No hesitation or round-decay weighting in v4")
        v3 = "norn.scoring.v3"
        self.assertNotEqual(build_event_profile(source, algorithm_version=v3)["analysis"]["attribution"]["rounds"],
                            build_event_profile(slower, algorithm_version=v3)["analysis"]["attribution"]["rounds"])

    def test_native_golden_hash(self):
        # Checked with native CPython only; a Pyodide check is still to do.
        profile = build_event_profile(default_journal(), algorithm_version="norn.scoring.v4")
        self.assertEqual(profile["profile_hash"], build_event_profile(default_journal(), algorithm_version="norn.scoring.v4")["profile_hash"])
        self.assertEqual(profile["profile_hash"], GOLDEN_V4_DEFAULT_JOURNAL)

    def test_rehashed_posterior_or_type_forgery_fails_replay(self):
        profile = build_event_profile(DEMO["journal"], algorithm_version="norn.scoring.v4")
        for mutate in (lambda p: p["analysis"]["preference"]["posterior"]["mean"].__setitem__(0, 9.0),
                       lambda p: p["analysis"]["preference"]["type_probabilities"][0].__setitem__("p", 1.0),
                       lambda p: p["analysis"]["preference"]["axes"]["sd"].__setitem__(1, 0.001)):
            forged = deepcopy(profile)
            mutate(forged)
            rehash(forged)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                verify_profile(forged, DEMO["journal"])

    def test_older_scoring_versions_still_replay(self):
        source = journal(7)
        v3 = build_event_profile(source, algorithm_version="norn.scoring.v3")
        self.assertEqual(v3["schema_version"], "norn.profile.v3")
        self.assertIn("preference_model", v3, "v3 keeps its 979-feature checkpoint")
        self.assertEqual(replay_document(build_replay_bundle(v3, source))["profile"], v3)
        legacy = json.loads((FIXTURES / "legacy-profile-v1.json").read_text())
        self.assertEqual(replay_document(deepcopy(legacy)), legacy)


GOLDEN_V4_DEFAULT_JOURNAL = "sha256:429ce269da37b7f484d92aa042c6450ef5e644775bac93b7510033324bb78c3f"


class TrajectoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = NornService()

    def test_journal_in_full_trajectory_out_without_sessions_or_model_calls(self):
        source = journal(5, open_last=True)
        original = deepcopy(source)
        result = self.service.trajectory({"journal": source})
        self.assertEqual(source, original)
        self.assertEqual((result["rounds"], result["complete"], result["open_round"]), (4, False, True))
        self.assertEqual((result["source"], result["verified"], result["profile"]), ("event_journal", False, None))
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["numerical_contract"], numeric.NATIVE_CONTRACT)
        self.assertEqual(len(self.service.sessions._sessions), 0)
        bare = self.service.trajectory(journal(4))
        self.assertEqual(bare["states"], result["states"], "A bare journal and {journal} agree")

    def test_exports_are_verified_first_whatever_their_scoring_version(self):
        source = journal(6)
        for algorithm in ("norn.scoring.v4", "norn.scoring.v3"):
            with self.subTest(algorithm=algorithm):
                bundle = build_replay_bundle(build_event_profile(source, algorithm_version=algorithm), source)
                result = self.service.trajectory(json.loads(json.dumps(bundle)))
                self.assertTrue(result["verified"])
                self.assertEqual(result["source"], "verified_export")
                self.assertEqual(result["profile"]["algorithm_version"], algorithm)
                self.assertEqual(result["model_version"], model.MODEL_VERSION)
                self.assertEqual(result["rounds"], 6)
        forged = build_replay_bundle(build_event_profile(source), source)
        forged["profile"]["analysis"]["preference"]["stop_bias"] = 3.0
        rehash(forged["profile"])
        with self.assertRaises(RequestError):
            self.service.trajectory(forged)
        snapshot = build_snapshot_profile(replay_events(source)["input"])
        with self.assertRaisesRegex(RequestError, "逐轮展示记录"):
            self.service.trajectory(build_replay_bundle(snapshot))
        for body in ([], {}, {"journal": {}, "extra": 1}, {"journal": {"schema_version": "norn.events.v1", "events": [{}]}}):
            with self.subTest(body=body), self.assertRaises(RequestError):
                self.service.trajectory(body)

    def test_session_trajectory_adds_frozen_versions_bytes_and_calls(self):
        result = self.service.analyze({"journal": journal(8)})
        trajectory = self.service.session_trajectory(result["session_id"])
        self.assertEqual((trajectory["source"], trajectory["rounds"], trajectory["complete"]), ("session", 8, True))
        self.assertEqual(trajectory["profile"]["profile_hash"], result["profile_hash"])
        self.assertGreater(trajectory["metrics"]["prompt_bytes"], 0)
        self.assertEqual([call["task"] for call in trajectory["calls"]], ["analysis"])
        self.assertNotIn("raw", trajectory["calls"][0]["usage"])
        with self.assertRaises(RequestError) as missing:
            self.service.session_trajectory("missing-session")
        self.assertEqual(missing.exception.status, 404)


class TrajectoryHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_post_and_session_routes(self):
        status, result = self.request("POST", "/api/trajectory", {"journal": journal(3)})
        self.assertEqual((status, result["rounds"]), (200, 3))
        status, reading = self.request("POST", "/api/analyze", {"journal": journal(3)})
        self.assertEqual(status, 200)
        status, frozen = self.request("GET", f"/api/sessions/{reading['session_id']}/trajectory")
        self.assertEqual((status, frozen["source"], frozen["states"]), (200, "session", result["states"]))
        self.assertEqual(self.request("GET", f"/api/sessions/{reading['session_id']}/trajectory?full=1")[0], 400)
        self.assertEqual(self.request("GET", "/api/sessions/missing/trajectory")[0], 404)
        self.assertEqual(self.request("POST", "/api/trajectory", {"journal": {"events": []}})[0], 400)


if __name__ == "__main__":
    unittest.main()
