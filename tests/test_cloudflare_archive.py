"""Cloudflare collection path: actor -> archive Durable Object -> operator export."""

import json
from types import SimpleNamespace
import unittest

from norn.collection import SCHEMA_SQL
from tests.test_cloudflare_entry import ENTRY, CloudflareEntryTests, Storage, data
from tests.test_collection import CONSENT, OTHER_VISITOR, VISITOR


class ArchiveNamespace:
    """Every RPC recreates the archive class; only its SQLite survives."""

    def __init__(self):
        self.storage = Storage()
        self.names = []
        self.fail = False        # every call fails before reaching storage
        self.failures = 0        # the next n calls fail before reaching storage
        self.lost_replies = 0    # the next n calls commit, then their reply is lost
        self.applied = []

    def getByName(self, name):
        self.names.append(name)
        storage, namespace = self.storage, self

        async def apply(operation_json):
            namespace.applied.append(json.loads(operation_json)["op"])
            if namespace.fail or namespace.failures:
                namespace.failures = max(0, namespace.failures - 1)
                raise RuntimeError("archive unavailable")
            reply = await ENTRY.NornArchive(SimpleNamespace(storage=storage), None).apply(operation_json)
            if namespace.lost_replies:
                namespace.lost_replies -= 1
                raise RuntimeError("reply lost after commit")
            return reply

        async def page(after, limit):
            return await ENTRY.NornArchive(SimpleNamespace(storage=storage), None).page(after, limit)
        return SimpleNamespace(apply=apply, page=page)

    def count(self):
        for statement in SCHEMA_SQL:  # the DO constructor does this in production
            self.storage.connection.execute(statement)
        return self.storage.connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]


