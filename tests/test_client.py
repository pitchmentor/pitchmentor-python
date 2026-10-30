import base64
import json
import unittest
from collections import deque
from typing import Any

import requests
from requests.adapters import BaseAdapter

from pitchmentor import (
    APIConnectionError,
    NotFoundError,
    PitchMentor,
    RateLimitError,
    WebhookVerificationError,
    verify_webhook,
)


class FakeAdapter(BaseAdapter):
    """Records requests and replays queued (status, body, headers) responses or exceptions."""

    def __init__(self, *responses: Any) -> None:
        super().__init__()
        self.queue = deque(responses)
        self.requests: list[requests.PreparedRequest] = []

    def send(self, request, **kwargs):  # type: ignore[no-untyped-def,override]
        self.requests.append(request)
        nxt = self.queue.popleft()
        if isinstance(nxt, Exception):
            raise nxt
        status, body, headers = (*nxt, {})[:3]
        res = requests.Response()
        res.status_code = status
        res.headers.update(headers)
        if isinstance(body, str):
            res._content = body.encode()
        else:
            res._content = json.dumps(body).encode()
            res.headers.setdefault("content-type", "application/json")
        res.request = request
        res.url = request.url
        return res

    def close(self) -> None:
        pass


def client(adapter: FakeAdapter, **kw: Any) -> PitchMentor:
    s = requests.Session()
    s.mount("http://", adapter)
    pm = PitchMentor("pm_live_test", base_url="http://api.test/", session=s, **kw)
    pm._sleep = lambda _s: None
    return pm


PAGE_END = {"data": [], "has_more": False, "next_cursor": None}


class RequestBuilding(unittest.TestCase):
    def test_get_query_and_path(self) -> None:
        a = FakeAdapter((200, PAGE_END))
        client(a).list_webhook_deliveries("we/1", status="failed", limit=5)
        r = a.requests[0]
        self.assertEqual(r.method, "GET")
        self.assertEqual(r.url, "http://api.test/v1/webhooks/we%2F1/deliveries?limit=5&status=failed")
        self.assertEqual(r.headers["Authorization"], "Bearer pm_live_test")
        self.assertNotIn("Idempotency-Key", r.headers)

    def test_bool_and_keyword_params(self) -> None:
        a = FakeAdapter((200, PAGE_END))
        client(a).list_calls(archived=True, from_="2026-01-01")
        self.assertIn("archived=true", a.requests[0].url)
        self.assertIn("from=2026-01-01", a.requests[0].url)

    def test_post_body_and_idempotency(self) -> None:
        a = FakeAdapter((201, {"lead": {"id": "l1"}, "action": "created"}), (200, {"id": "c1", "archivedAt": None}))
        pm = client(a)
        self.assertEqual(pm.upsert_lead({"name": "Ada"})["lead"]["id"], "l1")
        self.assertEqual(json.loads(a.requests[0].body), {"name": "Ada"})
        self.assertEqual(a.requests[0].headers["Content-Type"], "application/json")
        self.assertEqual(len(a.requests[0].headers["Idempotency-Key"]), 36)
        pm.archive_call("c1", idempotency_key="mine")
        self.assertEqual(a.requests[1].headers["Idempotency-Key"], "mine")
        self.assertNotIn("Content-Type", a.requests[1].headers)


class Pagination(unittest.TestCase):
    def test_iter_follows_cursor(self) -> None:
        a = FakeAdapter(
            (200, {"data": [{"id": "a"}, {"id": "b"}], "has_more": True, "next_cursor": "b"}),
            (200, {"data": [{"id": "c"}], "has_more": False, "next_cursor": None}),
        )
        self.assertEqual([c["id"] for c in client(a).list_calls_iter(limit=2)], ["a", "b", "c"])
        self.assertNotIn("cursor", a.requests[0].url)
        self.assertIn("cursor=b", a.requests[1].url)


class Retries(unittest.TestCase):
    def test_retries_429_and_5xx_with_same_key(self) -> None:
        a = FakeAdapter(
            (429, {"error": {"code": "rate_limited", "message": "slow"}}, {"retry-after": "3"}),
            (502, "bad gateway"),
            (200, {"id": "c1", "archivedAt": "x"}),
        )
        pm = client(a)
        slept: list[float] = []
        pm._sleep = slept.append
        pm.archive_call("c1")
        self.assertEqual(len(a.requests), 3)
        self.assertEqual(slept[0], 3.0)
        self.assertEqual(len({r.headers["Idempotency-Key"] for r in a.requests}), 1)

    def test_gives_up_with_rate_limit_error(self) -> None:
        a = FakeAdapter((429, {"error": {"code": "rate_limited", "message": "slow"}}, {"retry-after": "7"}))
        with self.assertRaises(RateLimitError) as ctx:
            client(a, max_retries=0).get_me()
        self.assertEqual(ctx.exception.retry_after, 7.0)
        self.assertEqual(ctx.exception.code, "rate_limited")

    def test_connection_errors(self) -> None:
        a = FakeAdapter(requests.ConnectionError("boom"), requests.ConnectionError("boom"))
        with self.assertRaises(APIConnectionError):
            client(a, max_retries=1).get_me()
        self.assertEqual(len(a.requests), 2)


class Errors(unittest.TestCase):
    def test_typed_error_no_retry(self) -> None:
        a = FakeAdapter((404, {"error": {"code": "not_found", "message": "Call not found"}}))
        with self.assertRaises(NotFoundError) as ctx:
            client(a).get_call("nope")
        self.assertEqual((ctx.exception.status, ctx.exception.code, str(ctx.exception)), (404, "not_found", "Call not found"))
        self.assertEqual(len(a.requests), 1)

    def test_csv_export_is_text(self) -> None:
        a = FakeAdapter((200, "id\nc1\n", {"content-type": "text/csv"}))
        self.assertEqual(client(a).export_calls(), "id\nc1\n")


# Known-good vector from the backend's test (= Standard Webhooks reference vector)
SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
MSG_ID, TS, BODY = "msg_p5jXN8AQM9LWM0D4loKWxJek", 1614265330, '{"test": 2432232314}'
SIG = "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="


def headers(**over: str) -> dict[str, str]:
    h = {"webhook-id": MSG_ID, "webhook-timestamp": str(TS), "webhook-signature": SIG}
    h.update({k.replace("_", "-"): v for k, v in over.items()})
    return h


class Webhooks(unittest.TestCase):
    def test_known_good_vector(self) -> None:
        self.assertEqual(verify_webhook(SECRET, headers(), BODY, now=TS), {"test": 2432232314})
        self.assertEqual(verify_webhook(SECRET, headers(webhook_signature=f"v1,bogus {SIG}"), BODY.encode(), now=TS + 5)["test"], 2432232314)
        self.assertEqual(verify_webhook(SECRET, {k.title(): v for k, v in headers().items()}, BODY, now=TS)["test"], 2432232314)

    def test_rejects(self) -> None:
        other = "whsec_" + base64.b64encode(b"another secret key").decode()
        for secret, h, body, now in [
            (SECRET, headers(), BODY + " ", TS),
            (other, headers(), BODY, TS),
            (SECRET, headers(webhook_id="msg_2"), BODY, TS),
            (SECRET, headers(), BODY, TS + 301),
            (SECRET, {"webhook-id": MSG_ID}, BODY, TS),
        ]:
            with self.assertRaises(WebhookVerificationError):
                verify_webhook(secret, h, body, now=now)


if __name__ == "__main__":
    unittest.main()
