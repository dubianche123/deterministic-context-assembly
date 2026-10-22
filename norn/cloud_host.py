"""Await language rendering between the shared prepare/finish stages.

The host serializes one browser's requests. Its SQLite pending receipt is
committed before any outbound I/O. A lost outcome is never dispatched twice.
"""

from copy import deepcopy
import json
import time

from .actor_state import DEEPSEEK_GENERATION_ROUTE, MAX_LIVE_MODEL_CALLS, dump_actor_state
from .cloud_provider import generate_cloud, prepare_cloud_request
from .cloud_quota import ReceiptCapacity, ReceiptConflict
from .events import content_hash
from .service import MAX_USER_CHARS, MAX_ENGLISH_USER_CHARS, RequestError


def model_route(env):
    provider = str(getattr(env, "NORN_PROVIDER", "mock"))
    if provider == "mock":
        return {"provider": "mock"}
    if provider != "openai-compatible":
        raise RequestError("在线模型配置无效，请联系维护者。", 503)
    return deepcopy(DEEPSEEK_GENERATION_ROUTE)


def _operation_id(value):
    if (type(value) is not str or not 1 <= len(value) <= 128
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise RequestError("在线生成需要有效的重试操作 ID。")
    return value


def response_status(result):
    return {"provider_unavailable": 503, "invalid_output": 502,
            "policy_blocked": 422, "internal_error": 500}.get(result.get("status"), 200)


class HostedGenerator:
    def __init__(self, service, receipts, quota, *, actor_id, api_key,
                 write_snapshot, schedule, generate=None, before_settle=None):
        self.service, self.receipts, self.quota = service, receipts, quota
        self.actor_id, self.api_key = actor_id, api_key
        self.write_snapshot, self.schedule = write_snapshot, schedule
        self.generate = generate or generate_cloud
        # Async side effects of the result (archive writes) that must settle
        # before the receipt does, so a retried ID replays the true outcome.
        self.before_settle = before_settle

    async def analyze(self, body):
        data = {key: value for key, value in body.items() if key != "request_id"}
        return await self._execute("analysis", body.get("request_id"), content_hash(data),
                                   lambda: self.service._prepare_analysis(data), "analysis")

    async def dialogue(self, body):
        key, message = body.get("session_id"), body.get("user_message")
        if type(key) is not str or not key or len(key) > 128:
            raise RequestError("请提供分析返回的 session_id")
        # Do not let the locale lookup bypass the authoritative operation path.
        # _prepare_dialogue retains the terminal missing-session outcome.
        try:
            language = self.service.sessions.get(key).context.get("block", {}).get("language", "zh")
        except RequestError:
            language = "zh"
        maximum = MAX_ENGLISH_USER_CHARS if language == "en" else MAX_USER_CHARS
        if type(message) is not str or not message.strip():
            raise RequestError("Please enter a question of at most 400 characters." if language == "en" else "请输入一句不超过100字的话")
        message = message.strip()
        if len(message) > maximum:
            raise RequestError("Your question must be at most 400 characters." if language == "en" else "输入不能超过100字")
        return await self._execute("dialogue:" + key, body.get("turn_id"),
                                   content_hash({"message": message}),
                                   lambda: self.service._prepare_dialogue(key, message), "dialogue")

    def _cached(self, receipt, task):
        if receipt["state"] == "pending":
            raise RequestError("上一次生成的结果尚未确认，该操作不会自动重复调用。请明确重试或重新开始。", 409)
        result = deepcopy(receipt["result"])
        if task == "analysis" and result["body"].get("session_id"):
            try:
                self.service.sessions.get(result["body"]["session_id"])
            except RequestError as exc:
                raise RequestError("该操作的临时会话已失效，请重新生成判词。", 410) from exc
        return result

    async def _execute(self, scope, key, fingerprint, prepare, task):
        key = _operation_id(key)
        try:
            old = self.receipts.get(scope, key, fingerprint)
        except ReceiptConflict as exc:
            raise RequestError("同一操作 ID 不能用于不同请求", 409) from exc
        if old:
            return self._cached(old, task)
        plan = prepare()
        if "response" in plan:
            return {"status": 200, "body": plan["response"]}
        generation, trace = plan["generation"], None
        dispatch = generation is None
        if dispatch:
            if not self.api_key:
                raise RequestError("在线模型尚未配置，请联系维护者。", 503)
            try:
                prepare_cloud_request(plan["prompt"])
            except ValueError as exc:
                raise RequestError("本次模型上下文超出在线演示限制，请减少选择后重试。", 413) from exc
            if self.service.limits.model_calls >= MAX_LIVE_MODEL_CALLS:
                raise RequestError("本浏览器的模型尝试已达上限，请稍后再试。", 429)
        try:
            reservation = self.receipts.reserve(scope, key, fingerprint)
        except ReceiptCapacity as exc:
            raise RequestError("本浏览器的重试记录已满，请稍后再试。", 429) from exc
        except ReceiptConflict as exc:
            raise RequestError("同一操作 ID 不能用于不同请求", 409) from exc
        if not reservation["created"]:
            return self._cached(reservation, task)
        # Even a crash before the model call leaves a conservative pending ID.
        await self.schedule(self.service)
        attempt = content_hash({"actor": self.actor_id, "scope": scope, "key": key,
                                "fingerprint": fingerprint})
        admitted = False
        try:
            if dispatch:
                admission = await self.quota.reserve(attempt)
                if isinstance(admission, str):
                    admission = json.loads(admission)
                if not admission["allowed"]:
                    if admission["reason"] != "duplicate":
                        self.receipts.discard_pending(scope, key, fingerprint)
                        await self.schedule(self.service)
                        raise RequestError("在线模型当前已达共享调用上限，请稍后重试。", 429)
                    raise RequestError("这次模型调用已被预留，结果未知时不会重复调用。", 409)
                admitted = True
                self.service.limits.model_calls += 1
                # Commit the browser counter before sending paid HTTP traffic.
                self.write_snapshot(dump_actor_state(self.service))
                started = time.perf_counter()
                generation = await self.generate(plan["prompt"], api_key=self.api_key)
                generation, trace = self.service._finish_render(
                    plan["prompt"], {"task": task}, generation, started)
            if task == "analysis":
                result = self.service._finish_analysis(plan, generation, trace)
                if result.get("session_id"):
                    self.service.sessions.get(result["session_id"]).generation_route = deepcopy(DEEPSEEK_GENERATION_ROUTE)
            else:
                result = self.service._finish_dialogue(plan, generation, trace)
            if self.before_settle is not None:
                await self.before_settle(result)
            status = response_status(result)
            if status != 200:
                result["error"] = result.get("reading") or result.get("reply") or "模型暂时无法回应，请重试。"
            settled = {"status": status, "body": result}
            state = dump_actor_state(self.service)
            self.receipts.settle(scope, key, fingerprint, settled,
                                 lambda: self.write_snapshot(state, atomic=False))
            await self.schedule(self.service)
            return settled
        finally:
            if admitted:
                try:
                    await self.quota.release(attempt)
                except Exception:
                    # The durable lease expires without refunding the attempt.
                    pass
