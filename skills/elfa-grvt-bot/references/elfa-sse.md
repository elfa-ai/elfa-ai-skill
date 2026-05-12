# Elfa Auto SSE delivery

Elfa Auto exposes a per-query Server-Sent Events stream and a REST backfill
endpoint. The bot uses both: SSE for live triggers, REST for offline recovery
after a restart or downtime window.

## SSE stream

- Endpoint: `GET https://api.elfa.ai/v2/auto/queries/:id/stream`
- Auth: `x-elfa-api-key: <ELFA_API_KEY>`
- Required client header: `Accept: text/event-stream`

The connection stays open until one of two things happens:

1. The query conditions evaluate true -- Elfa emits one `event: notification`,
   then an `event: end`, then closes the connection.
2. The strategy enters a terminal status (`triggered`, `expired`, `cancelled`)
   on Elfa's side -- the connection closes without a notification frame.

A 410 response on connect means the query was already terminal when the
request arrived; the client should call `get_query` to backfill and move on.

Example notification frame:

```
event: notification
data: {"executionId":"...","queryId":"...","firedAt":"...","triggerData":{...}}

```

(The blank line is the standard SSE record terminator.)

Key fields in the JSON payload:

- `executionId` -- unique per fire; used as the dedupe key (`fires.executionId` PK).
- `queryId` -- the strategy that fired.
- `firedAt` -- ISO 8601 timestamp.
- `triggerData` -- opaque payload from Auto describing what fired (which leg of
  an AND, current value vs threshold, etc.). The bot stores this in the registry
  but does not parse it.

## REST backfill

- Endpoint: `GET https://api.elfa.ai/v2/auto/queries/:id`
- Auth: `x-elfa-api-key`
- Returns: the query record including `executions` -- a list of past fires with
  `id` (the execution id), `createdAt`, and associated trigger data.

The receiver calls this on startup for every locally-active strategy. It also
calls it on every SSE reconnect loop before re-opening the stream. Any
execution whose `executionId` is not in the local `fires` table gets replayed
through the same `_process_fire` handler used for live SSE notifications, so
the bot recovers cleanly from a restart or an unattended downtime window.

REST backfill fires are tagged `"source": "rest_backfill"` in the synthetic
payload so they are distinguishable in logs and the registry.

## Reconnect semantics

Each strategy runs in its own asyncio task managed by the supervisor. The loop
is: REST status check -> open SSE -> wait for stream close -> repeat.

On transient errors (network blips, 5xx), the task backs off exponentially
(starting at 2 s, capped at 60 s) before retrying. It stops retrying when:

- The REST check returns a non-`active` status -- backfill is replayed and the
  task exits cleanly.
- The supervisor cancels the task because the local registry no longer lists
  the strategy as `active`.

The supervisor reconciles every ~5 s, so newly-added strategies are picked up
without a restart and strategies cancelled via the CLI are torn down promptly.

## Why SSE instead of webhooks

Webhook delivery required a public HTTPS endpoint: a cloudflared tunnel for
development, a PaaS host with a stable hostname for production. SSE flips the
direction -- the bot makes outbound HTTPS to Elfa, so it works behind NAT, on
a laptop, or in a Docker container with no port mapping. The fire-handler logic
is unchanged; only the delivery layer differs.

## Security

Trigger delivery is now authenticated by the bot's own `x-elfa-api-key`
credentials on the outbound connection. The previous webhook channel was
unsigned -- anyone who discovered the public URL could POST a fake fire. With
outbound SSE, the trigger source is Elfa itself and the key is held by the bot.
The per-strategy `max_notional_usd` cap remains as a real-money safety
primitive, but the "unauthenticated remote trigger" risk class is eliminated.
