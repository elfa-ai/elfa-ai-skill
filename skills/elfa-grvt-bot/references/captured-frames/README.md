# Captured SSE frames

Raw byte captures of the Elfa Auto SSE stream against `api.elfa.ai`,
one file per condition source per observed schema variant. Each file
is the literal newline-joined stream output for the documented event.

These exist so the parser is locked to **observed production reality**,
not to a single doc source's interpretation. Whenever Elfa changes the
SSE wire format, capture a fresh frame, date-stamp it, and add it here.
The parser must continue to accept ALL captured-frames variants so a
partial Elfa rollback does not break the bot.

To regenerate, follow the procedure in `04-algorithms.md` section
"Capturing SSE bytes". Date-stamp the filename:
`notification_<source>_<YYYY-MM-DD>.txt` for trigger events;
`stream_close_<YYYY-MM-DD>.txt` for close frames.

## Captured fixtures

| File | Frame type | Captured | Schema variant |
|---|---|---|---|
| `notification_cron_once_2026-05-13.txt` | Trigger | 2026-05-13 | `event: notification`, flat fields, `executionId` (UUID) |
| `notification_price_current_2026-05-13.txt` | Trigger | 2026-05-13 | `event: notification`, flat fields, `executionId` (UUID) |
| `stream_close_2026-05-14.txt` | Stream close | 2026-05-14 | `event: end`, payload `{code, status, queryId}` |

The 2026-05-13 trigger schema was re-verified against `api.elfa.ai`
on 2026-05-14 (cron.once probe) and is still in effect. The
documented schema (`event: notification:new` per
`docs.elfa.ai/auto/notifications`) has NOT been observed in production.

## Known schema variants (parser dispatches on `event:`)

1. **`event: notification`** (production today, verified 2026-05-14):
   - Top-level: `{status: "triggered", queryId, executionId (UUID),
     triggerTime, timestamp, title, body, message, queryTitle,
     autoDetails, queryIdShort, conditionsMet, queryDisplayTitle}`
   - Dedupe key: `executionId`
   - Fixtures: `notification_cron_once_2026-05-13.txt`,
     `notification_price_current_2026-05-13.txt`
2. **`event: notification:new`** (docs schema, NOT observed in prod):
   - Top-level: `{id (number), type, category, title, body,
     data: {queryId}, priority, createdAt}`
   - Dedupe key: `str(id)`
   - Fixture: none yet. Add one as soon as production starts emitting.
3. **`event: query.triggered`** (older canonical envelope, per a prior
   docs version; presumed gone):
   - Top-level: `{version, eventType, eventId, channel, queryId,
     timestamp, trigger, evaluation, action}`
   - Dedupe key: `eventId`
   - Fixture: none.

## Stream lifecycle events (non-trigger, parser silently skips)

- **`: keep-alive`**: SSE comment line, sent every ~15s. The parser
  treats any line starting with `:` as a no-op (per SSE spec).
- **`event: end`** with `data: {"code": "QUERY_STREAM_CLOSED",
  "status": "triggered", "queryId": "<uuid>"}`: sent by Elfa after a
  fire when it is closing the stream from its side. The parser drops
  it silently (not a trigger). The strategy-loop catches the
  subsequent connection close and falls back to poll-query for
  reconciliation. See `stream_close_2026-05-14.txt`.

## When to add a new fixture

- A new `event:` value appears.
- A previously-required field disappears from production.
- A new required field appears in production.
- A new close-frame variant appears.

Do NOT replace existing fixtures when production rolls forward; **add
new ones** alongside. Production rollovers are often partial or
reversible, and we want the parser to handle either side cleanly.
