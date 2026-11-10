"""Kairos System One client (Minara Cloud gateway), following the published contract:

- POST {KAIROS_BASE_URL}/v1/systemone, non-streaming, model "DMIND/Kairos" exactly.
- Bearer KAIROS_API_KEY from the server environment only; never logged.
- Errors in either `detail{...}` or `error{...}` shape; keep request IDs for diagnostics.
- Bounded retries: 429 and 502/503/504 only. Invalid input is never retried unchanged.
- A failed request or missing answer is an ERROR, never a negative judgment.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import aiohttp


class KairosError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None, request_id: str | None = None, retryable: bool = False):
        super().__init__(f"[{kind}] {message}")
        self.kind = kind
        self.message = message
        self.status = status
        self.request_id = request_id
        self.retryable = retryable


@dataclass
class KairosResult:
    answers: dict[str, Any]
    usage: dict[str, Any]
    metadata: dict[str, Any]
    latency_ms: float
    attempts: int
    request_bytes: int
    raw: dict[str, Any] = field(default_factory=dict)


class RateBudget:
    """Sliding one-hour request budget so a bug can never burn through the account."""

    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self.calls: deque[float] = deque()

    def try_take(self) -> bool:
        now = time.time()
        while self.calls and now - self.calls[0] > 3600:
            self.calls.popleft()
        if len(self.calls) >= self.per_hour:
            return False
        self.calls.append(now)
        return True

    def used(self) -> int:
        now = time.time()
        return sum(1 for t in self.calls if now - t <= 3600)


def _error_from(status: int, body: Any) -> KairosError:
    msg, code, rid = f"HTTP {status}", None, None
    if isinstance(body, dict):
        d = body.get("detail")
        e = body.get("error")
        if isinstance(d, dict):
            msg = d.get("message") or msg
            code = d.get("code") or d.get("error_type")
            rid = d.get("request_id") or d.get("upstream_request_id")
        elif isinstance(d, (str, list)):
            msg = json.dumps(d)[:500] if isinstance(d, list) else d
        if isinstance(e, dict):
            msg = e.get("message") or msg
            code = e.get("code") or code
            rid = e.get("request_id") or rid
        elif isinstance(e, str):
            msg = e
    elif isinstance(body, str) and body:
        msg = body[:300]
    if status in (401, 403):
        kind = "auth"
        if status == 403 or "model" in str(msg).lower() or "group" in str(msg).lower():
            msg += " | This API key may not access DMIND/Kairos: check the key belongs to a Kairos-enabled group and its model permissions include the exact model ID."
    elif status == 429:
        kind = "rate_limited"
    elif status in (400, 422):
        kind = "invalid_request"
    elif status == 413:
        kind = "payload_too_large"
    elif status in (502, 503, 504):
        kind = "upstream_unavailable"
    else:
        kind = f"http_{status}"
    return KairosError(kind, f"{msg}" + (f" (code={code})" if code else ""), status, rid, retryable=status in (429, 502, 503, 504))


class KairosClient:
    def __init__(self, base_url: str, api_key_getter, session: aiohttp.ClientSession, *, model: str = "DMIND/Kairos",
                 timeout_s: float = 120, max_retries: int = 2):
        self.url = base_url.rstrip("/") + "/v1/systemone"
        self._key = api_key_getter
        self.session = session
        self.model = model
        self.timeout = aiohttp.ClientTimeout(total=timeout_s, connect=10)
        self.max_retries = max_retries

    @property
    def configured(self) -> bool:
        return bool(self._key())

    async def ask(self, state: Any, questions: dict[str, Any], *, task_profile: str | None = None) -> KairosResult:
        key = self._key()
        if not key:
            raise KairosError("not_configured", "KAIROS_API_KEY is not set")
        if not 1 <= len(questions) <= 8:
            raise KairosError("invalid_request", f"Kairos V4 accepts 1-8 questions per request, got {len(questions)}")
        body: dict[str, Any] = {"model": self.model, "state": state, "questions": questions}
        if task_profile:
            body["task_profile"] = task_profile
        payload = json.dumps(body, separators=(",", ":"), allow_nan=False)
        if len(payload.encode()) > 1024 * 1024:
            raise KairosError("payload_too_large", "request exceeds the 1 MiB gateway limit")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        attempt = 0
        t0 = time.perf_counter()
        while True:
            attempt += 1
            try:
                async with self.session.post(self.url, data=payload, headers=headers, timeout=self.timeout) as r:
                    text = await r.text()
                    try:
                        data = json.loads(text) if text else {}
                    except json.JSONDecodeError:
                        data = text
                    if r.status != 200:
                        err = _error_from(r.status, data)
                        if err.retryable and attempt <= self.max_retries:
                            retry_after = r.headers.get("Retry-After")
                            delay = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else 2.0 * attempt
                            await asyncio.sleep(min(delay, 30))
                            continue
                        raise err
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt <= self.max_retries:
                    await asyncio.sleep(2.0 * attempt)
                    continue
                raise KairosError("transport", repr(exc)) from exc
            break

        latency = (time.perf_counter() - t0) * 1000
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            raise KairosError("malformed_response", f"response has no answers object: {str(data)[:300]}")
        meta = {k: data.get(k) for k in ("model", "model_version", "prompt_version", "calibration_version", "task_profile", "request_id", "timings_ms") if k in data}
        if isinstance(data.get("metadata"), dict):
            meta.update(data["metadata"])
        return KairosResult(
            answers=data["answers"], usage=data.get("usage") or {}, metadata=meta,
            latency_ms=latency, attempts=attempt, request_bytes=len(payload.encode()), raw=data,
        )
