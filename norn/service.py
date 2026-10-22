"""Shared deterministic analysis with bounded, temporary local conversations."""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import logging
import secrets
import threading
import time
import uuid

from .core import engine_module
from .logs import event

LOG = logging.getLogger("norn.service")


MAX_ROUNDS = 256
MAX_SELECTIONS = 256
MAX_REVISIONS = 4096
MAX_USER_CHARS = 100
MAX_ENGLISH_USER_CHARS = 400
SESSION_TTL_SECONDS = 1800
MAX_SESSIONS = 256


class RequestError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass
class Session:
    analysis: dict
    reading: str
    max_turns: int
    expires_at: float
    replay_bundle: dict | None = None
    history: list = field(default_factory=list)
    context: dict = field(default_factory=dict)
    prompt: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    generation_route: dict = field(default_factory=dict)
    turns: int = 0
    # Optional outcome label given after the reading; never part of the journal.
    post_session: dict | None = None
    # Anonymous collection record of this reading, when the visitor opted in.
    record_id: str | None = None
    lock: object = field(default_factory=threading.Lock)


class SessionStore:
    """Process memory only: an expiry and a size bound, with no persistence."""

    def __init__(self, ttl=SESSION_TTL_SECONDS, max_sessions=MAX_SESSIONS, clock=time.monotonic):
        if ttl <= 0 or max_sessions < 1:
            raise ValueError("Session limits must be positive")
        self.ttl = ttl
        self.max_sessions = max_sessions
        self.clock = clock
        self._sessions = OrderedDict()
        self._lock = threading.Lock()

    def _expire(self, now):
        expired = [key for key, session in self._sessions.items() if session.expires_at <= now]
        for key in expired:
            del self._sessions[key]

    def create(self, analysis, reading, max_turns, replay_bundle=None, *, context=None, prompt=None, calls=None):
        with self._lock:
            now = self.clock()
            self._expire(now)
            while len(self._sessions) >= self.max_sessions:
                self._sessions.popitem(last=False)
            key = secrets.token_urlsafe(32)
            initial = deepcopy(prompt or {})
            history = deepcopy(initial.get("messages", []))
            if history:
                history.append({"role": "assistant", "content": reading})
            self._sessions[key] = Session(
                analysis=deepcopy(analysis), reading=reading, max_turns=max_turns,
                expires_at=now + self.ttl, replay_bundle=deepcopy(replay_bundle),
                history=history, context=deepcopy(context or {}), prompt=initial,
                calls=deepcopy(calls or []))
            return key

    def get(self, key):
        with self._lock:
            self._expire(self.clock())
            session = self._sessions.get(key)
            if session is None:
                raise RequestError("会话已失效，请重新选图生成判词。", 404)
            return session


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise RequestError(f"{name} 必须是 {minimum} 到 {maximum} 之间的整数")
    return value


def validate_analysis(body, card_ids):
    if not isinstance(body, dict):
        raise RequestError("请求必须是 JSON 对象")
    rounds = _integer(body.get("total_rounds", 1), "total_rounds", 1, MAX_ROUNDS)
    validated = {"total_rounds": rounds}
    for name, limit in (("selections", MAX_SELECTIONS), ("deselection_events", MAX_REVISIONS)):
        items = body.get(name, [])
        if not isinstance(items, list) or len(items) > limit:
            raise RequestError(f"{name} 必须是最多 {limit} 项的数组")
        output = []
        seen_ids = set()
        for item in items:
            if not isinstance(item, dict):
                raise RequestError(f"{name} 中每一项必须是对象")
            card_id = item.get("id")
            if not isinstance(card_id, str) or card_id not in card_ids:
                raise RequestError(f"{name} 包含未知卡牌")
            rnd = _integer(item.get("round"), "round", 1, rounds)
            if name == "selections" and card_id in seen_ids:
                raise RequestError("同一卡牌不能重复选择")
            seen_ids.add(card_id)
            output.append({"id": card_id, "round": rnd})
        validated[name] = output

    durations = body.get("round_durations", [])
    if not isinstance(durations, list) or len(durations) > rounds:
        raise RequestError("round_durations 必须是每轮最多一项的数组")
    validated["round_durations"] = []
    seen_rounds = set()
    for item in durations:
        if not isinstance(item, dict):
            raise RequestError("round_durations 中每一项必须是对象")
        rnd = _integer(item.get("round"), "round", 1, rounds)
        duration = _integer(item.get("duration_ms"), "duration_ms", 0, 86_400_000)
        if rnd in seen_rounds:
            raise RequestError("同一轮不能重复提交耗时")
        seen_rounds.add(rnd)
        validated["round_durations"].append({"round": rnd, "duration_ms": duration})
    return validated


