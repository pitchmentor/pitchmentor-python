# pitchmentor

Official Python SDK for the [Pitch Mentor](https://pitchmentor.net) API (`https://api.pitchmentor.net/v1`).
Python 3.11+, synchronous, built on `requests`. Responses are plain dicts typed with `TypedDict`s (`Call`, `Lead`,
`Page[Call]`, …), so editors and mypy check them.

API reference: https://pitchmentor.net/docs

## Install

```bash
pip install pitchmentor
```

## Authentication

Create a team API key (`pm_live_…`) in the Pitch Mentor dashboard. It is sent as `Authorization: Bearer pm_live_…`.
The client reads `PITCHMENTOR_API_KEY` (and optionally `PITCHMENTOR_BASE_URL`) from the environment, or takes them as
arguments. Each key has scopes (`calls:read`, `leads:write`, …); every method's docstring names the scope it needs.

```py
from pitchmentor import PitchMentor

pm = PitchMentor()  # or PitchMentor("pm_live_…", base_url=..., max_retries=2, timeout=60.0)
```

The client holds a `requests.Session`; use it as a context manager (`with PitchMentor() as pm:`) or call `pm.close()`.

## Quick start

```py
me = pm.get_me()
detail = pm.get_call("call_id")
overview = pm.get_analytics_overview(period="30d")
per_member = pm.get_analytics_by_member(period="30d")
pm.upsert_lead({"name": "Ada Lovelace", "externalId": "crm-42", "email": "ada@example.com"})
pm.update_team_config({"toneGuidelines": "Friendly, concise."})
team = pm.get_team_config()
billing = pm.get_billing_status()
usage = pm.list_member_usage()
referrals = pm.get_referral_stats()
csv = pm.export_calls(format="csv")
pm.list_calls(from_="2026-01-01", archived=True)  # `from` is `from_` in Python
```

Every API operation is a method named after its `operationId` in snake_case (`list_calls`, `get_call`,
`create_webhook_endpoint`, …). `pm.request(method, path, query=..., body=...)` reaches endpoints newer than the SDK.

## Pagination

List methods return one page: `{"data": [...], "has_more": bool, "next_cursor": str | None}`. Pass `next_cursor` as
`cursor` for the next page, or use the `…_iter` method, which walks every page:

```py
page = pm.list_calls(period="7d", limit=50)
for call in pm.list_calls_iter(period="7d"):
    print(call["id"], call["prospectName"])
```

Paginated: `list_calls`, `list_leads`, `list_api_keys`, `list_webhook_endpoints`, `list_webhook_deliveries`.

## Retries, timeouts and idempotency

429, 5xx and connection errors are retried (`max_retries`, default 2) with exponential backoff, honoring `retry-after`.
Every POST carries an `Idempotency-Key`, generated unless you pass one and reused across retries, so a retried POST never
runs twice. Pass your own key to make a POST safe to repeat across processes:

```py
pm.upsert_lead({"name": "Ada", "externalId": "crm-42"}, idempotency_key="import-42")
```

## Errors

Non-2xx responses raise a `PitchMentorError` subclass with `status`, `code` (machine-readable, e.g. `not_found`,
`insufficient_scope`), `message`, `headers` and `body`: `BadRequestError` (400), `AuthenticationError` (401),
`PermissionDeniedError` (403), `NotFoundError` (404), `ConflictError` (409), `UnprocessableEntityError` (422),
`RateLimitError` (429, has `retry_after` seconds), `InternalServerError` (5xx). A request that never got a response
raises `APIConnectionError`.

```py
from pitchmentor import NotFoundError, PitchMentorError

try:
    pm.get_call("missing")
except NotFoundError:
    print("no such call")
except PitchMentorError as e:
    print(e.status, e.code, e.message)
```

## Webhooks

Webhooks are signed with [Standard Webhooks](https://www.standardwebhooks.com/) (`webhook-id`, `webhook-timestamp`,
`webhook-signature`). `verify_webhook` checks the signature against your endpoint's `whsec_…` secret, rejects timestamps
older than 5 minutes (`tolerance=300`), and returns the parsed event. Always pass the raw request body.

```py
import os

from flask import Flask, request
from pitchmentor import verify_webhook, WebhookVerificationError

app = Flask(__name__)

@app.post("/hooks/pitchmentor")
def hook():
    try:
        event = verify_webhook(os.environ["PITCHMENTOR_WEBHOOK_SECRET"], request.headers, request.get_data())
    except WebhookVerificationError:
        return "", 400
    if event["type"] == "call.completed":
        print(event["data"]["id"])
    return "", 204
```

## License

MIT
