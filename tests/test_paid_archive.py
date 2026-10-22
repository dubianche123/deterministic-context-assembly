"""Paid receipts and the record archive together: a retried ID replays the true outcome.

The real HostedGenerator, SQLite paid receipts and the Worker's own
_flush_records run together; only the model call and the archive RPC are fakes.
"""

import json
from types import SimpleNamespace
import unittest

from norn.collection import OutboxCollector
from tests.test_cloud_host import AsyncQuota, AsyncRenderer, Browser, Storage
from tests.test_cloudflare_archive import ArchiveNamespace
from tests.test_cloudflare_entry import ENTRY
from tests.test_collection import CONSENT, journal


class PaidArchiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.storages = [Storage(), Storage()]
        self.quota = AsyncQuota(self.storages[0], lambda: 1000)
        self.renderer = AsyncRenderer()
        self.browser = Browser(self.storages[1], self.quota, self.renderer, clock=lambda: 1000)
        self.archive = ArchiveNamespace()
        self.worker = SimpleNamespace(env=SimpleNamespace(NORN_ARCHIVE=self.archive))
        for storage in self.storages:
            self.addCleanup(storage.connection.close)
        self.addCleanup(self.archive.storage.connection.close)

    def host(self):
        """A fresh browser actor, restored from its committed snapshot, wired like the Worker."""
        host = self.browser.host()
        host.service.collection, host.service.deployment = OutboxCollector(), "cloudflare"
        host.before_settle = lambda result: ENTRY.NornSession._flush_records(self.worker, host.service, result)
        return host

    async def analyze(self):
        return await self.host().analyze({"journal": journal(), **CONSENT, "request_id": "reading-1"})

    async def test_kept_record_is_replayed_once_and_paid_once(self):
        first = await self.analyze()
        self.assertTrue(first["body"]["record"]["record_id"])
        again = await self.analyze()
        self.assertEqual(again["body"]["record"], first["body"]["record"])
        self.assertEqual((len(self.renderer.calls), self.archive.count()), (1, 1))

    async def test_dialogue_engagement_survives_restore_and_idempotent_retry_without_archiving_text(self):
        first = await self.analyze()
        session_id = first["body"]["session_id"]
        record_id = first["body"]["record"]["record_id"]

        def export_bytes():
            # Match the Worker's UTF-8 export representation, using a freshly
            # restored actor each time rather than the prior Python objects.
            bundle = self.host().service.export_session(session_id)
            return json.dumps(bundle, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")

        before = export_bytes()
        host = self.host()
        self.assertEqual(host.service.sessions.get(session_id).record_id, record_id)
        request = {"session_id": session_id, "user_message": "仅用于合成回归的私密追问", "turn_id": "followup-1"}
        response = await host.dialogue(request)
        self.assertEqual((response["status"], response["body"]["turn_count"]), (200, 1))
        self.assertEqual(await self.host().dialogue(request), response)
        self.assertEqual((len(self.renderer.calls), self.archive.applied), (2, ["put", "annotate"]))

        restored = self.host().service.sessions.get(session_id)
        self.assertEqual((restored.record_id, restored.turns), (record_id, 1))
        self.assertEqual(restored.history[-1]["content"], response["body"]["reply"])
        self.assertEqual(export_bytes(), before)

        exported = await self.archive.getByName(ENTRY.ARCHIVE_NAME).page("", 500)
        [record] = [json.loads(line) for line in exported.splitlines()]
        self.assertEqual(record["annotations"]["engagement"]["dialogue_turns"], 1)
        # Decode Unicode in the privacy check: ASCII escaping must not hide a
        # mistakenly archived Chinese question or reply from the assertion.
        record_text = json.dumps(record, ensure_ascii=False)
        self.assertNotIn(request["user_message"], record_text)
        self.assertNotIn(response["body"]["reply"], record_text)

    async def test_lost_record_is_never_claimed_on_retry(self):
        self.archive.failures = 2  # the put and its retry fail; the retraction is confirmed
        first = await self.analyze()
        self.assertEqual(first["status"], 200)
        self.assertTrue(first["body"]["reading"])
        self.assertIsNone(first["body"]["record"])
        again = await self.analyze()  # the paid receipt replays; no second model call
        self.assertIsNone(again["body"]["record"])
        self.assertEqual((len(self.renderer.calls), self.archive.count()), (1, 0))
        [session] = self.browser.snapshot()["sessions"]
        self.assertIsNone(session.get("record_id"))

    async def test_reply_lost_after_commit_is_kept_and_replayed(self):
        self.archive.lost_replies = 1
        first = await self.analyze()
        self.assertEqual(set(first["body"]["record"]), {"record_id"})
        again = await self.analyze()
        self.assertEqual(again["body"]["record"], first["body"]["record"])
        self.assertEqual((len(self.renderer.calls), self.archive.count()), (1, 1))

    async def test_conflicting_same_visit_reading_does_not_unlink_the_accepted_one(self):
        from norn.actor_state import new_actor_service
        from tests.test_partial_records import KEY
        from tests.test_research_deck import research, session
        service = new_actor_service(clock=lambda: 1000, deck="pamela-v1")
        # Exercise the retraction helper's scope independently of the Worker's
        # one-conversation capacity; prior cached receipts must not be rewritten.
        service.sessions.max_sessions = 2
        service.collection = OutboxCollector()
        source = session(research.RANDOM, rounds=3)
        request = {"journal": source, **CONSENT, "session_key": KEY, "request_id": "first-complete"}
        first = service.analyze(request)
        await ENTRY.NornSession._flush_records(self.worker, service, first)
        self.assertEqual(first["record"], {"record_id": KEY})
        other = service.analyze({**request, "request_id": "different-reading", "language": "en"})
        self.assertNotEqual(first["reading"], other["reading"])
        await ENTRY.NornSession._flush_records(self.worker, service, other)
        self.assertIsNone(other["record"])
        self.assertIsNone(service.sessions.get(other["session_id"]).record_id)
        self.assertEqual(service.sessions.get(first["session_id"]).record_id, KEY)
        self.assertEqual(service.analyze(request)["record"], {"record_id": KEY})
        self.assertEqual(self.archive.count(), 1)

    async def test_revoked_visitor_reading_remains_available_without_claiming_archive(self):
        from norn.collection import visitor_id
        from tests.test_collection import VISITOR
        await self.archive.getByName(ENTRY.ARCHIVE_NAME).apply(json.dumps({"op": "forget", "visitor_id": visitor_id(VISITOR)}))
        first = await self.analyze()
        self.assertEqual(first["status"], 200)
        self.assertTrue(first["body"]["reading"])
        self.assertIsNone(first["body"]["record"])
        again = await self.analyze()
        self.assertEqual(again, first)
        self.assertEqual((len(self.renderer.calls), self.archive.count()), (1, 0))
        [session] = self.browser.snapshot()["sessions"]
        self.assertIsNone(session.get("record_id"))

    async def test_unsettled_record_replays_as_unconfirmed(self):
        self.archive.fail = True
        first = await self.analyze()
        self.assertTrue(first["body"]["record"]["unconfirmed"])
        self.archive.fail = False
        again = await self.analyze()  # the paid receipt replays the same flagged receipt
        self.assertEqual(again["body"]["record"], first["body"]["record"])
        self.assertEqual((len(self.renderer.calls), self.archive.count()), (1, 0))
        [session] = self.browser.snapshot()["sessions"]
        self.assertEqual(session.get("record_id"), first["body"]["record"]["record_id"])


if __name__ == "__main__":
    unittest.main()
