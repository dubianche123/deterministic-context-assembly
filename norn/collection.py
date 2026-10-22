"""Anonymous, consented collection of visits for later learning.

A record is written only when the visitor chose to weave their choices in on
the welcome screen. A finished reading becomes a full record. Until then, a
research-deck visit keeps one partial record (its journal so far), advanced by
choices and provisional attention checkpoints, so a visit that ends without a reading still counts; the
full record upgrades it in place. There is no login, cookie identity, IP, device string or
dialogue text. The browser keeps one random visitor key in its own storage and
sends it with consented readings; the server stores only its hash, so visits
from the same browser can be linked for learning without knowing who it is.
The same key lets that browser withdraw one record or all of its records.

Local runs use SQLite. The Cloudflare adapter queues the same operations and
applies them to one archive Durable Object with the same SQL.
"""

from copy import deepcopy
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time

from . import __version__
from .logs import event

CONSENT_VERSION = "norn.consent.v4"
RECORD_SCHEMA_VERSION = "norn.record.v2"
PARTIAL_SCHEMA_VERSION = "norn.partial.v1"
PARTIAL_MARKER = f'"schema_version":"{PARTIAL_SCHEMA_VERSION}"'  # in the canonical payload
LOG = logging.getLogger("norn.collection")
VISITOR_KEY_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")

SCHEMA_SQL = (
    "CREATE TABLE IF NOT EXISTS records ("
    "record_id TEXT PRIMARY KEY, visitor_id TEXT NOT NULL, visit INTEGER NOT NULL, "
    "created_day TEXT NOT NULL, consent_version TEXT NOT NULL, "
    "payload TEXT NOT NULL, annotations TEXT NOT NULL DEFAULT '{}')",
    "CREATE INDEX IF NOT EXISTS records_by_visitor ON records (visitor_id, visit)",
    # No behavior, original key or raw record identifier survives withdrawal.
    # These irreversible tokens only prevent already-authorized in-flight writes
    # from recreating data after a successful withdrawal.
    "CREATE TABLE IF NOT EXISTS archive_tombstones (scope TEXT NOT NULL, token TEXT NOT NULL, "
    "PRIMARY KEY (scope, token))",
)
# The visit number is assigned inside the write, so concurrent tabs cannot collide.
# A repeated put of the same record is a no-op, so a write whose reply was lost
# can be retried without a second row.
INSERT_SQL = ("INSERT INTO records (record_id, visitor_id, visit, created_day, consent_version, payload) "
              "SELECT ?, ?, COALESCE(MAX(visit), 0) + 1, ?, ?, ? FROM records WHERE visitor_id = ? "
              "ON CONFLICT (record_id) DO NOTHING")
SELECT_RECORD_SQL = "SELECT visitor_id, payload FROM records WHERE record_id = ?"
UPDATE_RECORD_SQL = "UPDATE records SET payload = ?, consent_version = ? WHERE record_id = ?"
SELECT_TOMBSTONE_SQL = "SELECT 1 FROM archive_tombstones WHERE scope = ? AND token = ?"
INSERT_TOMBSTONE_SQL = "INSERT OR IGNORE INTO archive_tombstones (scope, token) VALUES (?, ?)"
SELECT_ANNOTATIONS_SQL = "SELECT annotations FROM records WHERE record_id = ?"
UPDATE_ANNOTATIONS_SQL = "UPDATE records SET annotations = ? WHERE record_id = ?"
SELECT_VISITOR_SQL = "SELECT visitor_id FROM records WHERE record_id = ?"
DELETE_SQL = "DELETE FROM records WHERE record_id = ?"
FORGET_SQL = "DELETE FROM records WHERE visitor_id = ?"
PAGE_SQL = ("SELECT record_id, visit, payload, annotations FROM records WHERE record_id > ? "
            "ORDER BY record_id LIMIT ?")
COUNT_SQL = ("SELECT COUNT(*) AS n, COUNT(DISTINCT visitor_id) AS visitors, "
             "MIN(created_day) AS first_day, MAX(created_day) AS last_day, "
             f"SUM(instr(payload, '{PARTIAL_MARKER}') > 0) AS partial FROM records")
