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

## On webhook delivery (in receiver logs)

### `400 missing X-Auto-Event-Id`

Elfa Auto did not include the event-id header. Should not happen with Elfa's current delivery. If it does, check that you are not behind a proxy that strips custom headers.

### Webhook signature errors

The receiver does not verify webhook signatures (Elfa Auto delivers unsigned), so signature/timestamp 401s do not occur. If Elfa ever re-enables signed delivery, a verifier would need to be reimplemented. See `references/elfa-webhooks.md`.

### `422 Unprocessable Entity`

FastAPI rejected the request because a header type didn't match. Check the receiver's `auto_events` declaration vs what Elfa sends; if Elfa changes header names, update the receiver.

## In Telegram alerts

### `manual_intervention_required`

Two main paths produce this alert:

1. **Entry placed but TP/SL setup partially or fully failed.** The position is open without a complete protective bracket. Read the alert details for which leg failed and intended TP/SL prices. Manually place the missing leg via GRVT UI.

2. **Registry write failed AFTER successful place_order.** The order is on GRVT but the local registry could not record it. Manually update the registry: mark the strategy `fired` and the fire `placed` with the GRVT order_id from the alert.

### `unknown_strategy`

Webhook arrived with a `queryId` that has no matching row in the registry. Causes:
- Auto query was registered but the local registry write failed
- Local DB was reset / migrated and lost the row
- Webhook delivered to the wrong receiver (URL mix-up)

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

## At the cloudflared tunnel layer

### Tunnel URL responds locally but Elfa webhook never arrives

cloudflared's quick tunnels (the `--url` form) are ephemeral. If cloudflared restarts, the URL changes but existing strategies on Elfa still point at the old URL. The webhook will hit a dead URL.

Solutions:
- Always re-create strategies after a tunnel restart with the new URL.
- Move to a named cloudflared tunnel (`cloudflared tunnel create`).
- Move to a PaaS deploy with a stable HTTPS URL.

### `Could not resolve host: <subdomain>.trycloudflare.com`

Local DNS hasn't propagated the new subdomain yet. This does not affect Elfa (their resolver picks up new domains quickly). Test the tunnel from another network, or wait a minute.

### `cloudflared: command not found`

Install:
- macOS: `brew install cloudflared`
- Linux: download from `https://github.com/cloudflare/cloudflared/releases`

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
pkill -9 -f "elfa_grvt_bot"

# 4. Stop the tunnel
pkill -9 cloudflared

# 5. Manually close any open GRVT positions via the UI
```

This is a panic stop, not a graceful shutdown. Use only when something is wrong and you need to halt all firing immediately.
