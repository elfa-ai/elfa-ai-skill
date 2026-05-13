# Captured SSE frames

Raw byte captures of the Elfa Auto SSE stream against `api.elfa.ai`,
one file per condition source. Each file is the literal newline-joined
stream output from a single triggered fire.

These exist so the parser is locked to **production reality**, not to
docs.elfa.ai's spec interpretation. When Elfa drifts the schema, tests
break and the fix is one re-capture, not an investigation.

To regenerate, run `scripts/capture_frame.py <eql-source>` against a
live Elfa API key. Date-stamp the filename: `notification_<source>_<YYYY-MM-DD>.txt`.

| File | Condition source | Captured |
|---|---|---|
| `notification_cron_once_2026-05-13.txt` | `cron.once period=5m` | 2026-05-13 |
| `notification_price_current_2026-05-13.txt` | `price.current(BTC) > 50000` | 2026-05-13 |

Schema (as of 2026-05-13):

```
id: <sse-level uuid>
event: notification
data: {"status":"triggered","queryId":"<uuid>","executionId":"<uuid>","triggerTime":"<iso8601>","timestamp":<epoch_ms>,"title":..,"body":..,"message":..,"conditionsMet":<int>, ...}
```

The `executionId` field is the canonical idempotency key and matches
`executions[i].id` from `GET /v2/auto/queries/:id` (verified against
production 2026-05-13). Use it for cross-channel dedupe.

Documented canonical envelope fields (`version`, `eventType`, `eventId`,
`channel`, `trigger`, `evaluation`, `action`) are NOT emitted by
production today. The parser must not require them.