ANNOTATION_KEYS = {"post_session", "engagement"}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def visitor_id(key):
    """Hash a browser's random visitor key; the key itself is never stored."""
    if (type(key) is not str or not 22 <= len(key) <= 64 or not set(key) <= VISITOR_KEY_ALPHABET):
        raise ValueError("visitor 必须是浏览器生成的匿名随机编号")
    return hashlib.sha256(key.encode("ascii")).hexdigest()[:32]


def session_key(value):
    """A visit's random session key: 32 lowercase hex characters made by the browser."""
    if type(value) is not str or len(value) != 32 or not set(value) <= set("0123456789abcdef"):
        raise ValueError("session_key 必须是浏览器生成的 32 位随机编号")
    return value


def requested_consent(body):
    """Pop the optional consent marker and visitor key from an analysis body.

    Returns the remaining body and the hashed visitor id, or None when the
    visitor did not opt in. Only the exact current consent version with a
    valid visitor key opts in; anything else is an error rather than a silent
    opt-in or opt-out.
    """
    if not isinstance(body, dict) or not {"consent", "visitor"} & set(body):
        return body, None
    if body.get("consent") != CONSENT_VERSION:
        raise ValueError("consent 只能是当前版本的同意标记")
    return ({key: item for key, item in body.items() if key not in {"consent", "visitor"}},
            visitor_id(body.get("visitor")))


def validate_open_round(value, journal):
    """Validate a provisional attention sample without closing the real journal.

    The sample is bound to the journal's currently open round. It is never a
    completed-round observation and must not be consumed as a STOP decision.
    Readiness/choice clocks retain the event contract's exact units.
    """
    if value is None:
        return None
    from .events import MAX_DURATION_MS, _card_subset, _hover, _integer
    fields = {"round", "elapsed_ms", "hidden_ms", "ready_offset_ms", "seen_card_ids", "hover"}
    if type(value) is not dict or set(value) != fields:
        raise ValueError("open_round must contain the current round's provisional attention fields")
    start, ready, last_choice = None, None, 0
    for event in journal["events"]:
        if event["type"] == "round_started":
            start, ready, last_choice = event, None, 0
        elif event["type"] == "round_ready":
            ready = event
        elif event["type"] in ("card_selected", "card_deselected"):
            last_choice = event["t_ms"]
        elif event["type"] in ("round_completed", "session_completed"):
            start = None
    if start is None or type(value["round"]) is not int or value["round"] != start["round"]:
        raise ValueError("open_round must refer to the journal's currently unfinished round")
    elapsed = _integer(value["elapsed_ms"], "open_round elapsed_ms", 0, MAX_DURATION_MS)
    offset = ready["ready_offset_ms"] if ready is not None else None
    if value["ready_offset_ms"] != offset or type(value["ready_offset_ms"]) is bool:
        raise ValueError("open_round readiness must match its recorded round_ready")
    if offset is not None and (type(value["ready_offset_ms"]) is not int or elapsed < offset + last_choice):
        raise ValueError("open_round elapsed_ms must cover readiness and every recorded choice")
    window = elapsed - offset if offset is not None else 0
    _integer(value["hidden_ms"], "open_round hidden_ms", 0, window)
    _card_subset(value["seen_card_ids"], start["card_ids"], "open_round seen_card_ids", nullable=True)
    if ready is None and value["hover"] is not None:
        raise ValueError("open_round hover requires recorded readiness")
    _hover(value["hover"], start["card_ids"], window)
    return deepcopy(value)


def build_partial(journal, *, visitor, session, experiments=None, deployment="local", clock=time.time,
                  open_round=None, experience=None, product=None, finale=None, pacing=None):
    """A consented visit that has no reading yet: its journal so far, keyed by the session."""
    record = {
        "schema_version": PARTIAL_SCHEMA_VERSION,
        "record_id": session,
        "visitor_id": visitor,
        "created_day": time.strftime("%Y-%m-%d", time.gmtime(clock())),
        "consent_version": CONSENT_VERSION,
        "app_version": __version__,
        "deployment": deployment,
        "experiments": dict(experiments or {}),
        "journal": deepcopy(journal),
    }
    if open_round is not None:
        record["open_round"] = validate_open_round(open_round, journal)
    if experience is not None:
        from .experience import validate_experience
        record["experience"] = validate_experience(experience, journal)
    _product_sidecars(record, product, finale, pacing)
    if experience is not None or product is not None:
        from .experience import archive_cohort_fields
        record.update(archive_cohort_fields(deployment, record.get("experience"), record.get("product")))
    return record


