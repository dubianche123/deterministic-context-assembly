"""Visibility-filtered preference labels and the optional post-reading rating."""

from copy import deepcopy
import http.client
import json
import threading
import unittest

from norn.actor_state import dump_actor_state, new_actor_service
from norn.core import engine_module
from norn.events import canonical_json, content_hash, validate_post_session
from norn.profile import replay_bundle
from norn.replay import replay_document
from norn.server import make_server
from norn.service import NornService, RequestError

from test_event_replay import ATTENTION_ROUNDS, attention_journal, journal_fixture, v1_projection


def observations(journal, include_open=False):
    model = engine_module("preference_model")
    return model.observations_from_journals([journal], include_open=include_open)["observations"]


def two_choice_journal():
    return attention_journal(
        {"cards": ["NORN_0001", "NORN_0002", "NORN_0003"], "ready": 120,
         "choices": [("card_selected", "NORN_0001", 800), ("card_selected", "NORN_0003", 1400)],
         "duration": 3000, "seen": ["NORN_0001", "NORN_0002", "NORN_0003"]})


class VisibilityFilteredNegativeTests(unittest.TestCase):
    def test_unseen_cards_are_neither_negatives_nor_positives(self):
        rows = observations(attention_journal(*ATTENTION_ROUNDS))
        first = rows[0]
        self.assertEqual(first["displayed"], ["NORN_0001", "NORN_0002", "NORN_0003", "NORN_0004"],
                         "The display fact itself is unchanged")
        self.assertEqual(first["selected"], ["NORN_0001"])
        self.assertEqual(first["unselected"], ["NORN_0002"],
                         "Cards 3 and 4 never reached the screen and are not rejected alternatives")
        # Round two has no observer data (null): every displayed card keeps its role.
        self.assertEqual((rows[1]["selected"], rows[1]["unselected"]),
                         (["NORN_0006", "NORN_0001"], ["NORN_0005"]))

    def test_v1_and_unknown_visibility_keep_historical_labels(self):
        legacy = observations(v1_projection(attention_journal(*ATTENTION_ROUNDS)))
        self.assertEqual(legacy[0]["unselected"], ["NORN_0002", "NORN_0003", "NORN_0004"])
        fixture = observations(journal_fixture())
        self.assertTrue(fixture and all(set(row["selected"]) | set(row["unselected"]) == set(row["displayed"])
                                        for row in fixture))

    def test_carried_selection_off_screen_is_not_a_fresh_comparison(self):
        journal = attention_journal(
            {"cards": ["NORN_0001", "NORN_0002"], "ready": 0, "choices": [("card_selected", "NORN_0001", 10)],
             "duration": 100, "seen": ["NORN_0001", "NORN_0002"]},
            {"cards": ["NORN_0005", "NORN_0006", "NORN_0001"], "ready": 0,
             "choices": [("card_selected", "NORN_0005", 30)], "duration": 100, "seen": ["NORN_0005", "NORN_0006"]},
            {"cards": ["NORN_0007", "NORN_0008"], "ready": 0, "choices": [("card_selected", "NORN_0007", 5)],
             "duration": 100, "seen": ["NORN_0007"]})
        rows = observations(journal)
        self.assertEqual([(row["selected"], row["unselected"]) for row in rows],
                         [(["NORN_0001"], ["NORN_0002"]), (["NORN_0005"], ["NORN_0006"])],
                         "A round whose only seen card was chosen supplies no relative label")

    def test_training_uses_fewer_but_valid_pairs_and_rejects_bad_attention(self):
        model = engine_module("preference_model")
        journal = attention_journal(*ATTENTION_ROUNDS)
        filtered = model.train([journal])["training"]["pair_count"]
        historical = model.train([v1_projection(journal)])["training"]["pair_count"]
        self.assertLess(filtered, historical)
        bad = deepcopy(journal)
        bad["events"][-2]["seen_card_ids"] = ["NORN_0001"]   # not displayed in round three
        with self.assertRaises(ValueError):
            model.train([bad])
        bad = deepcopy(journal)
        del bad["events"][1]                                # choice without readiness
        for sequence, event in enumerate(bad["events"], 1):
            event["sequence"] = sequence
        with self.assertRaises(ValueError):
            model.train([bad])

    def test_open_round_preview_is_provisional_without_visibility(self):
        journal = attention_journal(*ATTENTION_ROUNDS[:1], complete=False)
        journal["events"].pop()  # reopen the round
        rows = observations(journal, include_open=True)
        self.assertTrue(rows[-1]["provisional"])
        self.assertEqual(rows[-1]["unselected"], ["NORN_0002", "NORN_0003", "NORN_0004"])


