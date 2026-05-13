# Elfa Auto SSE delivery

Wire format and lifecycle for the per-query notification stream the bot
consumes. Cross-checked against `docs.elfa.ai` (canonical):
`/auto/notifications` and `/api/rest/auto-stream-query-v-2`.

## SSE stream

- Endpoint: `GET https://api.elfa.ai/v2/auto/queries/:id/stream`
- Auth: `x-elfa-api-key: <ELFA_API_KEY>` (HMAC is not required for the
  stream itself; HMAC only applies to trade-flavoured mutations).
- Required client header: `Accept: text/event-stream`

Documented response statuses:

| Code | Meaning |
|------|---------|
| 200  | SSE stream established (text/event-stream) |
| 204  | No content |
| 401  | Missing or invalid API key |
| 404  | Query not found |
| 410  | Query stream closed (already terminal on connect) |

A 410 on connect means the query was already in a terminal status when the
request arrived. The bot's `_strategy_loop` then falls back to the
poll-query endpoint for status reconciliation.

## Production event payload

Captured 2026-05-13 against `api.elfa.ai` (see
`references/captured-frames/notification_*_2026-05-13.txt` for the
exact bytes). The actual wire format is flatter than the canonical
envelope `docs.elfa.ai/auto/notifications` describes, and the
canonical envelope fields (`version`, `eventType`, `eventId`,
`channel`, `trigger`, `evaluation`, `action`) are NOT emitted today.
The parser is locked to production, not to the spec.

```
event: notification
id: <sse-level uuid>
data: {"status":"triggered","queryId":"<uuid>","executionId":"<uuid>","triggerTime":"2026-...Z","timestamp":<epoch_ms>,"title":"Auto Plan Alert","body":"...","message":"...","conditionsMet":<int>, ...}
```

Required JSON fields the parser checks for (any missing -> drop with WARNING):

- `status` (must equal `"triggered"`)
- `queryId` (must match the stream URL's query id)
- `executionId` -- **canonical idempotency key**
- `triggerTime` (ISO 8601)

Informational fields (passed through, not required):

- `timestamp` (epoch ms, mirrors `triggerTime`)
- `title`, `body`, `message` -- human-readable strings from the
  notify action template Builder Chat emits
- `queryTitle`, `queryDisplayTitle`, `queryIdShort`, `autoDetails`
- `conditionsMet` (integer count, `1` for `cron.once`,
  `price.current`, etc. observed in production)

The SSE protocol `id:` line carries an SSE-level UUID that is NOT
the same as `executionId`. The bot keys idempotency on `executionId`
from the `data:` payload; the `id:` line is ignored.

If Elfa rolls out the documented canonical envelope (`event:
query.triggered` with `eventId`/`channel`/`trigger`/etc.), the parser
accepts both `event: notification` and `event: query.triggered`. New
fields are tolerated. Adding new required fields to the schema is the
breaking case; re-capture and update the parser when that happens.

## Dedupe key

`executionId` is the canonical idempotency primitive. Same UUID
namespace as `executions[i].id` from `GET /v2/auto/queries/:id`
(verified against production 2026-05-13), so it is safe to dedupe
across SSE delivery and poll-query reconciliation. The bot uses it as
the primary key in the local `fires` table.

## Poll-query (`GET /v2/auto/queries/:id`)

Used for status reconciliation and as the secondary observation
channel for fires that arrived while the receiver was offline.

Response shape:

```json
{
  "queryId": "q_123",
  "status": "active",
  "latestEvaluation": {
    "evaluatedAt": "2026-05-13T06:53:25.000Z",
    "wouldTriggerNow": false
  },
  "executions": [
    {
      "id": "94631fa0-05db-482a-9040-cfbaf13ece71",
      "queryId": "q_123",
      "type": "notification",
      "status": "success",
      "createdAt": "2026-05-13T06:53:25.405Z"
    }
  ]
}
```

`executions[i].id` is the same UUID namespace as the SSE payload's
`executionId` (verified production 2026-05-13). The bot can therefore
dedupe SSE fires against poll-query executions safely if needed.

The bot calls this on startup and after each SSE disconnect to learn
the authoritative remote status. If the remote status is terminal AND
the local strategy is still `active`:

- `triggered` + at least one execution while we were offline
  -> `manual_intervention_required` (the trigger may be stale by the
  time the receiver reconnects and prices have moved; the user
  reviews the GRVT side manually)
- `triggered` + the execution was already processed live via SSE
  -> alert suppressed (dedupe by `executionId`)
- `expired` -> `strategy_terminated_remotely`, severity `info`
- `cancelled` -> `strategy_terminated_remotely`, severity `warning`
- `failed` -> `strategy_terminated_remotely`, severity `error`

## Query lifecycle states

Documented Auto status set (from `auto/agent-quickstart`, `v-2-auto.tag`):

- **Live**: `active`
- **Unsupported by this bot**: `recurring` (documented live status, rejected locally)
- **Terminal**: `triggered`, `expired`, `cancelled`, `failed`

The supervisor treats `active` as "keep SSE open". Terminal statuses cause
the per-strategy task to exit cleanly after status sync. `recurring` is
treated as unsupported and mapped to local `failed` because this bot is
single-fire only.

## Reconnect semantics

Each strategy runs in its own asyncio task managed by the supervisor.
Per-iteration: poll-query for status -> open SSE -> consume frames until
stream closes -> repeat.

On transient errors (network blips, 5xx), the task backs off exponentially
(starting at 2 s, capped at 60 s) before retrying. It stops retrying when:

- Poll-query reports a terminal status -- the local status is synced and
  the task exits cleanly.
- The supervisor cancels the task because the local registry no longer
  lists the strategy as `active`.

The supervisor reconciles every ~5 s, so newly-added strategies are
picked up without a restart and locally-cancelled strategies are torn
down promptly.

## Why SSE instead of webhooks

Webhook delivery required a public HTTPS endpoint: a cloudflared tunnel
for development, a PaaS host with a stable hostname for production. SSE
flips the direction -- the bot makes outbound HTTPS to Elfa, so it works
behind NAT, on a laptop, or in a Docker container with no port mapping.
The fire-handler logic is unchanged; only the delivery layer differs.

## Security

Trigger delivery is authenticated by the bot's own `x-elfa-api-key`
credentials on the outbound connection. The previous webhook channel was
unsigned -- anyone who discovered the public URL could POST a fake fire.
With outbound SSE, the trigger source is Elfa itself and the key is held
by the bot. The per-strategy `max_notional_usd` cap remains as a
real-money safety primitive, but the "unauthenticated remote trigger"
risk class is eliminated.