def build_record(bundle, generation, *, visitor, prompt_version=None, experiments=None,
                 deployment="local", clock=time.time, language="zh", probe=None, experience=None,
                 product=None, finale=None, pacing=None):
    """One finished reading: the raw journal is the learning fact; the rest
    identifies which code produced the reading. No derived profile is stored,
    so records stay usable after the scoring model changes. A pilot's
    validated new-image probe journal (norn.probe) rides along when present."""
    profile = bundle["profile"]
    route = generation or {}
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "record_id": session_key(experiments["session_key"]) if experiments and experiments.get("session_key")
                     else secrets.token_hex(16),
        "visitor_id": visitor,
        "created_day": time.strftime("%Y-%m-%d", time.gmtime(clock())),
        "consent_version": CONSENT_VERSION,
        "app_version": __version__,
        "deployment": deployment,
        "experiments": dict(experiments or {}),
        "journal": deepcopy(bundle["journal"]),
        "events_hash": profile.get("events_hash"),
        "profile_hash": profile.get("profile_hash"),
        "profile_version": profile.get("schema_version"),
        "algorithm_version": profile.get("algorithm_version"),
        "reading": route.get("text"),
        "generation": {"provider": route.get("provider"), "model": route.get("model"),
                       "prompt_version": prompt_version,
                       **({"language": "en"} if language == "en" else {}),
                       "simulated": bool(route.get("simulated")), "status": route.get("status", "ok")},
    }
    if probe is not None:
        record["probe"] = deepcopy(probe)
    if experience is not None:
        from .experience import validate_experience
        record["experience"] = validate_experience(experience, bundle["journal"])
    _product_sidecars(record, product, finale, pacing)
    if experience is not None or product is not None:
        from .experience import archive_cohort_fields
        record.update(archive_cohort_fields(deployment, record.get("experience"), record.get("product")))
    return record


def _product_sidecars(record, product, finale, pacing):
    """Product observations may accompany either a pending or finished reading."""
    if product is not None:
        record["product"] = product
    if finale is not None:
        record["finale"] = finale
    if pacing is not None:
        record["pacing"] = pacing
    if product is not None or finale is not None or pacing is not None:
        from .product import validate_product_fields
        validate_product_fields(record)


class Collector:
    """Operation queue shared by every store: put / annotate / withdraw / forget."""

    enabled = True

    def submit(self, record):
        """Queue a new record; returns the receipt the browser keeps."""
        stored = self._apply({"op": "put", "record": record})
        return {"record_id": record["record_id"]} if stored else None

    def submit_partial(self, record):
        """Queue the latest snapshot of a visit that has no reading yet."""
        return self._apply({"op": "put_partial", "record": record})

    def annotate(self, record_id, fields):
        if set(fields) - ANNOTATION_KEYS:
            raise ValueError("Unsupported record annotation")
        return self._apply({"op": "annotate", "record_id": record_id, "fields": deepcopy(fields)})

    def withdraw(self, record_id, visitor):
        """Delete one record of this visitor (visitor is the hashed id)."""
        if type(record_id) is not str or len(record_id) != 32:
            raise ValueError("record_id 无效")
        return self._apply({"op": "withdraw", "record_id": record_id, "visitor_id": visitor})

    def forget(self, visitor):
        """Delete every record this visitor has left."""
        return self._apply({"op": "forget", "visitor_id": visitor})

    def _apply(self, operation):
        raise NotImplementedError


class DisabledCollector(Collector):
    enabled = False

    def submit(self, record):
        return None

    def submit_partial(self, record):
        return None

    def annotate(self, record_id, fields):
        return None

    def withdraw(self, record_id, visitor):
        raise ValueError("本服务未开启记录")

    def forget(self, visitor):
        raise ValueError("本服务未开启记录")


def _barrier_token(scope, visitor, record_id=None):
    """Domain-separated irreversible tokens, never an original withdrawal key."""
    return hashlib.sha256(_canonical(["norn.archive.withdrawal.v1", scope, visitor, record_id]).encode("utf-8")).hexdigest()


