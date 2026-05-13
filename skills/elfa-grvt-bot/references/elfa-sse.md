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

## Canonical event payload

Per `auto/notifications` the SSE frame for a trigger looks like:

```
event: query.triggered
id: evt_01J...
data: {"version":"1.0","eventType":"query.triggered","eventId":"evt_01J...","timestamp":"2026-04-01T12:00:00.000Z","queryId":"q_123","channel":"sse","trigger":{"symbol":"BTC","reason":"price > threshold"},"evaluation":{"triggered":true},"action":{"type":"notify"}}
```

Top-level JSON fields:

- `version` (e.g. `"1.0"`)
- `eventType` (`"query.triggered"`)
- `eventId` (`"evt_01J..."`) -- **canonical idempotency key**
- `timestamp` (ISO 8601)
- `queryId`
- `channel` (`"sse"` here; `"webhook"` for the webhook channel; etc.)
- `trigger` (per-condition payload, e.g. `{"symbol":"BTC","reason":"..."}`)
- `evaluation` (`{"triggered":true}`)
- `action` (`{"type":"notify"}` for notify-style queries)

The SSE protocol `id:` line carries the same `eventId` value per the
published example. The bot prefers `data.eventId` (the canonical field)
and falls back to the SSE `id:` line defensively if the payload is missing
or malformed.

## Dedupe key

`eventId` is the idempotency primitive across delivery channels per the
docs ("Deduplicate by `eventId`", `auto/notifications`). The bot uses it
as the primary key in the local `fires` table.

Note: this is a different identifier namespace from `executions[i].id`
returned by `GET /v2/auto/queries/:id` (poll-query). Those are internal
Athena execution records (`exec_xxx`), not Auto event IDs. The bot does
**not** dedupe SSE fires against poll-query executions for that reason --
see the next section.

## Poll-query (`GET /v2/auto/queries/:id`)

Used for status reconciliation only -- never for replaying fires through
the order-placement path.

Response shape (per `api/rest/auto-poll-query-v-2`):

```json
{
  "queryId": "q_123",
  "status": "active",
  "latestEvaluation": {
    "evaluatedAt": "2026-04-01T12:00:00.000Z",
    "wouldTriggerNow": false
  },
  "executions": [
    {
      "id": "exec_xxx",
      "queryId": "q_123",
      "type": "notify",
      "status": "success",
      "createdAt": "2026-04-01T12:00:01.000Z"
    }
  ]
}
```

The bot calls this on startup and after each SSE disconnect to learn the
authoritative remote status. If the remote status is terminal AND the
local strategy is still `active`, the bot syncs the local status and
emits an alert:

- `triggered` + executions while we were offline -> `manual_intervention_required`
  (we cannot safely replay because `executions[i].id` is not the same
  namespace as SSE `eventId`; the user reviews the GRVT side manually)
- `expired` -> `strategy_terminated_remotely`, severity `info`
- `cancelled` / `failed` -> `strategy_terminated_remotely`, severity `warning`

## Query lifecycle states

Documented Auto status set (from `auto/agent-quickstart`, `v-2-auto.tag`):

- **Live**: `active`, `recurring`
- **Terminal**: `triggered`, `expired`, `cancelled`, `failed`

The supervisor treats live statuses as "keep SSE open"; terminal
statuses cause the per-strategy task to exit cleanly after status sync.

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
