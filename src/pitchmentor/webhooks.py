from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Any, Mapping


class WebhookVerificationError(Exception):
    pass


def verify_webhook(
    secret: str,
    headers: Mapping[str, str],
    body: str | bytes,
    *,
    tolerance: int = 300,
    now: float | None = None,
) -> dict[str, Any]:
    """Verifies a Pitch Mentor webhook (Standard Webhooks) and returns the parsed event.

    Pass the raw request body exactly as received, and the request headers (any mapping;
    lookups are case-insensitive). Raises WebhookVerificationError.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    msg_id, timestamp, signatures = lower.get("webhook-id"), lower.get("webhook-timestamp"), lower.get("webhook-signature")
    if not msg_id or not timestamp or not signatures:
        raise WebhookVerificationError("Missing webhook-id, webhook-timestamp or webhook-signature header")
    try:
        ts = int(timestamp)
    except ValueError:
        raise WebhookVerificationError("Invalid webhook-timestamp") from None
    if abs((time.time() if now is None else now) - ts) > tolerance:
        raise WebhookVerificationError("Webhook timestamp is outside the tolerance window")

    payload = body.encode() if isinstance(body, str) else body
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except binascii.Error:
        raise WebhookVerificationError("Invalid webhook secret") from None
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{timestamp}.".encode() + payload, hashlib.sha256).digest()).decode()
    for part in signatures.split(" "):
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig.encode(), expected.encode()):
            try:
                event: dict[str, Any] = json.loads(payload)
            except ValueError:
                raise WebhookVerificationError("Webhook body is not valid JSON") from None
            return event
    raise WebhookVerificationError("No matching webhook signature")