def _prefix(earlier, later):
    """The older journal must contain exactly the facts at the start of the new one."""
    if earlier.get("schema_version") != later.get("schema_version"):
        return False
    old_events, new_events = earlier.get("events", []), later.get("events", [])
    return len(old_events) <= len(new_events) and _canonical(old_events) == _canonical(new_events[:len(old_events)])


def _hover_extends(earlier, later):
    """An open hover's last interval may extend, but past intervals cannot change."""
    if earlier is None:
        return True
    if later is None or len(later) < len(earlier):
        return False
    if not earlier:
        return True
    return (earlier[:-1] == later[:len(earlier) - 1]
            and earlier[-1][:2] == later[len(earlier) - 1][:2]
            and earlier[-1][2] <= later[len(earlier) - 1][2])


def _attention_extends(earlier, later):
    if later is None or later["round"] != earlier["round"] or later["elapsed_ms"] < earlier["elapsed_ms"]:
        return False
    if earlier["ready_offset_ms"] is not None and later["ready_offset_ms"] != earlier["ready_offset_ms"]:
        return False
    seen = earlier["seen_card_ids"]
    return (later["hidden_ms"] >= earlier["hidden_ms"]
            and (seen is None or later["seen_card_ids"] is not None
                 and later["seen_card_ids"][:len(seen)] == seen)
            and _hover_extends(earlier["hover"], later["hover"]))


def _checkpoint_extends(previous, current):
    """Preserve sampled open attention or cover it with the eventual real close."""
    earlier = previous.get("open_round")
    if earlier is None:
        return True
    closed = next((event for event in current["journal"]["events"]
                   if event["type"] == "round_completed" and event["round"] == earlier["round"]), None)
    if closed is not None:
        later = {"round": closed["round"], "elapsed_ms": closed["duration_ms"],
                 "hidden_ms": closed["hidden_ms"], "seen_card_ids": closed["seen_card_ids"],
                 "ready_offset_ms": earlier["ready_offset_ms"], "hover": closed["hover"]}
        return _attention_extends(earlier, later)
    return _attention_extends(earlier, current.get("open_round"))


def apply_operation(execute, operation):
    """Apply one operation in the caller's transaction, including withdrawal barriers.

    An existing visit may only advance along the same event history. A complete
    record upgrades its partial row in place; it never deletes/reinserts it.
    Revocation tokens stop late writes across tabs/actors after withdrawal.
    """
    kind = operation["op"]
    if kind in ("put", "put_partial"):
        record = deepcopy(operation["record"])
        identity, visitor = record["record_id"], record["visitor_id"]
        session = record.get("experiments", {}).get("session_key")
        if kind == "put" and session and identity != session:
            return False
        if (execute(SELECT_TOMBSTONE_SQL, "visitor", _barrier_token("visitor", visitor))
                or execute(SELECT_TOMBSTONE_SQL, "record", _barrier_token("record", visitor, identity))):
            return False
        from .experience import experience_extends, validate_archive_cohort, validate_experience
        from .product import finale_extends, product_extends, validate_product_fields
        from .pacing import trace_extends
        if "experience" in record:
            # The shared transaction boundary also validates outbox/RPC writes,
            # so another actor cannot bypass sidecar invariants.
            record["experience"] = validate_experience(record["experience"], record.get("journal"))
        validate_product_fields(record)
        validate_archive_cohort(record)
        rows = execute(SELECT_RECORD_SQL, identity)
        if rows:
            owner, payload = rows[0]
            if not hmac.compare_digest(owner, visitor):
                return False
            previous = json.loads(payload)
            record["created_day"] = previous["created_day"]
            if previous.get("schema_version") != PARTIAL_SCHEMA_VERSION:
                # An archive RPC retry is idempotent. A different reading cannot
                # silently claim that this already-completed visit was stored.
                return kind == "put" and _canonical(record) == payload
            if (not _prefix(previous["journal"], record["journal"])
                    or not _checkpoint_extends(previous, record)
                    or not experience_extends(previous.get("experience"), record.get("experience"))
                    or not product_extends(previous.get("product"), record.get("product"))
                    or not finale_extends(previous.get("finale"), record.get("finale"))
                    or not trace_extends(previous.get("pacing"), record.get("pacing"))):
                return False
            if previous.get("product") is not None and any(previous.get(key) != record.get(key)
                    for key in ("deployment", "source_deployment")):
                return False
            if kind == "put_partial" and _canonical(previous["journal"]) == _canonical(record["journal"]):
                if (previous.get("open_round") == record.get("open_round")
                        and previous.get("experience") == record.get("experience")
                        and previous.get("product") == record.get("product")
                        and previous.get("finale") == record.get("finale")
                        and previous.get("pacing") == record.get("pacing")):
                    return True
            execute(UPDATE_RECORD_SQL, _canonical(record), record["consent_version"], identity)
            return True
        execute(INSERT_SQL, identity, visitor, record["created_day"], record["consent_version"],
                _canonical(record), visitor)
        return True
    if kind == "annotate":
        rows = execute(SELECT_ANNOTATIONS_SQL, operation["record_id"])
        if not rows:
            return False
        merged = {**json.loads(rows[0][0]), **operation["fields"]}
        execute(UPDATE_ANNOTATIONS_SQL, _canonical(merged), operation["record_id"])
        return True
    if kind == "withdraw":
        visitor, identity = operation["visitor_id"], operation["record_id"]
        rows = execute(SELECT_VISITOR_SQL, identity)
        if rows and not hmac.compare_digest(rows[0][0], visitor):
            raise ValueError("撤回凭证无效")
        execute(INSERT_TOMBSTONE_SQL, "record", _barrier_token("record", visitor, identity))
        if rows:
            execute(DELETE_SQL, identity)
        return True
    if kind == "forget":
        visitor = operation["visitor_id"]
        execute(INSERT_TOMBSTONE_SQL, "visitor", _barrier_token("visitor", visitor))
        execute(FORGET_SQL, visitor)
        return True
    raise ValueError("Unknown collection operation")