class CloudflareArchiveTests(CloudflareEntryTests):
    def setUp(self):
        super().setUp()
        self.archive = ArchiveNamespace()
        self.env.NORN_ARCHIVE = self.archive
        self.env.NORN_ARCHIVE_TOKEN = "operator-secret-token"
        self.namespace.env = self.env
        self.addCleanup(self.archive.storage.connection.close)

    async def consented(self, request_id="reading-1"):
        response = await self.fetch("/api/analyze", "POST", {"journal": self.journal(), "request_id": request_id,
                                                              **CONSENT})
        self.assertEqual(response.status, 200, response.body)
        return data(response)

    async def test_consented_reading_is_archived_rated_withdrawn(self):
        plain = await self.reading()
        self.assertIsNone(plain["record"])
        self.assertEqual(self.archive.count(), 0)
        result = await self.consented("reading-2")
        receipt = result["record"]
        self.assertEqual(self.archive.names[-1], ENTRY.ARCHIVE_NAME)
        self.assertEqual(self.archive.count(), 1)
        rated = await self.fetch("/api/feedback", "POST", {"session_id": result["session_id"],
                                                          "reading_rating": 5, "closest_card_id": None})
        self.assertEqual(rated.status, 200, rated.body)
        export = await self.fetch("/api/archive/export", headers={"Authorization": "Bearer operator-secret-token"})
        self.assertEqual(export.status, 200, export.body)
        [line] = export.body.splitlines()
        record = json.loads(line)
        self.assertEqual(record["deployment"], "cloudflare")
        self.assertEqual(record["journal"], self.journal())
        self.assertEqual(record["visit"], 1)
        self.assertEqual(record["annotations"]["post_session"]["reading_rating"], 5)
        wrong = await self.fetch("/api/record/withdraw", "POST", {**receipt, "visitor": OTHER_VISITOR})
        self.assertEqual(wrong.status, 403)
        self.assertEqual(self.archive.count(), 1)
        done = await self.fetch("/api/record/withdraw", "POST", {**receipt, "visitor": VISITOR})
        self.assertEqual((done.status, data(done)), (200, {"withdrawn": True}))
        self.assertEqual(self.archive.count(), 0)

    async def test_second_visit_is_linked_and_forget_clears_all(self):
        await self.consented("visit-1")
        await self.consented("visit-2")
        export = await self.fetch("/api/archive/export", headers={"Authorization": "Bearer operator-secret-token"})
        rows = [json.loads(line) for line in export.body.splitlines()]
        self.assertEqual(sorted(row["visit"] for row in rows), [1, 2])
        self.assertEqual(len({row["visitor_id"] for row in rows}), 1)
        self.assertNotIn(VISITOR, export.body)
        done = await self.fetch("/api/record/withdraw", "POST", {"visitor": VISITOR, "all": True})
        self.assertEqual((done.status, data(done)), (200, {"withdrawn": True}))
        self.assertEqual(self.archive.count(), 0)

    async def test_export_requires_the_operator_token(self):
        await self.consented()
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "operator-secret-token"}):
            response = await self.fetch("/api/archive/export", headers=headers)
            self.assertEqual(response.status, 401)
        bad = await self.fetch("/api/archive/export?limit=9999",
                               headers={"Authorization": "Bearer operator-secret-token"})
        self.assertEqual(bad.status, 400)
        del self.env.NORN_ARCHIVE_TOKEN
        missing = await self.fetch("/api/archive/export", headers={"Authorization": "Bearer "})
        self.assertEqual(missing.status, 404)

    async def test_reply_lost_after_commit_keeps_the_receipt(self):
        # The record is stored but the RPC raises: one idempotent retry confirms it.
        self.archive.lost_replies = 1
        result = await self.consented()
        self.assertEqual(set(result["record"]), {"record_id"})
        self.assertEqual((self.archive.count(), self.archive.applied), (1, ["put", "put"]))
        retried = await self.consented()  # same request_id replays the stored reply
        self.assertEqual(retried["record"], result["record"])
        done = await self.fetch("/api/record/withdraw", "POST", {**result["record"], "visitor": VISITOR})
        self.assertEqual((done.status, data(done)), (200, {"withdrawn": True}))
        self.assertEqual(self.archive.count(), 0)

    async def test_unknown_write_is_retracted_before_saying_nothing_was_kept(self):
        # Both puts commit with lost replies; the withdrawal is confirmed, so "not kept" is true.
        self.archive.lost_replies = 2
        result = await self.consented()
        self.assertTrue(result["reading"])
        self.assertIsNone(result["record"])
        self.assertEqual((self.archive.count(), self.archive.applied), (0, ["put", "put", "withdraw"]))
        self.assertIsNone((await self.consented())["record"])
        self.assertIn('"event":"record.put_retracted"', self.output.getvalue())

    async def test_unreachable_archive_keeps_an_unconfirmed_receipt(self):
        self.archive.fail = True
        result = await self.consented()
        self.assertTrue(result["reading"])
        self.assertEqual(result["record"], {"record_id": result["record"]["record_id"], "unconfirmed": True})
        self.archive.fail = False
        retried = await self.consented()  # same request_id replays the stored reply
        self.assertEqual(retried["session_id"], result["session_id"])
        self.assertEqual(retried["record"], result["record"])
        self.assertEqual(self.archive.count(), 0)
        # The visitor can still withdraw; a record that never landed counts as withdrawn.
        done = await self.fetch("/api/record/withdraw", "POST", {"record_id": result["record"]["record_id"],
                                                                  "visitor": VISITOR})
        self.assertEqual((done.status, data(done)), (200, {"withdrawn": True}))
        logs = self.output.getvalue()
        self.assertIn('"event":"record.put"', logs)
        self.assertIn('"ok":false', logs)
        self.assertNotIn(self.journal()["events"][0]["card_ids"][0], logs)

    async def test_failed_write_before_storage_ends_without_a_receipt(self):
        self.archive.failures = 2  # put and its retry never reach storage; the withdrawal does
        result = await self.consented()
        self.assertIsNone(result["record"])
        self.assertEqual((self.archive.count(), self.archive.applied), (0, ["put", "put", "withdraw"]))

    async def test_research_visit_keeps_a_partial_record_until_its_reading(self):
        from unittest.mock import patch
        from tests.test_partial_records import KEY, prefix
        from tests.test_research_deck import research, session
        real = self.clock_patch.temp_original
        self.env.NORN_DECK = "pamela-v1"
        journal = session(research.MIXED, rounds=4)
        token = {"Authorization": "Bearer operator-secret-token"}
        with patch("norn.actor_state.new_actor_service",
                   side_effect=lambda state=None, **kwargs: real(state, clock=lambda: self.now, **kwargs)):
            for rounds in (0, 2):
                stored = await self.fetch("/api/record/partial", "POST",
                                          {**CONSENT, "session_key": KEY, "journal": prefix(journal, rounds)})
                self.assertEqual((stored.status, data(stored)), (200, {"stored": True}), stored.body)
            [row] = [json.loads(line) for line in (await self.fetch("/api/archive/export", headers=token)).body.splitlines()]
            self.assertEqual((row["schema_version"], row["record_id"], row["visit"]), ("norn.partial.v1", KEY, 1))
            extra = await self.fetch("/api/record/partial", "POST",
                                     {**CONSENT, "session_key": KEY, "journal": prefix(journal, 1), "note": 1})
            self.assertEqual(extra.status, 400)
            reading = await self.fetch("/api/analyze", "POST", {"journal": journal, "request_id": "partial-full",
                                                                "session_key": KEY, **CONSENT})
            self.assertEqual(reading.status, 200, reading.body)
            [row] = [json.loads(line) for line in (await self.fetch("/api/archive/export", headers=token)).body.splitlines()]
            self.assertEqual((row["schema_version"], row["visit"], row["experiments"]["session_key"]),
                             ("norn.record.v2", 1, KEY))
            done = await self.fetch("/api/record/withdraw", "POST", {"visitor": VISITOR, "all": True})
            self.assertEqual(done.status, 200)
            self.assertEqual(self.archive.count(), 0)


    async def test_partial_network_ordering_and_in_place_full_upgrade(self):
        from unittest.mock import patch
        from tests.test_partial_records import KEY, prefix
        from tests.test_research_deck import research, session
        self.env.NORN_DECK = "pamela-v1"
        real = self.clock_patch.temp_original
        journal = session(research.MIXED, rounds=5)
        token = {"Authorization": "Bearer operator-secret-token"}

        async def partial(rounds, key=KEY):
            return await self.fetch("/api/record/partial", "POST",
                                    {**CONSENT, "session_key": key, "journal": prefix(journal, rounds)})

        with patch("norn.actor_state.new_actor_service",
                   side_effect=lambda state=None, **kwargs: real(state, clock=lambda: self.now, **kwargs)):
            self.assertEqual(data(await partial(4)), {"stored": True})
            self.assertEqual(data(await partial(1)), {"stored": False})
            self.assertEqual(data(await partial(4)), {"stored": True})
            self.assertEqual(data(await partial(0, "f" * 32)), {"stored": True})
            reading = await self.fetch("/api/analyze", "POST",
                                       {"journal": journal, "request_id": "upgrade-original-session",
                                        "session_key": KEY, **CONSENT})
            self.assertEqual(reading.status, 200, reading.body)
            self.assertEqual(data(reading)["record"], {"record_id": KEY})
            self.assertEqual(data(await partial(4)), {"stored": False})
            rows = {row["record_id"]: row for row in [json.loads(line) for line in
                    (await self.fetch("/api/archive/export", headers=token)).body.splitlines()]}
            self.assertEqual((rows[KEY]["schema_version"], rows[KEY]["visit"]), ("norn.record.v2", 1))
            self.assertEqual((rows["f" * 32]["schema_version"], rows["f" * 32]["visit"]), ("norn.partial.v1", 2))
            done = await self.fetch("/api/record/withdraw", "POST", {"visitor": VISITOR, "all": True})
            self.assertEqual(done.status, 200)
            self.assertEqual(data(await partial(4)), {"stored": False})
            self.assertEqual(data(await partial(4, "e" * 32)), {"stored": False})
            self.assertEqual(self.archive.count(), 0)

    async def test_partial_acknowledgement_comes_from_archive_not_outbox(self):
        from unittest.mock import patch
        from tests.test_partial_records import KEY, prefix
        from tests.test_research_deck import research, session
        self.env.NORN_DECK = "pamela-v1"
        real = self.clock_patch.temp_original
        journal = session(research.RANDOM, rounds=4)

        async def partial(rounds):
            return await self.fetch("/api/record/partial", "POST",
                                    {**CONSENT, "session_key": KEY, "journal": prefix(journal, rounds)})

        with patch("norn.actor_state.new_actor_service",
                   side_effect=lambda state=None, **kwargs: real(state, clock=lambda: self.now, **kwargs)):
            self.archive.failures = 2
            failed = await partial(0)
            self.assertEqual((failed.status, data(failed)), (200, {"stored": False, "unconfirmed": True}))
            self.assertEqual(self.archive.count(), 0)
            self.archive.lost_replies = 1
            self.assertEqual(data(await partial(1)), {"stored": True})
            self.archive.lost_replies = 2
            uncertain = await partial(2)
            self.assertEqual(data(uncertain), {"stored": False, "unconfirmed": True})
            # No automatic retraction of the shared session ID: a different
            # actor's later snapshot may already be here when a reply is lost.
            self.assertEqual(self.archive.count(), 1)
            self.assertNotIn("withdraw", self.archive.applied)
            self.assertEqual(data(await partial(1)), {"stored": False})
            self.assertEqual(data(await partial(2)), {"stored": True})

    async def test_active_round_attention_survives_archive_rpc_without_closing_the_journal(self):
        from copy import deepcopy
        from unittest.mock import patch
        from tests.test_partial_records import KEY, active_round, attention
        from tests.test_research_deck import research, session
        self.env.NORN_DECK = "pamela-v1"
        real = self.clock_patch.temp_original
        journal = session(research.RANDOM, rounds=2, keep=lambda _: False)
        current = active_round(journal)
        # Preserve a final current-round selection and its withdrawal as real facts.
        layout = current["events"][1]["layout"]
        card = current["events"][0]["card_ids"][0]
        for kind, moment in (("card_selected", 100), ("card_deselected", 200)):
            current["events"].append({"event_id": f"evt-{len(current['events']) + 1:06d}",
                                      "sequence": len(current["events"]) + 1, "type": kind,
                                      "round": 1, "card_id": card, "t_ms": moment,
                                      "input": "mouse", "layout": layout})
        body = {**CONSENT, "session_key": KEY, "journal": current,
                "open_round": attention(current, 1000, seen_card_ids=[card], hover=[[0, 20, 800]])}
        token = {"Authorization": "Bearer operator-secret-token"}
        with patch("norn.actor_state.new_actor_service",
                   side_effect=lambda state=None, **kwargs: real(state, clock=lambda: self.now, **kwargs)):
            self.assertEqual(data(await self.fetch("/api/record/partial", "POST", body)), {"stored": True})
            newer = deepcopy(body)
            newer["open_round"].update(elapsed_ms=2000, hover=[[0, 20, 1800]])
            self.archive.lost_replies = 2
            uncertain = await self.fetch("/api/record/partial", "POST", newer)
            self.assertEqual(data(uncertain), {"stored": False, "unconfirmed": True})
            # A lost acknowledgement must not retract the newer current-round sample.
            self.assertEqual(data(await self.fetch("/api/record/partial", "POST", body)), {"stored": False})
            export = await self.fetch("/api/archive/export", headers=token)
            [row] = [json.loads(line) for line in export.body.splitlines()]
            self.assertEqual(row["open_round"], newer["open_round"])
            self.assertEqual(row["journal"], current)
            self.assertFalse(any(event["type"] == "round_completed" for event in row["journal"]["events"]))
            malformed = deepcopy(newer)
            malformed["open_round"]["round"] = 2
            self.assertEqual((await self.fetch("/api/record/partial", "POST", malformed)).status, 400)
            self.assertEqual((await self.fetch("/api/record/withdraw", "POST",
                                               {"visitor": VISITOR, "all": True})).status, 200)
            self.assertEqual(data(await self.fetch("/api/record/partial", "POST", newer)), {"stored": False})
            self.assertEqual(self.archive.count(), 0)

    async def test_single_withdrawal_blocks_a_late_original_snapshot(self):
        from unittest.mock import patch
        from tests.test_partial_records import KEY, prefix
        from tests.test_research_deck import research, session
        self.env.NORN_DECK = "pamela-v1"
        real = self.clock_patch.temp_original
        journal = session(research.RANDOM, rounds=3)
        with patch("norn.actor_state.new_actor_service",
                   side_effect=lambda state=None, **kwargs: real(state, clock=lambda: self.now, **kwargs)):
            body = {**CONSENT, "session_key": KEY, "journal": prefix(journal, 1)}
            self.assertEqual(data(await self.fetch("/api/record/partial", "POST", body)), {"stored": True})
            self.assertEqual((await self.fetch("/api/record/withdraw", "POST",
                                               {"visitor": VISITOR, "record_id": KEY})).status, 200)
            body["journal"] = prefix(journal, 2)
            self.assertEqual(data(await self.fetch("/api/record/partial", "POST", body)), {"stored": False})
            self.assertEqual(self.archive.count(), 0)

    async def test_experience_append_survives_rpc_reordering_full_upgrade_and_withdrawal(self):
        from unittest.mock import patch
        from tests.test_experience import experience
        from tests.test_partial_records import KEY, prefix
        from tests.test_research_deck import research, session
        self.env.NORN_DECK = "pamela-v1"
        real = self.clock_patch.temp_original
        journal = session(research.RANDOM, rounds=3, keep=lambda _: False)
        token = {"Authorization": "Bearer operator-secret-token"}
        with patch("norn.actor_state.new_actor_service",
                   side_effect=lambda state=None, **kwargs: real(state, clock=lambda: self.now, **kwargs)), \
                patch("norn.service.NornService._render", side_effect=AssertionError("Must not call a model")):
            body = {**CONSENT, "session_key": KEY, "journal": prefix(journal, 2), "experience": experience()}
            self.assertEqual(data(await self.fetch("/api/record/partial", "POST", body)), {"stored": True})
            first = {**body, "experience": experience("on", 1)}
            self.assertEqual(data(await self.fetch("/api/record/partial", "POST", first)), {"stored": True})
            latest = {**body, "experience": experience("on", 1, 2)}
            self.archive.lost_replies = 2  # both writes commit, only their acknowledgements are lost
            self.assertEqual(data(await self.fetch("/api/record/partial", "POST", latest)),
                             {"stored": False, "unconfirmed": True})
            for stale in (first, {key: value for key, value in body.items() if key != "experience"},
                          {**body, "experience": experience("off")}):
                self.assertEqual(data(await self.fetch("/api/record/partial", "POST", stale)), {"stored": False})
            export = await self.fetch("/api/archive/export", headers=token)
            [row] = [json.loads(line) for line in export.body.splitlines()]
            self.assertEqual(row["experience"], latest["experience"])
            self.assertEqual(row["visit"], 1)
            self.assertEqual((row["deployment"], row["source_deployment"]),
                             ("cloudflare.round-feedback.v1", "cloudflare"))
            reading = await self.fetch("/api/analyze", "POST", {**CONSENT, "session_key": KEY, "journal": journal,
                "experience": experience("on", 1, 2, 3), "request_id": "experience-full"})
            self.assertEqual(reading.status, 200, reading.body)
            self.assertEqual(data(reading)["provider_calls"], 0)
            self.assertEqual(data(reading)["record"], {"record_id": KEY})
            export = await self.fetch("/api/archive/export", headers=token)
            [row] = [json.loads(line) for line in export.body.splitlines()]
            self.assertEqual((row["schema_version"], row["visit"]), ("norn.record.v2", 1))
            self.assertEqual(row["experience"], experience("on", 1, 2, 3))
            self.assertEqual((row["deployment"], row["source_deployment"]),
                             ("cloudflare.round-feedback.v1", "cloudflare"))
            self.assertEqual(data(await self.fetch("/api/record/partial", "POST", latest)), {"stored": False})
            self.assertEqual((await self.fetch("/api/record/withdraw", "POST",
                                               {"visitor": VISITOR, "all": True})).status, 200)
            self.assertEqual(data(await self.fetch("/api/record/partial", "POST", latest)), {"stored": False})
            self.assertEqual(self.archive.count(), 0)

    async def test_invalid_experience_is_rejected_by_both_cf_routes_without_generation(self):
        from unittest.mock import patch
        from tests.test_experience import experience
        from tests.test_partial_records import KEY, prefix
        from tests.test_research_deck import research, session
        self.env.NORN_DECK = "pamela-v1"
        real = self.clock_patch.temp_original
        journal = session(research.RANDOM, rounds=3, keep=lambda _: False)
        with patch("norn.actor_state.new_actor_service",
                   side_effect=lambda state=None, **kwargs: real(state, clock=lambda: self.now, **kwargs)), \
                patch("norn.service.NornService._render", side_effect=AssertionError("Must not call a model")):
            for value in (None, experience("off", 1), experience("on", 4)):
                for route, record in (("/api/record/partial", prefix(journal, 2)), ("/api/analyze", journal)):
                    response = await self.fetch(route, "POST", {**CONSENT, "session_key": KEY,
                        "journal": record, "experience": value})
                    self.assertEqual(response.status, 400, response.body)
            self.assertEqual(self.archive.count(), 0)


del CloudflareEntryTests  # do not run the inherited base suite twice


if __name__ == "__main__":
    unittest.main()