class PostSessionRatingTests(unittest.TestCase):
    def setUp(self):
        self.service = NornService()
        self.result = self.service.analyze({"journal": two_choice_journal(), "request_id": "reading-1"})
        self.session = self.result["session_id"]

    def feedback(self, rating=4, closest="NORN_0003"):
        return self.service.record_feedback({"session_id": self.session, "reading_rating": rating,
                                             "closest_card_id": closest})

    def test_skipping_leaves_the_export_exactly_as_before(self):
        exported = self.service.export_session(self.session)
        self.assertEqual(set(exported), {"schema_version", "journal", "profile"})
        self.assertEqual(replay_bundle(exported), exported["profile"])

    def test_rating_is_attached_to_session_export_and_survives_replay(self):
        before = self.service.export_session(self.session)
        saved = self.feedback()
        record = saved["post_session"]
        self.assertEqual(record["reading_rating"], 4)
        self.assertEqual(record["closest_card_id"], "NORN_0003")
        self.assertEqual(record["profile_hash"], self.result["profile_hash"])
        self.assertEqual(record["reading_hash"], content_hash(self.result["reading"]))
        self.assertEqual((record["provider"], record["simulated"]), ("mock", True),
                         "Ratings of simulated readings stay distinguishable from model readings")
        exported = self.service.export_session(self.session)
        self.assertEqual(exported["post_session"], record)
        self.assertEqual({key: exported[key] for key in before}, before,
                         "A rating never changes the journal, profile or their hashes")
        transported = json.loads(canonical_json(exported))
        self.assertEqual(replay_bundle(transported), before["profile"])
        self.assertEqual(replay_document(transported)["post_session"], record)
        replaced = self.feedback(rating=None, closest="NORN_0001")["post_session"]
        self.assertEqual(self.service.export_session(self.session)["post_session"], replaced)

    def test_invalid_ratings_and_foreign_records_are_rejected(self):
        for body in ({"reading_rating": 6}, {"reading_rating": 0}, {"reading_rating": True},
                     {"reading_rating": 4.0}, {"reading_rating": None, "closest_card_id": None},
                     {"closest_card_id": "NORN_0002"}, {"closest_card_id": "UNKNOWN"}):
            request = {"session_id": self.session, "reading_rating": None, "closest_card_id": None, **body}
            with self.subTest(body=body), self.assertRaises(RequestError):
                self.service.record_feedback(request)
        with self.assertRaises(RequestError):
            self.service.record_feedback({"session_id": self.session, "reading_rating": 3})
        with self.assertRaises(RequestError):
            self.service.record_feedback({"session_id": self.session, "reading_rating": 3,
                                          "closest_card_id": None, "profile_hash": "sha256:" + "0" * 64})
        with self.assertRaises(RequestError) as missing:
            self.service.record_feedback({"session_id": "expired", "reading_rating": 3, "closest_card_id": None})
        self.assertEqual(missing.exception.status, 404)
        self.feedback()
        exported = self.service.export_session(self.session)
        selected = [row["card_id"] for row in exported["profile"]["analysis"]["attribution"]["selections"]]
        for change in (lambda r: r.update(profile_hash="sha256:" + "0" * 64),
                       lambda r: r.update(closest_card_id="NORN_0002"),
                       lambda r: r.update(schema_version="norn.post_session.v9"),
                       lambda r: r.update(extra=True), lambda r: r.update(simulated="yes")):
            bad = deepcopy(exported)
            change(bad["post_session"])
            with self.subTest(change=change), self.assertRaises(ValueError):
                replay_bundle(bad)
        validate_post_session(exported["post_session"], selected_card_ids=selected,
                              profile_hash=exported["profile"]["profile_hash"])

    def test_cloud_actor_persists_the_rating_and_old_snapshots_remain_valid(self):
        clock = lambda: 1_700_000_000.0
        service = new_actor_service(clock=clock)
        session = service.analyze({"journal": two_choice_journal(), "request_id": "reading-1"})["session_id"]
        without = dump_actor_state(service)
        self.assertNotIn("post_session", without["sessions"][0], "Snapshots stay unchanged until a rating exists")
        service.record_feedback({"session_id": session, "reading_rating": 2, "closest_card_id": None})
        state = json.loads(canonical_json(dump_actor_state(service)))
        restored = new_actor_service(state, clock=clock)
        self.assertEqual(restored.export_session(session)["post_session"]["reading_rating"], 2)
        self.assertEqual(new_actor_service(without, clock=clock).export_session(session).get("post_session"), None)
        state["sessions"][0]["post_session"]["closest_card_id"] = "NORN_0002"
        with self.assertRaises(ValueError):
            new_actor_service(state, clock=clock)


class FeedbackHTTPTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_feedback_endpoint_and_export(self):
        status, reading = self.request("POST", "/api/analyze", {"journal": two_choice_journal(),
                                                                "request_id": "reading-http"})
        self.assertEqual(status, 200)
        status, saved = self.request("POST", "/api/feedback", {"session_id": reading["session_id"],
                                                               "reading_rating": 5, "closest_card_id": None})
        self.assertEqual(status, 200, saved)
        self.assertTrue(saved["saved"])
        status, exported = self.request("GET", f"/api/sessions/{reading['session_id']}/export")
        self.assertEqual(status, 200)
        self.assertEqual(exported["post_session"]["reading_rating"], 5)
        self.assertEqual(exported["journal"]["schema_version"], "norn.events.v2")
        status, verified = self.request("POST", "/api/replay", exported)
        self.assertEqual(status, 200, verified)
        self.assertTrue(verified["verified"])
        status, rejected = self.request("POST", "/api/feedback", {"session_id": reading["session_id"],
                                                                  "reading_rating": 9, "closest_card_id": None})
        self.assertEqual(status, 400, rejected)


if __name__ == "__main__":
    unittest.main()