class SQLiteCollector(Collector):
    """Local durable store; one file, safe for the threaded HTTP server."""

    def __init__(self, path):
        import sqlite3  # local runs only; Workers store records in a Durable Object
        self.path = str(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(records)")}
        if columns and "visitor_id" not in columns:
            # A pre-v2 table: replace it only while it holds nothing.
            if self._db.execute("SELECT COUNT(*) FROM records").fetchone()[0]:
                raise sqlite3.DatabaseError("records table predates norn.record.v2; move the file aside")
            self._db.execute("DROP TABLE records")
        for statement in SCHEMA_SQL:
            self._db.execute(statement)

    def _execute(self, sql, *params):
        return self._db.execute(sql, params).fetchall()

    def _apply(self, operation):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                result = apply_operation(self._execute, operation)
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")
            return result

    def page(self, after="", limit=500):
        with self._lock:
            rows = self._execute(PAGE_SQL, after, limit)
        return [{**json.loads(payload), "visit": visit, "annotations": json.loads(notes)}
                for _, visit, payload, notes in rows]

    def stats(self):
        with self._lock:
            count, visitors, first, last, partial = self._execute(COUNT_SQL)[0]
        return {"records": count, "visitors": visitors, "first_day": first, "last_day": last,
                "partial_records": partial or 0}

    def close(self):
        with self._lock:
            self._db.close()


class OutboxCollector(Collector):
    """Hosted adapters: queue operations, then apply them after the request."""

    def __init__(self):
        self.outbox = []

    def _apply(self, operation):
        self.outbox.append(operation)
        return True

    def drain(self):
        pending, self.outbox = self.outbox, []
        return pending


def open_collector(data_dir):
    """Return a SQLite collector, or a disabled one when storage is unusable.

    A read-only or missing data directory must not take down the experience;
    the welcome screen then simply offers no weaving choice.
    """
    if data_dir is None:
        return DisabledCollector()
    from pathlib import Path
    import sqlite3
    try:
        root = Path(data_dir).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        collector = SQLiteCollector(root / "records.sqlite3")
    except (OSError, sqlite3.Error) as exc:
        event(LOG, "collection.unavailable", logging.WARNING, error_type=type(exc).__name__)
        return DisabledCollector()
    event(LOG, "collection.ready", store="sqlite", **collector.stats())
    return collector