class NornService:
    def __init__(self, provider="mock", config=None, sessions=None, generate=None,
                 max_model_calls=100, max_concurrency=2, collector=None, deployment="local",
                 algorithm_version=None, deck=None):
        from .profile import ALGORITHM_VERSION, SEQUENTIAL_ALGORITHM_VERSION
        if algorithm_version is not None and algorithm_version not in (ALGORITHM_VERSION, SEQUENTIAL_ALGORITHM_VERSION):
            raise ValueError("Service scoring version must be norn.scoring.v4 or norn.scoring.v5")
        # A host-level route, not a mutable option in an analysis HTTP body.
        # Independent service instances can reproduce historical experiments
        # concurrently without patching process-global profile constants.
        self.algorithm_version = algorithm_version or ALGORITHM_VERSION
        from .providers import provider_config
        if generate is None:
            from .providers import generate
        self.provider = provider
        self.config = provider_config(provider, config)
        from .context import PROMPT_VERSION
        self.prompt_version = PROMPT_VERSION
        self.sessions = sessions if sessions is not None else SessionStore()
        self.generate = generate
        self.fast = engine_module("fast_thinker")
        self.router = engine_module("template_router")
        self.dialogue_engine = engine_module("dialogue_thinker")
        self.card_ids = frozenset(self.fast._load_cards())
        # A research deployment serves one research deck instead of Norn's own cards.
        self.deck = engine_module("research_deck").load_deck(deck) if deck else None
        from .runtime import DispatchLimits, OperationStore
        self.limits = DispatchLimits(max_model_calls, max_concurrency)
        self.operations = OperationStore()
        from .collection import DisabledCollector
        self.collection = collector if collector is not None else DisabledCollector()
        self.deployment = deployment

    def health(self):
        return {"status": "ok", "provider": self.provider, "simulated": self.provider == "mock",
                "offline": self.provider == "mock", "limits": self.limits.snapshot(),
                "collection": self.collection.enabled}

    def _finale_entry(self, scope, key):
        if type(key) is not str or not 1 <= len(key) <= 128:
            raise RequestError("Invalid finale capability")
        with self.operations._lock:
            entry = self.operations._entries.get((scope, key))
            if entry is None or entry.expires_at <= self.operations.clock() or not entry.done or entry.error:
                raise RequestError("This finale has expired; start a new game", 410)
            return entry

    def finale_start(self, body):
        from .events import content_hash
        from .product import validate_product
        from .collection import requested_consent, session_key
        from .experience import validate_experience
        from .finale import create, public_challenge
        allowed = {"journal", "challenge_id", "product", "jev_session", "consent", "visitor",
                   "session_key", "experience", "language"}
        if self.deck is None or type(body) is not dict or not {"journal", "challenge_id", "product"} <= set(body) or set(body) - allowed:
            raise RequestError("Finale start needs a sealed exploration and product version")
        try:
            product = validate_product(body["product"], body["journal"])
            _, visitor = requested_consent(body)
            session = session_key(body["session_key"]) if visitor is not None and "session_key" in body else None
            if visitor is not None and session is None:
                raise ValueError("A contributing game requires its visit key")
            if visitor is None and any(k in body for k in ("session_key", "experience")):
                raise ValueError("Private games do not submit research sidecars")
            ticket = body.get("jev_session")
            if ticket is not None:
                from .jev_live import _TICKET
                if type(ticket) is not str or not _TICKET.fullmatch(ticket):
                    raise ValueError("Invalid pre-entry Jev capability")
            def seal():
                private = create(body["journal"], self.deck, body["challenge_id"])
                private.update(product=product, visitor=visitor, session_key=session)
                if "experience" in body:
                    private["experience"] = validate_experience(body["experience"], private["journal"])
                if ticket is not None:
                    private["jev_session"] = ticket
                    private["jev"] = {"status": "pending", "provider_calls": 0}
                return private
            private = self.operations.execute("finale", body["challenge_id"], content_hash(body), seal)
            return {**public_challenge(private), "product_version": product["schema_version"],
                    "jev_status": private["jev"]["status"]}
        except RequestError:
            raise
        except ValueError as exc:
            raise RequestError(str(exc)) from exc

    def finale_choose(self, body):
        from .events import content_hash
        from .finale import finish
        if (type(body) is not dict or not {"challenge_id", "card_id", "elapsed_ms"} <= set(body)
                or set(body) - {"challenge_id", "card_id", "elapsed_ms", "observation"}):
            raise RequestError("Choose one sealed finale picture")
        key = body["challenge_id"]
        def choose():
            entry = self._finale_entry("finale", key)
            with entry.lock:
                try:
                    result = finish(entry.result, body["card_id"], body["elapsed_ms"], body.get("observation"))
                except ValueError as exc:
                    raise RequestError(str(exc)) from exc
                self._collect_finale(entry.result, result)
                return result
        return self.operations.execute("finale-choice", key, content_hash(body), choose)

    def _collect_finale(self, private, result):
        if private.get("visitor") is None or not self.collection.enabled:
            return
        from .collection import build_partial
        try:
            record = build_partial(private["journal"], visitor=private["visitor"], session=private["session_key"],
                experiments={"display": engine_module("research_deck").arm(private["journal"]),
                             "deck": self.deck.name, "session_key": private["session_key"]},
                deployment=self.deployment, experience=private.get("experience"),
                product=private["product"], finale=result, pacing=private["pacing"])
            self.collection.submit_partial(record)
        except Exception as exc:
            event(LOG, "finale.checkpoint_failed", logging.ERROR, error_type=type(exc).__name__)

    def finale_jev_plan(self, body):
        """Reserve one opponent attempt before I/O; the host persists this state."""
        from .finale import jev_journal
        if type(body) is not dict or set(body) != {"challenge_id"}:
            raise RequestError("Jev finale needs only its sealed challenge")
        key = body["challenge_id"]
        with self.operations._lock:
            if ("finale-choice", key) in self.operations._entries:
                raise RequestError("Jev cannot predict after the final choice", 409)
        entry = self._finale_entry("finale", key)
        with entry.lock:
            private = entry.result
            if "jev_session" not in private:
                raise RequestError("Jev was not enabled before this game", 403)
            if private.get("jev_started"):
                return {"cached": True, "status": "ready" if private["jev"]["status"] == "ok" else "failed",
                        "provider_calls": 0, "challenge_id": key}
            private["jev_started"] = True
            return {"cached": False, "challenge_id": key,
                    "body": {"jev_session": private["jev_session"], "journal": jev_journal(private, self.deck)}}

    def finale_settle_jev(self, key, result):
        from .jev import DEFAULT_MODEL
        with self.operations._lock:
            if ("finale-choice", key) in self.operations._entries:
                return {"status": "late", "challenge_id": key, "provider_calls": result.get("provider_calls", 0)}
        entry = self._finale_entry("finale", key)
        with entry.lock:
            cards = entry.result["candidate_ids"]
            probs = result.get("probabilities")
            status = "failed"
            entry.result["jev"] = {"status": "failed", "model": DEFAULT_MODEL,
                                   "provider_calls": result.get("provider_calls", 0)}
            if (result.get("status") == "ok" and result.get("candidate_ids") == cards
                    and type(probs) is list and len(probs) == 4):
                highest = max(probs)
                predicted = next(c for c, p in zip(cards, probs) if p == highest)
                entry.result["jev"].update(status="ok", probabilities=deepcopy(probs), predicted_card_id=predicted)
                status = "ready"
            return {"status": status, "challenge_id": key, "provider_calls": result.get("provider_calls", 0)}

    def pacing(self, body):
        from .product import validate_product
        from .pacing import trace
        if self.deck is None or type(body) is not dict or set(body) != {"journal", "product"}:
            raise RequestError("Pacing needs this game's exploration journal")
        try:
            validate_product(body["product"], body["journal"])
            return trace(body["journal"], self.deck)
        except ValueError as exc:
            raise RequestError(str(exc)) from exc

    def _render(self, prompt, **kwargs):
        # Rendering is replaceable; it cannot mutate the deterministic profile.
        self.limits.enter(self.provider != "mock")
        started = time.perf_counter()
        try:
            generation = self.generate(self.provider, deepcopy(prompt),
                                       config=deepcopy(self.config), **deepcopy(kwargs))
        except Exception:
            # An ambiguous attempt still consumes its reservation and gets a
            # retry receipt and a trace. Do not echo remote exception details.
            generation = {"text": "这次生成暂时无法完成，请稍后重新尝试。",
                          "status": "internal_error", "blocked": False,
                          "reason_codes": ["internal_generation_error"], "usage": {}}
        finally:
            self.limits.leave()
        return self._finish_render(prompt, kwargs, generation, started)

    def _finish_render(self, prompt, kwargs, generation, started):
        """Normalize one settled generation at the shared provider boundary.

        Async hosts perform their own bounded dispatch, then use this same
        finalizer so request identity, sanitization, and traces stay identical.
        """
        generation = deepcopy(generation)
        if prompt.get("language") == "en":
            from .providers import localize_result
            generation = localize_result(generation, "en")
        if generation.get("status") == "sanitized":
            generation.update(status="ok", output_status="sanitized")
        generation["provider_request_id"] = generation.get("request_id")
        generation["request_id"] = uuid.uuid4().hex
        generation.setdefault("elapsed_ms", round((time.perf_counter() - started) * 1000, 3))
        generation.setdefault("provider_calls", 0 if self.provider == "mock" else 1)
        trace = {key: deepcopy(generation.get(key)) for key in (
            "request_id", "provider_request_id", "status", "output_status", "reason_codes", "elapsed_ms", "usage",
            "provider_calls", "model", "cache")}
        trace.update(task=kwargs.get("task"), provider=self.provider,
                     simulated=self.provider == "mock", prompt=deepcopy(prompt),
                     request_view="logical provider boundary before protocol mapping")
        return generation, trace

    def analyze(self, body):
        from .events import content_hash
        if not isinstance(body, dict):
            raise RequestError("请求必须是 JSON 对象")
        data = {key: value for key, value in body.items() if key != "request_id"}
        try:
            fingerprint = content_hash(data)
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
        result = self.operations.execute("analysis", body.get("request_id"), fingerprint,
                                         lambda: self._analyze(data))
        if result.get("session_id"):
            try:
                self.sessions.get(result["session_id"])
            except RequestError as exc:
                raise RequestError("该操作的临时会话已失效，请重新生成判词。", 410) from exc
        return result

    def _analyze(self, body):
        plan = self._prepare_analysis(body)
        generation, trace = plan["generation"], None
        if generation is None:
            generation, trace = self._render(plan["prompt"], task="analysis", analysis=plan["analysis"])
        return self._finish_analysis(plan, generation, trace)

    def _prepare_analysis(self, body):
        """Build authoritative inputs without dispatching or creating a session."""
        from .profile import (build_event_profile, build_replay_bundle, build_snapshot_profile,
                              REPLAY_SCHEMA_VERSION)
        from .events import replay_events
        from .context import build_session_context, build_analysis_prompt
        from .collection import requested_consent
        if not isinstance(body, dict):
            raise RequestError("请求必须是 JSON 对象")
        try:
            has_experience = "experience" in body
            raw_experience = body.get("experience")
            raw_product, finale_id = body.get("product"), body.get("finale_id")
            language = body.get("language", "zh")
            if language not in ("zh", "en"):
                raise RequestError("language must be zh or en")
            body = {key: value for key, value in body.items() if key not in {"language", "experience", "product", "finale_id"}}
            body, visitor = requested_consent(body)
            if self.deck is not None:
                from .collection import session_key
                session = body.get("session_key")
                body = {key: value for key, value in body.items() if key != "session_key"}
                if session is not None:
                    if visitor is None:
                        raise RequestError("session_key 只随同意保存的记录一起提交")
                    session = session_key(session)
                plan = self._prepare_research(body, visitor, language, session)
                from .experience import validate_experience
                if has_experience:
                    plan["experience"] = validate_experience(raw_experience, plan["bundle"]["journal"])
                if raw_product is not None or finale_id is not None:
                    from .product import validate_product
                    product = validate_product(raw_product, plan["bundle"]["journal"])
                    result = deepcopy(self._finale_entry("finale-choice", finale_id).result)
                    private = self._finale_entry("finale", finale_id).result
                    if (result["events_hash"] != plan["bundle"]["profile"]["events_hash"]
                            or result["profile_hash"] != plan["bundle"]["profile"]["profile_hash"]
                            or product != private["product"] or visitor != private.get("visitor")
                            or session != private.get("session_key")
                            or plan.get("experience") != private.get("experience")):
                        raise RequestError("The reading must preserve its sealed game and privacy choice", 409)
                    from .research_inspection import build_context
                    context = build_context(plan["bundle"]["profile"], self.deck, language=language, finale=result)
                    plan.update(context=context, prompt=build_analysis_prompt(context), generation=None, limit=3,
                                product=product, finale=result, pacing=result["pacing"])
                return plan
            probe = body.get("probe")
            body = {key: value for key, value in body.items() if key != "probe"}
            if probe is not None:
                # New-image pilot choices are kept only in a consented record, next to a real journal.
                from .probe import validate_probe
                if visitor is None or "journal" not in body:
                    raise RequestError("probe 只能随同意保存的事件记录一起提交")
                probe = validate_probe(probe)[0]
            if "journal" in body:
                if set(body) != {"journal"}:
                    raise RequestError("journal 不能与汇总快照字段混用")
                canonical = replay_events(body["journal"])["journal"]
                bundle = {"schema_version": REPLAY_SCHEMA_VERSION, "journal": canonical,
                          "profile": build_event_profile(canonical, algorithm_version=self.algorithm_version)}
            else:
                request = validate_analysis(body, self.card_ids)
                bundle = build_replay_bundle(build_snapshot_profile(request, algorithm_version=self.algorithm_version))
            from .experience import validate_experience
            experience = validate_experience(raw_experience, bundle.get("journal")) if has_experience else None
        except RequestError:
            raise
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
        versioned = bundle["profile"]
        analysis = versioned["analysis"]
        from .experiments import assign
        experiments = assign(visitor or secrets.token_hex(16))
        # The display arm is randomized per session in the browser and logged in the journal itself.
        arm = engine_module("display_policy").session_arm(bundle["journal"]) if bundle.get("journal") else None
        if arm is not None:
            experiments["display"] = arm
        # An experiment arm named "reading_prompt" picks the projection per visitor.
        context = build_session_context(versioned, experiments.get("reading_prompt", self.prompt_version),
                                        language=language)
        prompt = build_analysis_prompt(context)
        generation = None
        if not analysis["total_selections"]:
            # Empty choice is a product rule, independent of the model route.
            generation = {
                "text": ("You did not keep any pictures this time. There is no need to force a meaning from that. "
                         "When you feel like exploring again, begin with a picture that makes you pause."
                         if language == "en" else engine_module("lambda_function").EMPTY_SELECTION_READING),
                "status": "ok", "blocked": False, "provider": self.provider,
                "simulated": self.provider == "mock", "provider_calls": 0,
                "usage": {"input_tokens": None, "output_tokens": None,
                          "cached_tokens": None, "cached_tokens_observed": False,
                          "source": "not_applicable"},
            }
        limit = self.dialogue_engine.dialogue_limit_for_rounds(analysis["total_rounds"], analysis["data_density"])
        # Only complete event journals are learning facts; legacy snapshots are not.
        return {"bundle": bundle, "analysis": analysis, "context": context,
                "prompt": prompt, "limit": limit, "generation": generation,
                "visitor": visitor if bundle.get("journal") is not None else None,
                "experiments": experiments, "probe": probe,
                **({"experience": experience} if has_experience else {})}

    def _prepare_research(self, body, visitor, language, session=None):
        """A research deck's journal: its own cards, display v2 and the research-v6 reading."""
        from .events import replay_events
        from .context import build_analysis_prompt
        from .experiments import assign
        research = engine_module("research_deck")
        if set(body) != {"journal"}:
            raise RequestError("研究牌库只接受完整的事件记录")
        journal = body["journal"]
        if type(journal) is not dict or type(journal.get("events")) is not list:
            raise RequestError("研究牌库需要有效的事件记录")
        if research.deck_of(journal) != self.deck.name:
            raise RequestError("这份记录不属于本研究牌库")
        replay = replay_events(journal, cards=self.deck.cards)
        canonical = replay["journal"]
        if any(event["display"]["deck"] != self.deck.label for event in canonical["events"]
               if event["type"] == "round_started"):
            raise RequestError("这份记录来自另一版研究牌库")
        from .research_inspection import build_bundle, build_context
        bundle = build_bundle(canonical, self.deck)
        profile = bundle["profile"]
        analysis = profile["analysis"]
        context = build_context(profile, self.deck, language=language)
        experiments = {**assign(visitor or secrets.token_hex(16)), "display": research.arm(canonical),
                       "deck": self.deck.name, **({"session_key": session} if session else {})}
        generation = None
        if not analysis["total_selections"]:
            generation = {
                "text": ("You did not keep any pictures this time. There is no need to force a meaning from that. "
                         "When you feel like exploring again, begin with a picture that makes you pause."
                         if language == "en" else engine_module("lambda_function").EMPTY_SELECTION_READING),
                "status": "ok", "blocked": False, "provider": self.provider,
                "simulated": self.provider == "mock", "provider_calls": 0,
                "usage": {"input_tokens": None, "output_tokens": None, "cached_tokens": None,
                          "cached_tokens_observed": False, "source": "not_applicable"}}
        limit = self.dialogue_engine.dialogue_limit_for_rounds(analysis["total_rounds"], analysis["data_density"])
        return {"bundle": bundle, "analysis": analysis, "context": context, "prompt": build_analysis_prompt(context),
                "limit": limit, "generation": generation, "visitor": visitor, "experiments": experiments,
                "probe": None}

    def _finish_analysis(self, plan, generation, trace=None):
        """Commit a settled reading, creating a session only on success."""
        generation = generation if generation is not None else plan["generation"]
        if generation is None:
            raise ValueError("Analysis generation has not settled")
        bundle, analysis = plan["bundle"], plan["analysis"]
        versioned, limit = bundle["profile"], plan["limit"]
        response = {
            # Scoring v4 computes no four-axis heuristic; these stay null there.
            "mbti_type": analysis.get("mbti_type"),
            "confidence": analysis.get("confidence"),
            "reading": generation["text"],
            "language": plan["context"]["block"].get("language", "zh"),
            "max_question_chars": (MAX_ENGLISH_USER_CHARS if plan["context"]["block"].get("language") == "en"
                                   else MAX_USER_CHARS),
            "mode": analysis["data_density"],
            "total_rounds": analysis["total_rounds"],
            "total_selections": analysis["total_selections"],
            "choice_revision": analysis.get("choice_revision", {}),
            "empty_selection": not analysis["total_selections"],
            "max_dialogue_turns": limit,
            "coords_avg": analysis.get("coords_avg"),
            "preference": deepcopy(analysis.get("preference")),
            **self._profile_metadata(versioned),
            **self._generation_metadata(generation),
        }
        ok = generation.get("status", "ok") == "ok"
        if self.deck is not None:
            from .portrait import build_portrait
            response["portrait"] = build_portrait(versioned, self.deck, response["language"])
            if plan.get("finale") is not None:
                response["finale"] = deepcopy(plan["finale"])
                response["persona"] = deepcopy(plan["context"]["block"]["reading_intent"]["persona"])
        if ok:
            response["session_id"] = self.sessions.create(
                analysis, generation["text"], limit, bundle, context=plan["context"],
                prompt=plan["prompt"], calls=[trace] if trace is not None else [])
            response["record"] = self._collect(plan, generation, response["session_id"])
        event(LOG, "analysis.completed", status=generation.get("status", "ok"),
              rounds=analysis["total_rounds"], selections=analysis["total_selections"],
              provider=generation.get("provider", self.provider), simulated=self.provider == "mock",
              elapsed_ms=generation.get("elapsed_ms"), collected=bool(ok and response.get("record")))
        return response

    def _collect(self, plan, generation, session_id):
        """Store a consented reading; failure never costs the visitor their reading."""
        if plan.get("visitor") is None or not self.collection.enabled:
            return None
        from .collection import build_record
        try:
            receipt = self.collection.submit(build_record(
                plan["bundle"], {**generation, "provider": generation.get("provider", self.provider),
                                 "simulated": self.provider == "mock"},
                visitor=plan["visitor"], prompt_version=plan["context"].get("prompt_version"),
                experiments=plan["experiments"], deployment=self.deployment,
                language=plan["context"]["block"].get("language", "zh"), probe=plan.get("probe"),
                experience=plan.get("experience"), product=plan.get("product"),
                finale=plan.get("finale"), pacing=plan.get("pacing")))
        except Exception as exc:
            event(LOG, "record.store_failed", logging.ERROR, error_type=type(exc).__name__)
            return None
        if receipt is not None:
            self.sessions.get(session_id).record_id = receipt["record_id"]
            event(LOG, "record.stored", deployment=self.deployment)
        return receipt

    def drop_record(self, record_id, *, session_id=None):
        """A queued record never reached storage (hosted outbox): unlink it from
        its session and from any stored reply, so a retry cannot claim it was kept."""
        def retract(result):
            if session_id is not None and result.get("session_id") != session_id:
                return
            if isinstance(result.get("record"), dict) and result["record"].get("record_id") == record_id:
                result["record"] = None
        with self.sessions._lock:
            for key, session in self.sessions._sessions.items():
                if session.record_id == record_id and (session_id is None or key == session_id):
                    session.record_id = None
        self.operations.rewrite(retract)

    def mark_record_unconfirmed(self, record_id, *, session_id=None):
        """The archive neither confirmed nor ruled out a queued record: keep the
        receipt, so the visitor can still withdraw it, and say it may be missing."""
        def flag(result):
            if session_id is not None and result.get("session_id") != session_id:
                return
            if isinstance(result.get("record"), dict) and result["record"].get("record_id") == record_id:
                result["record"]["unconfirmed"] = True
        self.operations.rewrite(flag)

    def _annotate_record(self, session, fields):
        if session.record_id is None or not self.collection.enabled:
            return
        try:
            stored = self.collection.annotate(session.record_id, fields)
            # False: the visitor already withdrew the record, so there is nothing to annotate.
            event(LOG, "record.annotated" if stored else "record.annotate_missing", fields=sorted(fields))
        except Exception as exc:
            event(LOG, "record.annotate_failed", logging.ERROR, error_type=type(exc).__name__)

    def record_partial(self, body):
        """Keep a consented research visit that has no reading yet: its journal so far.

        Choices stay as real open-journal events. Optional ``open_round`` holds
        provisional attention without inventing a completion or STOP. The
        archive advances this same visit monotonically until its real reveal.
        """
        from .collection import build_partial, requested_consent, session_key, validate_open_round
        from .events import replay_events
        required = {"consent", "visitor", "session_key", "journal"}
        if not isinstance(body, dict) or not required <= set(body) or set(body) - required - {"open_round", "experience", "product"}:
            raise RequestError("中途记录必须包含 consent, visitor, session_key, journal，可选 open_round、experience")
        if self.deck is None or not self.collection.enabled:
            return {"stored": False}
        research = engine_module("research_deck")
        try:
            body, visitor = requested_consent(body)
            session = session_key(body["session_key"])
            journal = body["journal"]
            if type(journal) is not dict or research.deck_of(journal) != self.deck.name:
                raise ValueError("这份记录不属于本研究牌库")
            canonical = replay_events(journal, require_complete=False, cards=self.deck.cards)["journal"]
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
        events = canonical["events"]
        if any(event["type"] == "session_completed" for event in events) and "product" not in body:
            raise RequestError("已揭示的会话请提交完整记录")
        if any(event["display"]["deck"] != self.deck.label for event in events if event["type"] == "round_started"):
            raise RequestError("这份记录来自另一版研究牌库")
        try:
            open_round = validate_open_round(body.get("open_round"), canonical)
            from .experience import validate_experience
            experience = validate_experience(body["experience"], canonical) if "experience" in body else None
            from .product import validate_product
            product = validate_product(body["product"], canonical) if "product" in body else None
            from .pacing import trace
            pacing = trace(canonical, self.deck) if product is not None else None
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
        record = build_partial(canonical, visitor=visitor, session=session, deployment=self.deployment,
                               experiments={"display": research.arm(canonical), "deck": self.deck.name},
                               open_round=open_round, experience=experience, product=product, pacing=pacing)
        stored = self.collection.submit_partial(record)
        event(LOG, "record.partial", rounds=sum(e["type"] == "round_started" for e in events), stored=bool(stored))
        return {"stored": bool(stored)}

    def withdraw_record(self, body):
        """Delete one record, or every record, left by the browser holding this visitor key."""
        from .collection import visitor_id
        if not isinstance(body, dict) or set(body) not in ({"visitor", "record_id"}, {"visitor", "all"}) \
                or body.get("all", True) is not True:
            raise RequestError("撤回请求必须是 {visitor, record_id} 或 {visitor, all: true}")
        try:
            visitor = visitor_id(body["visitor"])
            if "all" in body:
                self.collection.forget(visitor)
            else:
                self.collection.withdraw(body["record_id"], visitor)
        except ValueError as exc:
            raise RequestError(str(exc), 403 if "凭证" in str(exc) else 400) from exc
        event(LOG, "record.forgotten" if "all" in body else "record.withdrawn")
        return {"withdrawn": True}

    def _inspection(self, profile, journal, *, context=None, prompt=None, calls=None, complete=True, compact=False):
        if self.deck is not None:
            from .research_inspection import inspection
            result = inspection(profile, journal, self.deck, context=context, prompt=prompt, calls=calls,
                                complete=complete, compact=compact)
            result["limits"] = self.limits.snapshot()
            return result
        from .context import build_session_context, build_analysis_prompt
        from .runtime import request_metrics
        context = deepcopy(context) if context is not None else build_session_context(profile)
        prompt = deepcopy(prompt) if prompt is not None else build_analysis_prompt(context)
        analysis = profile["analysis"]
        ledger = analysis["attribution"]
        mode = analysis["data_density"]
        route_reason = ("0–5 轮 → sparse_mode" if analysis["total_rounds"] <= 5 else
                        "6–20 轮 → dense_mode" if analysis["total_rounds"] <= 20 else
                        "21–256 轮 → overflow_mode")
        if not analysis["total_selections"]:
            route_reason = "没有有效选择 → sparse_mode；固定空选择判词不调用模型"
        rounds = deepcopy(ledger.get("rounds", []))
        numeric = engine_module("numeric")
        with numeric.use(profile.get("numerical_contract", numeric.NATIVE_CONTRACT)):
            for row in rounds:
                row["base_weight"] = self.fast._selection_weight_factors(
                    row["round"], analysis["total_rounds"], {})[0]
        model_engine = engine_module("preference_model")
        catalog = model_engine.load_catalog()
        projection = catalog.get("projection", {})
        learned = analysis.get("preference") or {}
        choice = engine_module("choice_model")
        guarded = engine_module("choice_policy")
        if profile.get("preference_model"):
            scores = {row["card_id"]: row["score"] for row in model_engine.predict_scores(profile["preference_model"])}
        elif learned.get("model_version") in (choice.MODEL_VERSION, guarded.MODEL_VERSION):
            catalog = choice.load_catalog()
            theta = (learned["effective"]["theta"] if learned.get("model_version") == guarded.MODEL_VERSION
                     else learned["posterior"]["mean"])
            scores = dict(zip(catalog.ids, choice.card_scores(theta, catalog)))
        else:
            scores = {}
        selected = {row["card_id"] for row in ledger["selections"]}
        preference_map = {
            "projection": {name: deepcopy(projection.get(name)) for name in (
                "method", "explained_variance_ratio", "coordinate_extent")},
            "points": [{"card_id": card, "x": point[0], "y": point[1],
                        "score": scores.get(card), "selected": card in selected}
                       for card, point in projection.get("points", {}).items()],
            "scope": "fixed image-feature similarity projection; color is relative ranking, not personality probability"}
        result = {"profile": deepcopy(profile), "journal": deepcopy(journal),
                "profile_hash": profile.get("profile_hash"), "complete": complete,
                "coords_avg": deepcopy(analysis.get("coords_avg")), "attribution": deepcopy(ledger),
                "preference": deepcopy(analysis.get("preference")),
                "preference_map": preference_map,
                "round_weights": rounds, "mode": mode, "route_reason": route_reason,
                "reconstructed_coords": (engine_module("ledger").reconstruct_coords(ledger)
                                         if "dimensions" in ledger else None),
                "context": context, "prompt": prompt, "calls": deepcopy(calls or []),
                "metrics": request_metrics(journal if journal is not None else profile.get("input", {}),
                                           prompt, calls),
                "limits": self.limits.snapshot()}
        if compact:
            from .inspection import compact_inspection
            return compact_inspection(result)
        return result

    def preview(self, body):
        """Pure event-prefix projection: never seal facts, create sessions or render."""
        if self.deck is not None:
            if (not isinstance(body, dict) or "journal" not in body or set(body) - {"journal", "compact", "product"}
                    or "compact" in body and type(body["compact"]) is not bool):
                raise RequestError("预览请求必须包含 journal，可选 compact 必须为布尔值")
            from .research_inspection import validated
            try:
                replay = validated(body["journal"], self.deck, require_complete=False)
                numeric = engine_module("numeric")
                with numeric.use(numeric.NATIVE_CONTRACT):
                    profile = engine_module("research_deck").build_profile(replay["journal"], self.deck,
                                                                           replay["events_hash"])
                result = self._inspection(profile, replay["journal"], complete=replay["complete"],
                                          compact=body.get("compact", False))
                if "product" in body:
                    result["pacing"] = self.pacing({"journal": body["journal"], "product": body["product"]})
                return result
            except ValueError as exc:
                raise RequestError(str(exc)) from exc
        numeric = engine_module("numeric")
        with numeric.use(numeric.PORTABLE_CONTRACT):
            return self._preview(body)

    def _preview(self, body):
        from .events import replay_events, content_hash
        from .profile import (build_event_profile, metadata_version, ALGORITHM_VERSION,
                              PREVIEW_SCHEMA_VERSION, choice_analysis)
        if (not isinstance(body, dict) or "journal" not in body
                or set(body) - {"journal", "compact"}
                or "compact" in body and type(body["compact"]) is not bool):
            raise RequestError("预览请求必须包含 journal，可选 compact 必须为布尔值")
        try:
            replay = replay_events(body["journal"], require_complete=False)
            if replay["complete"]:
                profile = build_event_profile(replay["journal"], algorithm_version=self.algorithm_version)
            else:
                analysis = choice_analysis(replay["input"], {
                    "selection_event_ids": replay["selection_event_ids"],
                    "revision_event_ids": replay["revision_event_ids"]}, replay["journal"], include_open=True,
                    algorithm_version=self.algorithm_version)
                preview_schema = PREVIEW_SCHEMA_VERSION if self.algorithm_version == ALGORITHM_VERSION else "norn.preview.v4"
                profile = {"schema_version": preview_schema, "source": "event_preview",
                           "provisional": True, "algorithm_version": self.algorithm_version,
                           "numerical_contract": engine_module("numeric").PORTABLE_CONTRACT,
                           "metadata_version": metadata_version(), "events_hash": replay["events_hash"],
                           "analysis": analysis, "feature_version": analysis["preference"]["feature_version"]}
                profile["profile_hash"] = content_hash(profile)
            return self._inspection(profile, replay["journal"], complete=replay["complete"],
                                    compact=body.get("compact", False))
        except ValueError as exc:
            raise RequestError(str(exc)) from exc

    def inspect_session(self, key, *, compact=False):
        session = self.sessions.get(key)
        with session.lock:
            if session.replay_bundle is None:
                raise RequestError("该会话没有可检查的画像", 404)
            result = self._inspection(session.replay_bundle["profile"], session.replay_bundle["journal"],
                                      context=session.context, prompt=session.prompt, calls=session.calls,
                                      compact=compact)
            result.update(turn_count=session.turns, max_turns=session.max_turns)
            return result

    @staticmethod
    def _profile_metadata(profile):
        return {"profile_version": profile["schema_version"],
                "algorithm_version": profile["algorithm_version"],
                "metadata_version": profile["metadata_version"],
                "profile_hash": profile["profile_hash"],
                "events_hash": profile.get("events_hash"),
                "profile_source": profile["source"],
                "feature_version": profile.get("feature_version"),
                "preference_version": (profile.get("preference_model")
                                       or profile.get("analysis", {}).get("preference") or {}).get("model_version")}

    def trajectory(self, body):
        """Journal or exported record in, every round's posterior out, in one reply.

        An open round is left out until it closes. A replay export is verified
        first and retains its scoring policy; historical v1–v3 exports keep
        their existing v4 display projection. This is not a hashed profile.
        """
        from .events import replay_events
        from .replay import replay_document
        if not isinstance(body, dict):
            raise RequestError("请求必须是 JSON 对象")
        profile = None
        offline_comparison = None
        has_offline_comparison = False
        try:
            if body.get("schema_version") in ("norn.replay.v1", "norn.research-replay.v1"):
                bundle = replay_document(body)
                if bundle.get("journal") is None:
                    raise RequestError("旧版汇总快照没有逐轮展示记录，无法回放轨迹")
                journal, profile, source = bundle["journal"], bundle["profile"], "verified_export"
            elif str(body.get("schema_version", "")).startswith("norn.events."):
                journal, source = body, "event_journal"
            elif set(body) == {"journal"}:
                journal, source = body["journal"], "event_journal"
            elif set(body) == {"journal", "comparison_report"} and self.deck is not None:
                journal, source = body["journal"], "event_journal"
                offline_comparison = body["comparison_report"]
                has_offline_comparison = True
            else:
                raise RequestError("轨迹请求需要 journal、事件记录或导出的回放记录")
            if self.deck is not None:
                from .research_inspection import validated
                replay = validated(journal, self.deck, require_complete=False)
                if profile is not None and profile.get("deck") != self.deck.label:
                    raise ValueError("This record belongs to another deck")
            else:
                replay = replay_events(journal, require_complete=False)
            if has_offline_comparison:
                from .pamela_compare_cli import validate_saved_report
                offline_comparison = validate_saved_report(offline_comparison, replay["journal"], self.deck)
            result = self._trajectory(replay, algorithm_version=profile.get("algorithm_version") if profile else None)
            if has_offline_comparison:
                result["offline_comparison"] = offline_comparison
                result["offline_comparison_verification"] = {
                    "record_binding_verified": True, "report_content_hash_verified": True,
                    "provider_call_authenticated": False,
                    "scope": "The saved report matches this journal, deck and frozen-prefix task; "
                             "its self-reported hash does not authenticate a Jev request."}
        except RequestError:
            raise
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
        result.update(source=source, verified=profile is not None,
                      profile=self._profile_metadata(profile) if profile is not None else None)
        return result

    def session_trajectory(self, key):
        """The frozen session's journal, plus its version and call disclosures."""
        from .events import replay_events
        from .runtime import request_metrics
        if not isinstance(key, str) or not key or len(key) > 128:
            raise RequestError("请提供有效的 session_id")
        session = self.sessions.get(key)
        with session.lock:
            if session.replay_bundle is None or session.replay_bundle.get("journal") is None:
                raise RequestError("该会话没有可回放的逐轮记录", 404)
            bundle = deepcopy(session.replay_bundle)
            prompt, calls = deepcopy(session.prompt), deepcopy(session.calls)
        if self.deck is not None:
            from .research_inspection import validated
            replay = validated(bundle["journal"], self.deck, require_complete=False)
        else:
            replay = replay_events(bundle["journal"], require_complete=False)
        result = self._trajectory(replay,
                                  algorithm_version=bundle["profile"]["algorithm_version"])
        metrics = request_metrics(bundle["journal"], prompt, calls)
        metrics.pop("usage", None)
        result.update(source="session", verified=True, profile=self._profile_metadata(bundle["profile"]),
                      metrics=metrics, calls=[{
                          **{name: call.get(name) for name in ("task", "provider", "model", "simulated", "status",
                                                               "output_status", "elapsed_ms", "provider_calls")},
                          # Vendor raw payloads stay in the full inspection.
                          "usage": {name: value for name, value in (call.get("usage") or {}).items() if name != "raw"}}
                          for call in calls])
        return result

    def _trajectory(self, replay, *, algorithm_version=None):
        if self.deck is not None:
            from .research_inspection import trajectory
            return trajectory(replay, self.deck)
        from .profile import ALGORITHM_VERSION
        model, numeric = engine_module("choice_model"), engine_module("numeric")
        scoring_version = algorithm_version or self.algorithm_version
        scorer = engine_module("choice_policy") if scoring_version == ALGORITHM_VERSION else model
        # Native libm is enough for a projection that is never hashed; the
        # profile's own posterior summary keeps the portable contract.
        with numeric.use(numeric.NATIVE_CONTRACT):
            result = scorer.trajectory(model.observations(replay["journal"]))
        events = replay["journal"]["events"]
        result.update(numerical_contract=numeric.NATIVE_CONTRACT, complete=replay["complete"],
                      open_round=bool(events) and events[-1]["type"] not in ("round_completed", "session_completed"),
                      events_hash=replay["events_hash"], event_count=len(events), provider_calls=0)
        return result

    def export_session(self, key):
        if not isinstance(key, str) or not key or len(key) > 128:
            raise RequestError("请提供有效的 session_id")
        session = self.sessions.get(key)
        with session.lock:
            if session.replay_bundle is None:
                raise RequestError("该会话没有可导出的回放记录", 404)
            bundle = deepcopy(session.replay_bundle)
            if session.post_session is not None:
                bundle["post_session"] = deepcopy(session.post_session)
            return bundle

    def record_feedback(self, body):
        """Attach or replace the optional post-reading rating of one session.

        Skipping is the default and sends nothing. The rating annotates the
        sealed session; it cannot change its journal, profile or any hash.
        """
        from .events import POST_SESSION_SCHEMA_VERSION, content_hash, validate_post_session
        from .profile import selected_card_ids
        if not isinstance(body, dict) or set(body) != {"session_id", "reading_rating", "closest_card_id"}:
            raise RequestError("评分请求必须且只能包含 session_id、reading_rating 和 closest_card_id")
        key = body["session_id"]
        if not isinstance(key, str) or not key or len(key) > 128:
            raise RequestError("请提供分析返回的 session_id")
        session = self.sessions.get(key)
        with session.lock:
            if session.replay_bundle is None:
                raise RequestError("该会话没有可评分的画像", 404)
            profile = session.replay_bundle["profile"]
            route = session.generation_route or {}
            provider = route.get("provider") or self.provider
            record = {"schema_version": POST_SESSION_SCHEMA_VERSION,
                      "reading_rating": body["reading_rating"], "closest_card_id": body["closest_card_id"],
                      "profile_hash": profile["profile_hash"], "reading_hash": content_hash(session.reading),
                      "context_hash": session.context.get("context_hash"),
                      "provider": provider, "simulated": provider == "mock"}
            try:
                record = validate_post_session(record, profile_hash=profile["profile_hash"],
                                               selected_card_ids=selected_card_ids(profile))
            except ValueError as exc:
                raise RequestError(str(exc)) from exc
            session.post_session = record
            self._annotate_record(session, {"post_session": record})
            return {"saved": True, "post_session": deepcopy(record)}

    def replay(self, document):
        from .replay import replay_document
        try:
            bundle = replay_document(document)
            if self.deck is not None and bundle["profile"].get("deck") != self.deck.label:
                raise ValueError("This record belongs to another deck")
            if self.deck is None and bundle["profile"].get("deck"):
                raise ValueError("Open this record with its matching deck")
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
        profile = bundle["profile"]
        return {"verified": True, **self._inspection(profile, bundle["journal"])}

    def _generation_metadata(self, generation):
        return {
            "blocked": generation.get("blocked", False),
            "status": generation.get("status", "ok"),
            "usage": generation.get("usage", {}),
            "provider": generation.get("provider", self.provider),
            "simulated": generation.get("simulated", self.provider == "mock"),
            "provider_calls": generation.get("provider_calls", 0),
            "elapsed_ms": generation.get("elapsed_ms"),
            "request_id": generation.get("request_id"),
            "output_status": generation.get("output_status", generation.get("status", "ok")),
            "reason_codes": generation.get("reason_codes", []),
        }

    def dialogue(self, body):
        from .events import content_hash
        if not isinstance(body, dict):
            raise RequestError("请求必须是 JSON 对象")
        key = body.get("session_id")
        if not isinstance(key, str) or not key or len(key) > 128:
            raise RequestError("请提供分析返回的 session_id")
        # This lookup only selects validation copy and length. A missing or
        # expired session must fail inside the operation below, so its terminal
        # error remains a replayable receipt instead of bypassing idempotency.
        try:
            language = self.sessions.get(key).context.get("block", {}).get("language", "zh")
        except RequestError:
            language = "zh"
        maximum = MAX_ENGLISH_USER_CHARS if language == "en" else MAX_USER_CHARS
        message = body.get("user_message")
        if not isinstance(message, str) or not message.strip():
            raise RequestError("Please enter a question of at most 400 characters." if language == "en" else "请输入一句不超过100字的话")
        message = message.strip()
        if len(message) > maximum:
            raise RequestError("Your question must be at most 400 characters." if language == "en" else "输入不能超过100字")
        return self.operations.execute(
            "dialogue:" + key, body.get("turn_id"), content_hash({"message": message}),
            lambda: self._dialogue(key, message))

    def _dialogue(self, key, message):
        session = self.sessions.get(key)
        with session.lock:
            plan = self._prepare_dialogue(key, message)
            if "response" in plan:
                return plan["response"]
            generation, trace = self._render(
                plan["prompt"], task="dialogue", analysis=plan["analysis"],
                user_message=plan["user_message"], turn_count=plan["turn"], max_turns=session.max_turns)
            return self._finish_dialogue(plan, generation, trace)

    def _prepare_dialogue(self, key, message):
        """Read the next frozen prompt while the host holds its session lock."""
        from .context import build_dialogue_prompt
        session = self.sessions.get(key)
        identity = self._profile_metadata(session.replay_bundle["profile"]) if session.replay_bundle else {}
        if session.turns >= session.max_turns:
            language = session.context.get("block", {}).get("language", "zh")
            text = ("The loom rests here for this reading. You can begin again when another picture catches your eye."
                    if language == "en" else self.dialogue_engine.lockout_text(session.max_turns))
            return {"response": {"reply": text, "text": text, "blocked": True, "ended": True,
                    "turn_count": session.turns, "max_turns": session.max_turns,
                    "provider": self.provider, "simulated": self.provider == "mock",
                    "status": "ok", "usage": {}, "language": language,
                    "max_question_chars": MAX_ENGLISH_USER_CHARS if language == "en" else MAX_USER_CHARS,
                    **identity}}
        # The browser cannot replace the profile, history, or turn allowance.
        turn = session.turns + 1
        if len(session.calls) >= 32:
            raise RequestError("此会话的生成尝试已达上限，请重新开始。", 429)
        prompt = build_dialogue_prompt(session.context, session.history, message, turn, session.max_turns)
        return {"session": session, "prompt": prompt, "turn": turn, "identity": identity,
                "analysis": deepcopy(session.analysis), "user_message": message, "generation": None}

    def _finish_dialogue(self, plan, generation, trace=None):
        """Commit a settled attempt; only successful text advances the history."""
        if "response" in plan:
            return deepcopy(plan["response"])
        if generation is None:
            raise ValueError("Dialogue generation has not settled")
        session, prompt = plan["session"], plan["prompt"]
        if trace is not None:
            session.calls.append(deepcopy(trace))
        session.prompt = deepcopy(prompt)
        success = generation.get("status", "ok") == "ok"
        if success:
            session.history = deepcopy(prompt["messages"]) + [
                {"role": "assistant", "content": generation["text"]}]
            session.turns = plan["turn"]
            # Engagement is a count only; dialogue text never reaches a record.
            self._annotate_record(session, {"engagement": {"dialogue_turns": session.turns,
                                                           "max_turns": session.max_turns}})
        event(LOG, "dialogue.completed", status=generation.get("status", "ok"), turn=plan["turn"],
              max_turns=session.max_turns, elapsed_ms=generation.get("elapsed_ms"))
        return {"reply": generation["text"], "text": generation["text"],
                "language": session.context.get("block", {}).get("language", "zh"),
                "max_question_chars": (MAX_ENGLISH_USER_CHARS if session.context.get("block", {}).get("language") == "en"
                                       else MAX_USER_CHARS),
                "ended": success and session.turns >= session.max_turns,
                "turn_count": session.turns, "max_turns": session.max_turns,
                **plan["identity"], **self._generation_metadata(generation)}
