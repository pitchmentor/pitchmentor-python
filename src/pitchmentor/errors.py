from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from typing import Any, Mapping


class PitchMentorError(Exception):
    """Any non-2xx response. ``code`` is the API's machine-readable error code."""

    def __init__(self, message: str, *, status: int, code: str, headers: Mapping[str, str], body: Any) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.headers = headers
        self.body = body


class BadRequestError(PitchMentorError): ...


class AuthenticationError(PitchMentorError): ...


class PermissionDeniedError(PitchMentorError): ...


class NotFoundError(PitchMentorError): ...


class ConflictError(PitchMentorError): ...


class UnprocessableEntityError(PitchMentorError): ...


class RateLimitError(PitchMentorError):
    @property
    def retry_after(self) -> float | None:
        """Seconds to wait before retrying, from the ``retry-after`` header."""
        return parse_retry_after(self.headers.get("retry-after"))


class InternalServerError(PitchMentorError): ...


class APIConnectionError(Exception):
    """The request never got an HTTP response (network failure or timeout)."""


_BY_STATUS: dict[int, type[PitchMentorError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: UnprocessableEntityError,
    429: RateLimitError,
}


def error_from_response(status: int, headers: Mapping[str, str], body: Any) -> PitchMentorError:
    # v1 shape is {"error": {"code", "message"}}; tolerate {"error": "message"} and plain text too
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        code, message = str(err.get("code", "error")), str(err.get("message", f"HTTP {status}"))
    else:
        code = "internal_error" if status >= 500 else "error"
        message = err if isinstance(err, str) else (body[:500] if isinstance(body, str) and body else f"HTTP {status}")
    cls = _BY_STATUS.get(status, InternalServerError if status >= 500 else PitchMentorError)
    return cls(message, status=status, code=code, headers=headers, body=body)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None
