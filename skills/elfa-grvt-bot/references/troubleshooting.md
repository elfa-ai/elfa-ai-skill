# Troubleshooting

Common errors and what they mean, organized by where they surface.

## During strategy authoring (agent session, hitting Elfa)

### `elfa create_query failed: 401 Unauthorized`

`ELFA_API_KEY` is wrong, expired, or missing. Re-copy from the Elfa developer portal (no leading/trailing whitespace). Note that historical "Invalid HMAC signature" errors no longer apply: as of 2026-05-08, Elfa accepts API-key auth alone.

### `elfa cancel_query failed: 409 Query must be cancelled before deletion`

You should never see this from the project code, since `ElfaClient.cancel_query` calls `POST /v2/auto/queries/:id/cancel` (not DELETE). If you wrote a custom call to `DELETE /v2/auto/queries/:id`, switch to the cancel endpoint; deletion is allowed only after cancel.

### `validate_query` returns `valid: false`

Read the `errors` array. Common issues:
- Period below the documented minimum for `cron`/`llm` (1h)
- Missing required arg on a TA method (`ema`/`sma` need `period`)
- `period` passed as string instead of number
- Symbol not supported (try `GET /v2/auto/validate-symbol/<SYMBOL>` first)

### `wouldTriggerNow: true` when you didn't expect it

The state-form operator (`<`, `>`) fires whenever the condition is true. If the current value is already past the threshold, the strategy fires on the next eval (often within seconds).

For "X dips below threshold" semantics, use `crosses_below`. It only fires on transition.

## Receiver process died / not running

Check whether the receiver is up:

```bash
pgrep -f elfa_grvt_bot
```

If nothing is returned, the process is not running. Restart it:

```bash
source .venv/bin/activate
python -m elfa_grvt_bot
```

On restart the supervisor calls `GET /v2/auto/queries/:id` (poll-query) for each registered active strategy to reconcile status. If a strategy already triggered while the receiver was offline, the bot does NOT replay it as a fire (SSE `eventId` and poll-query `executions[i].id` are different identifier namespaces per the docs -- cross-channel dedupe is unsafe). Instead it emits a `manual_intervention_required` alert so you review the GRVT side manually. To minimize this window, run the receiver under systemd or a PaaS auto-restarter.

## On SSE stream connection (in receiver logs)

### `401 Unauthorized` on stream open

`ELFA_API_KEY` is wrong or expired. The `stream_notifications` call raises a `RuntimeError` with the status code. Rotate the key in `.env` and restart.

### `404 Not Found` on stream open

The query no longer exists on Elfa's side (was hard-deleted). The supervisor will observe a non-`active` status on the next REST check and stop the per-strategy task automatically. No action required unless the strategy should be replaced.

### Stream closes without a notification event

Normal behavior. A query stream emits at most one `notification` event (the trigger), then `event: end`, and the server closes the connection. If the condition hasn't fired yet, the stream may stay open for a long time and then close on the server's schedule. The supervisor re-opens it on the next reconcile loop.

### Stream disconnects with a network error

Expected on flaky networks. The per-strategy `_strategy_loop` catches `httpx.HTTPError` and `ConnectionError`, logs a warning, and retries with exponential backoff (initial 2s, max 60s). During the gap, any fires that landed are picked up by the REST backfill on reconnect.

### Nothing in the logs after "supervisor started"

The supervisor found no active strategies to spawn tasks for. Check that at least one strategy is registered and active:

```bash
python src/registry_cli.py list --status active
```

If the output is empty, no strategies have been registered yet, or all were previously cancelled/fired.

## Fires that landed while the receiver was offline

The bot does NOT auto-replay offline fires. SSE notifications carry an `eventId` (`evt_xxx`); poll-query `executions[i].id` (`exec_xxx`) is a different identifier namespace per the documented schemas (`auto/notifications` vs `api/rest/auto-poll-query-v-2`). Replaying poll-query executions through the order path would risk double-placing trades when both channels later catch up.

Instead: on supervisor startup, poll-query reconciles status. If the remote query is in a terminal state AND executions are present, the supervisor emits a `manual_intervention_required` alert listing the strategy. Review the GRVT side and decide whether to enter manually.

To keep the offline window minimal, run the receiver under systemd / a PaaS auto-restarter.

## In Telegram alerts

### `manual_intervention_required`

Two main paths produce this alert:

1. **Entry placed but TP/SL setup partially or fully failed.** The position is open without a complete protective bracket. Read the alert details for which leg failed and intended TP/SL prices. Manually place the missing leg via GRVT UI.

2. **Registry write failed AFTER successful place_order.** The order is on GRVT but the local registry could not record it. Manually update the registry: mark the strategy `fired` and the fire `placed` with the GRVT order_id from the alert.

### `unknown_strategy`

A fire was received for a `queryId` that has no matching row in the registry. Causes:
- Auto query was registered but the local registry write failed
- Local DB was reset / migrated and lost the row

Look up the `queryId` on Elfa's side via `GET /v2/auto/queries/:id`. If the strategy is something you want to honor, recreate the registry row manually with `registry_cli.py add`.

### `guardrail_rejected`

Receiver explicitly rejected the fire. Reasons in the message:
- Strategy `env` does not match receiver `GRVT_ENV`: misconfigured receiver or stale strategy.
- Notional exceeds `max_notional_usd` cap: price moved between strategy creation and fire; either accept and recreate with a larger cap, or skip.

(Note: there is no symbol allowlist. If a strategy somehow points at a symbol GRVT doesn't list, you'll see a `grvt_other` alert from `fetch_mid_price` instead of `guardrail_rejected`.)

### `grvt_set_leverage`

Set-leverage call failed for a non-deprecated reason (auth, network). Check GRVT auth.

If the failure message contains `deprecated` or `code=2106`, it is the known deprecated `set_initial_leverage` API. The receiver should have logged a warning and proceeded; this alert means the deprecated-detection logic missed something. File a bug.

### `insufficient_margin`

Order rejected by GRVT for lack of margin. Either fund the account or reduce strategy size / leverage.

### `grvt_other`

Catch-all GRVT error. Read the message. If it is a known pattern (geo-block, rate-limit, instrument suspended), document and add specific handling.

## At the GRVT order placement layer

See `references/grvt-api.md` for the full quirks list. Quick map of error codes:

| Code | Meaning |
|---|---|
| 2002 | Signature does not match payload (most often non-ASCII or non-tick-aligned price) |
| 2020 | Market order with limit price (set leg `limit_price="0"` for market) |
| 2066 | Order below min notional (usually $5) |
| 2106 | API deprecated (set_initial_leverage; no action, leverage is account-level) |
| 2117 | Position-linked TPSL only via web/mobile (use OCO via bulk_orders instead) |
| 2402 | Reduce-only size exceeds position size (use OCO bulk_orders, or smaller sizes) |

## If everything is on fire

```bash
# 1. Cancel all active strategies on Elfa side
source .venv/bin/activate; set -a; source .env; set +a
python -c "
import os, requests
api_key = os.environ['ELFA_API_KEY']
# GET active queries, then POST /v2/auto/queries/:id/cancel for each
# (api-key header auth: 'x-elfa-api-key': api_key)
"

# 2. Mark everything cancelled locally
sqlite3 registry.db "UPDATE strategies SET status='cancelled' WHERE status='active';"

# 3. Stop the receiver
pkill -f "elfa_grvt_bot"

# 4. Manually close any open GRVT positions via the UI
```

This is a panic stop, not a graceful shutdown. Use only when something is wrong and you need to halt all firing immediately.
