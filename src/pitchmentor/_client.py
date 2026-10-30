from __future__ import annotations

import json
import os
import random
import time
import uuid
from typing import Any, Callable, Iterator, TypeVar

import requests

from ._generated import Operations, Page
from .errors import APIConnectionError, error_from_response, parse_retry_after

__version__ = "0.1.1"
DEFAULT_BASE_URL = "https://api.pitchmentor.net"
_MAX_RETRY_DELAY = 60.0

T = TypeVar("T")


class PitchMentor(Operations):
    """Pitch Mentor API client.

    ``api_key`` defaults to ``PITCHMENTOR_API_KEY``; ``base_url`` to ``PITCHMENTOR_BASE_URL``,
    else https://api.pitchmentor.net. 429, 5xx and network errors are retried ``max_retries``
    times, honoring ``retry-after``. POSTs get an ``Idempotency-Key`` (generated unless given)
    that is reused across retries.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        max_retries: int = 2,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        api_key = api_key or os.environ.get("PITCHMENTOR_API_KEY")
        if not api_key:
            raise ValueError("Missing API key: pass api_key or set PITCHMENTOR_API_KEY")
        self._api_key = api_key
        self.base_url = (base_url or os.environ.get("PITCHMENTOR_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout
        self._session = session or requests.Session()
        self._sleep: Callable[[float], None] = time.sleep

    def __enter__(self) -> PitchMentor:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Sends any request (for endpoints newer than this SDK). JSON is parsed; other responses return text."""
        params = {k: _query_value(v) for k, v in (query or {}).items() if v is not None}
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json", "User-Agent": f"pitchmentor-python/{__version__}"}
        data = None
        if body is not None:
            # Fastify rejects an empty body with a JSON content-type, so only set it when there is a body
            headers["Content-Type"] = "application/json"
            data = json.dumps(body)
        if method == "POST":
            headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())

        attempt = 0
        while True:
            try:
                res = self._session.request(method, self.base_url + path, params=params, data=data, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt < self.max_retries:
                    self._sleep(_backoff(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(f"{method} {path} failed: {exc}") from exc

            if (res.status_code == 429 or res.status_code >= 500) and attempt < self.max_retries:
                retry_after = parse_retry_after(res.headers.get("retry-after"))
                self._sleep(min(retry_after, _MAX_RETRY_DELAY) if retry_after is not None else _backoff(attempt))
                attempt += 1
                continue

            parsed: Any = res.text
            if "json" in res.headers.get("content-type", "") and res.text:
                try:
                    parsed = res.json()
                except ValueError:
                    pass
            if not res.ok:
                raise error_from_response(res.status_code, res.headers, parsed)
            return parsed

    _request = request

    def _paginate(self, fetch_page: Callable[[str | None], Page[T]]) -> Iterator[T]:
        cursor: str | None = None
        while True:
            page = fetch_page(cursor)
            yield from page["data"]
            cursor = page["next_cursor"] if page["has_more"] else None
            if not cursor:
                return


def _query_value(value: Any) -> Any:
    return ("true" if value else "false") if isinstance(value, bool) else value


def _backoff(attempt: int) -> float:
    """~0.5s, 1s, 2s … capped at 8s, with jitter."""
    return float(min(8.0, 0.5 * 2**attempt) * random.uniform(0.75, 1.25))
