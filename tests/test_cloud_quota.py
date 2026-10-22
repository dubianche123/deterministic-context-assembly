"""Paid admission survives object recreation and fails closed on ambiguous calls."""

import json
import sqlite3
from types import SimpleNamespace
import unittest

from norn.cloud_quota import (LEASE_TTL, RECEIPT_TTL, PaidReceipts, QuotaStore,
                             ReceiptCapacity, ReceiptConflict)


class SQL:
    def __init__(self, connection, proxy=False):
        self.connection = connection
        self.proxy = proxy
        self.fail_prefix = None

    def exec(self, query, *bindings):
        if self.fail_prefix is not None and query.startswith(self.fail_prefix):
            raise RuntimeError("Injected SQL failure")
        cursor = self.connection.execute(query, bindings)
        names = [column[0] for column in cursor.description or []]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        if self.proxy:
            # Like JsProxy row objects, these expose property access rather than
            # Python dict methods, including nullable SQL fields.
            rows = [SimpleNamespace(**row) for row in rows]
        return SimpleNamespace(toArray=lambda: rows)


class Storage:
    def __init__(self, proxy=False):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.sql = SQL(self.connection, proxy=proxy)
        self.transactions = 0

    def transaction_sync(self, callback):
        self.transactions += 1
        self.connection.execute("BEGIN")
        try:
            result = callback()
            self.connection.commit()
            return result
        except BaseException:
            self.connection.rollback()
            raise

    def rows(self, query, *bindings):
        return self.connection.execute(query, bindings).fetchall()


class PaidReceiptTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.addCleanup(self.storage.connection.close)
        self.now = 1000

    def receipts(self, **kwargs):
        return PaidReceipts(self.storage.sql, self.storage.transaction_sync,
                            clock=lambda: self.now, **kwargs)

    def test_pending_receipt_survives_eviction_and_never_renews_or_retries(self):
        first = self.receipts().reserve("analysis", "read-1", "sha256:input-a")
        self.assertTrue(first["created"])
        self.assertEqual(first["state"], "pending")
        self.assertIsNone(first["result"])
        self.now += 120
        restored = self.receipts()
        self.assertEqual(restored.get("analysis", "read-1", "sha256:input-a"),
                         {key: value for key, value in first.items() if key != "created"})
        duplicate = restored.reserve("analysis", "read-1", "sha256:input-a")
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["expires_at"], 1000 + RECEIPT_TTL)
        with self.assertRaisesRegex(ValueError, "different input"):
            self.receipts().reserve("analysis", "read-1", "sha256:input-b")
        with self.assertRaisesRegex(ValueError, "different input"):
            self.receipts().get("analysis", "read-1", "sha256:input-b")

    def test_settle_snapshot_and_receipt_atomically_and_detach_results(self):
        self.storage.sql.exec("CREATE TABLE session_snapshot (body TEXT)")
        receipts = self.receipts()
        receipts.reserve("dialogue:session", "turn-1", "sha256:input")
        response = {"status": 200, "body": {"answer": "一次结果", "turn": 1}}

        def save():
            self.storage.sql.exec("INSERT INTO session_snapshot (body) VALUES (?)", "turn-one")

        settled = receipts.settle("dialogue:session", "turn-1", "sha256:input", response, save)
        self.assertEqual(settled["state"], "settled")
        self.assertEqual(self.storage.rows("SELECT body FROM session_snapshot"), [("turn-one",)])
        response["body"]["answer"] = "caller mutation"
        settled["result"]["body"]["answer"] = "return mutation"
        stored = self.receipts().get("dialogue:session", "turn-1", "sha256:input")
        self.assertEqual(stored["result"]["body"]["answer"], "一次结果")
        again = self.receipts().reserve("dialogue:session", "turn-1", "sha256:input")
        self.assertFalse(again["created"])
        self.assertEqual(again["state"], "settled")
        self.receipts().settle("dialogue:session", "turn-1", "sha256:input", stored["result"], save)
        self.assertEqual(self.storage.rows("SELECT COUNT(*) FROM session_snapshot"), [(1,)])
        with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
            self.receipts().settle("dialogue:session", "turn-1", "sha256:input",
                                   {"status": 502, "body": {"error": "different"}}, save)

    def test_snapshot_failure_rolls_back_both_and_retains_pending(self):
        self.storage.sql.exec("CREATE TABLE session_snapshot (body TEXT)")
        self.receipts().reserve("analysis", "read", "input")

        def fail_snapshot():
            self.storage.sql.exec("INSERT INTO session_snapshot (body) VALUES ('not-durable')")
            raise RuntimeError("Snapshot failed")

        with self.assertRaisesRegex(RuntimeError, "Snapshot failed"):
            self.receipts().settle("analysis", "read", "input", {"status": 200}, fail_snapshot)
        self.assertEqual(self.storage.rows("SELECT COUNT(*) FROM session_snapshot"), [(0,)])
        self.assertEqual(self.receipts().get("analysis", "read", "input")["state"], "pending")
        self.assertFalse(self.receipts().reserve("analysis", "read", "input")["created"])

    def test_receipt_write_failure_rolls_back_preceding_snapshot(self):
        self.storage.sql.exec("CREATE TABLE session_snapshot (body TEXT)")
        self.receipts().reserve("analysis", "read", "input")
        self.storage.sql.fail_prefix = "UPDATE norn_paid_receipts"

        def save():
            self.storage.sql.exec("INSERT INTO session_snapshot (body) VALUES ('not-durable')")

        with self.assertRaisesRegex(RuntimeError, "Injected SQL failure"):
            self.receipts().settle("analysis", "read", "input", {"status": 200}, save)
        self.storage.sql.fail_prefix = None
        self.assertEqual(self.storage.rows("SELECT COUNT(*) FROM session_snapshot"), [(0,)])
        self.assertEqual(self.receipts().get("analysis", "read", "input")["state"], "pending")

    def test_expiry_is_absolute_and_expired_receipts_cannot_settle(self):
        first = self.receipts().reserve("analysis", "read", "input")
        self.now = first["expires_at"]
        self.assertIsNone(self.receipts().get("analysis", "read", "input"))
        with self.assertRaisesRegex(ValueError, "missing or expired"):
            self.receipts().settle("analysis", "read", "input", {"status": 200}, lambda: None)
        renewed = self.receipts().reserve("analysis", "read", "new-input")
        self.assertTrue(renewed["created"])
        self.assertEqual(renewed["expires_at"], self.now + RECEIPT_TTL)

    def test_error_responses_and_proxy_rows_are_round_tripped(self):
        self.storage.sql.proxy = True
        receipt = self.receipts()
        receipt.reserve("analysis", "read", "input")
        result = {"status": 502, "body": {"error": "Provider response was incomplete"}}
        receipt.settle("analysis", "read", "input", result, lambda: None)
        self.assertEqual(self.receipts().get("analysis", "read", "input")["result"], result)

    def test_capacity_preserves_existing_receipts_and_prunes_only_expired(self):
        receipt = self.receipts(capacity=2)
        first = receipt.reserve("analysis", "one", "input-one")
        self.now += 100
        receipt.reserve("analysis", "two", "input-two")
        receipt.settle("analysis", "two", "input-two", {"status": 200}, lambda: None)
        with self.assertRaises(ReceiptCapacity):
            receipt.reserve("analysis", "three", "input-three")
        self.assertFalse(receipt.reserve("analysis", "one", "input-one")["created"])
        self.assertEqual(receipt.reserve("analysis", "two", "input-two")["state"], "settled")
        with self.assertRaises(ReceiptConflict):
            receipt.reserve("analysis", "one", "different-input")
        self.now = first["expires_at"]
        self.assertTrue(receipt.reserve("analysis", "three", "input-three")["created"])
        self.assertEqual(self.storage.rows("SELECT operation_key FROM norn_paid_receipts ORDER BY operation_key"),
                         [("three",), ("two",)])
        # The public default is bounded even when callers do not configure it.
        self.now += RECEIPT_TTL
        default = self.receipts()
        for index in range(40):
            default.reserve("analysis", str(index), "input")
        with self.assertRaises(ReceiptCapacity):
            default.reserve("analysis", "overflow", "input")

    def test_discard_only_pending_matches_all_identity_fields_and_permits_retry(self):
        receipt = self.receipts()
        receipt.reserve("analysis", "read", "input")
        with self.assertRaises(ReceiptConflict):
            receipt.discard_pending("analysis", "read", "wrong-input")
        self.assertEqual(receipt.get("analysis", "read", "input")["state"], "pending")
        self.assertFalse(receipt.discard_pending("dialogue:other", "read", "input"))
        self.assertTrue(receipt.discard_pending("analysis", "read", "input"))
        self.assertFalse(receipt.discard_pending("analysis", "read", "input"))
        self.assertTrue(self.receipts().reserve("analysis", "read", "input")["created"])
        receipt.settle("analysis", "read", "input", {"status": 200}, lambda: None)
        self.assertFalse(receipt.discard_pending("analysis", "read", "input"))
        self.assertEqual(receipt.get("analysis", "read", "input")["state"], "settled")

    def test_next_expiry_prunes_at_deadline_without_refreshing_other_receipts(self):
        self.storage.sql.proxy = True
        receipt = self.receipts()
        self.assertIsNone(receipt.next_expiry())
        first = receipt.reserve("analysis", "one", "input-one")
        self.now += 100
        second = receipt.reserve("analysis", "two", "input-two")
        self.assertEqual(self.receipts().next_expiry(), first["expires_at"])
        self.now = first["expires_at"]
        self.assertEqual(self.receipts().next_expiry(), second["expires_at"])
        self.assertIsNone(receipt.get("analysis", "one", "input-one"))
        self.now = second["expires_at"]
        self.assertIsNone(self.receipts().next_expiry())
        self.assertEqual(self.storage.rows("SELECT COUNT(*) FROM norn_paid_receipts"), [(0,)])

    def test_invalid_results_and_async_callback_fail_before_settlement(self):
        receipt = self.receipts()
        receipt.reserve("analysis", "read", "input")
        called = []
        for result in ({"status": float("nan")}, {1: "coerced-key"}, {"value": (1, 2)}, []):
            with self.subTest(result=result), self.assertRaises(ValueError):
                receipt.settle("analysis", "read", "input", result, lambda: called.append(True))
        self.assertEqual(called, [])

        async def async_snapshot():
            called.append(True)

        with self.assertRaisesRegex(ValueError, "awaitable"):
            receipt.settle("analysis", "read", "input", {"status": 200}, async_snapshot)
        self.assertEqual(called, [])
        self.assertEqual(receipt.get("analysis", "read", "input")["state"], "pending")


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.addCleanup(self.storage.connection.close)
        self.now = 1000

    def quota(self):
        return QuotaStore(self.storage.sql, self.storage.transaction_sync, clock=lambda: self.now)

    def test_daily_budget_is_global_across_actors_and_recreations(self):
        for index in range(100):
            # Distinct hashed actor identities share the one global store.
            attempt = f"actor-{index}:analysis:key:input"
            self.assertTrue(self.quota().reserve(attempt)["allowed"])
            self.quota().release(attempt)
        denied = self.quota().reserve("new-cookie:analysis:key:input")
        self.assertFalse(denied["allowed"])
        self.assertEqual(denied["reason"], "daily_limit")
        self.assertEqual(denied["reset_at"], 86400)
        self.assertEqual(self.storage.rows("SELECT used FROM norn_budget_days"), [(100,)])
        self.assertNotIn("actor-", json.dumps(denied))

    def test_concurrent_limit_retry_and_crash_lease_expiry(self):
        accepted = [self.quota().reserve(f"attempt-{index}") for index in range(4)]
        self.assertTrue(all(result["allowed"] for result in accepted))
        denied = self.quota().reserve("attempt-4")
        self.assertEqual(denied["reason"], "concurrency_limit")
        self.assertEqual(denied["retry_at"], 1000 + LEASE_TTL)
        self.assertEqual(self.storage.rows("SELECT used FROM norn_budget_days"), [(4,)])
        # A denied reservation is not a consumed attempt and may retry later.
        self.now += LEASE_TTL
        self.assertTrue(self.quota().reserve("attempt-4")["allowed"])
        self.assertEqual(self.storage.rows("SELECT used FROM norn_budget_days"), [(5,)])
        duplicate = self.quota().reserve("attempt-0")
        self.assertEqual(duplicate["reason"], "duplicate")
        self.assertFalse(duplicate["allowed"])
        self.assertEqual(duplicate["lease_expires_at"], 1000 + LEASE_TTL)

    def test_duplicate_never_extends_lease_or_double_counts_even_after_release(self):
        first = self.quota().reserve("attempt")
        self.now += 100
        duplicate = self.quota().reserve("attempt")
        self.assertFalse(duplicate["allowed"])
        self.assertEqual(duplicate["reason"], "duplicate")
        self.assertEqual(duplicate["lease_expires_at"], first["lease_expires_at"])
        self.assertEqual(self.quota().release("attempt"), {"released": True})
        self.assertEqual(self.quota().release("attempt"), {"released": False})
        self.assertEqual(self.quota().release("missing"), {"released": False})
        self.assertEqual(self.quota().reserve("attempt")["reason"], "duplicate")
        self.assertEqual(self.storage.rows("SELECT used FROM norn_budget_days"), [(1,)])

    def test_release_opens_concurrent_slot_without_refunding_daily_budget(self):
        self.assertTrue(self.quota().reserve("first", max_daily=2, max_active=1)["allowed"])
        self.assertEqual(self.quota().reserve("second", max_daily=2, max_active=1)["reason"],
                         "concurrency_limit")
        self.quota().release("first")
        self.assertTrue(self.quota().reserve("second", max_daily=2, max_active=1)["allowed"])
        self.quota().release("second")
        self.assertEqual(self.quota().reserve("third", max_daily=2, max_active=1)["reason"],
                         "daily_limit")

    def test_utc_rollover_keeps_active_previous_day_leases_and_duplicate_history(self):
        self.now = 86400 - 10
        self.quota().reserve("prior-day", max_daily=1, max_active=1)
        self.now = 86400
        self.assertEqual(self.quota().reserve("prior-day", max_daily=1, max_active=1)["reason"],
                         "duplicate")
        self.assertEqual(self.quota().reserve("today", max_daily=1, max_active=1)["reason"],
                         "concurrency_limit")
        self.quota().release("prior-day")
        today = self.quota().reserve("today", max_daily=1, max_active=1)
        self.assertTrue(today["allowed"])
        self.assertEqual(today["reset_at"], 2 * 86400)
        self.assertEqual(self.storage.rows("SELECT day, used FROM norn_budget_days ORDER BY day"),
                         [(0, 1), (1, 1)])
        # At the exact UTC boundary, yesterday survives and the older day is
        # pruned. This keeps a cross-midnight retry from consuming twice.
        self.now = 2 * 86400
        self.quota().reserve("boundary-day")
        self.assertEqual(self.storage.rows("SELECT day FROM norn_budget_days ORDER BY day"), [(1,), (2,)])
        self.assertEqual(self.storage.rows("SELECT attempt_id FROM norn_budget_attempts ORDER BY attempt_id"),
                         [("boundary-day",), ("today",)])
        # Bounded retention removes only days outside the retry window.
        self.now = 3 * 86400
        self.quota().reserve("new-day")
        self.assertEqual(self.storage.rows("SELECT day FROM norn_budget_days ORDER BY day"), [(2,), (3,)])
        self.assertEqual(self.storage.rows("SELECT attempt_id FROM norn_budget_attempts ORDER BY attempt_id"),
                         [("boundary-day",), ("new-day",)])

    def test_reservation_failure_does_not_consume_daily_budget(self):
        quota = self.quota()
        self.storage.sql.fail_prefix = "INSERT INTO norn_budget_attempts"
        with self.assertRaisesRegex(RuntimeError, "Injected SQL failure"):
            quota.reserve("attempt")
        self.storage.sql.fail_prefix = None
        self.assertEqual(self.storage.rows("SELECT used FROM norn_budget_days"), [])
        self.assertTrue(self.quota().reserve("attempt")["allowed"])
        self.assertEqual(self.storage.rows("SELECT used FROM norn_budget_days"), [(1,)])

    def test_proxy_rows_and_validation(self):
        self.storage.sql.proxy = True
        self.assertTrue(self.quota().reserve("one", max_active=1)["allowed"])
        self.assertEqual(self.quota().reserve("two", max_active=1)["reason"], "concurrency_limit")
        self.quota().release("one")
        self.assertTrue(self.quota().reserve("two", max_active=1)["allowed"])
        for attempt, daily, active in (("", 100, 4), ("space forbidden", 100, 4),
                                       ("valid", True, 4), ("valid", 100, -1)):
            with self.subTest(attempt=attempt, daily=daily, active=active), self.assertRaises(ValueError):
                self.quota().reserve(attempt, daily, active)


if __name__ == "__main__":
    unittest.main()
