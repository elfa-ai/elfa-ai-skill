# Manual Smoke Test (production)

Run this once after the full system is wired up, before relying on it for
real strategies. It places a small ($100-cap) order on GRVT prod to prove
every link in the chain works end-to-end.

## Pre-checks

- Receiver is running:
  ```bash
  ps aux | grep -F "elfa_grvt_bot" | grep -v grep
  ```
  You should see one `python -m elfa_grvt_bot` process. If not, start it
  in another terminal:
  ```bash
  set -a && source .env && set +a
  python -m elfa_grvt_bot
  ```
  The receiver logs `supervisor started` and then `spawning SSE task for
  <query_id>` for each active strategy. No tunnel or public URL needed.
- (Optional) If Telegram is configured, the bot has spoken to you at
  least once and `TELEGRAM_CHAT_ID` is correct. Verify:
  ```bash
  if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
    curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
         -d "chat_id=$TELEGRAM_CHAT_ID&text=smoke test ping"
  fi
  ```
  If Telegram is configured you should see "smoke test ping" arrive in
  Telegram. If not, in-chat alerts via `registry_cli.py alerts` still work.
- GRVT account has at least $200 of free margin and supports
  `BTC_USDT_Perp` trading.

## Run

In an agent session in this directory, say:

> "Smoke test: notify me when BTC price > 0 (always true). On trigger, buy
> 0.001 BTC_USDT_Perp market. Cap notional at $100. Expiry 1h."

The agent should:
1. Run `registry_cli.py alerts --pending` (no alerts expected on a fresh setup).
2. Forward the description to Elfa Builder Chat (framed as "Notify me when...").
3. Show you a draft EQL (single condition `price.current(BTC) > 0`).
4. Confirm the order spec with you.
5. Validate via `/v2/auto/queries/validate`.
6. Show the full strategy summary.
7. On your "yes," POST to `/v2/auto/queries` and write the registry row.

The receiver's supervisor picks up the new strategy on its next poll (~5s),
opens the SSE stream for it, and processes the trigger immediately. Within
seconds you should see in Telegram:

1. A `trigger_received` ping: "Elfa trigger fired: ... Placing BUY 0.001
   BTC_USDT_Perp (market) on GRVT"
2. An `order_placed` confirmation: "BUY 0.001 BTC_USDT_Perp (market)"

## Verify

```bash
python src/registry_cli.py list   # status=fired
python src/registry_cli.py alerts # order_placed alert recorded
```

In the GRVT UI: confirm the order filled and you have ~0.001 BTC long.

## Unwind

Manually, in the GRVT UI: market-sell 0.001 BTC_USDT_Perp to flatten.

(For v1 we don't have an exit-strategy primitive; smoke-test cleanup is
manual.)

## Rollback

If anything goes wrong:

- Logs are at the receiver's stdout (or PaaS log stream).
- Pending alerts are surfaced next agent session in this directory.
- Cancel the Auto query immediately:
  ```bash
  python src/registry_cli.py cancel <query_id>
  ```
  The receiver's per-strategy SSE task notices the terminal status on its
  next reconcile and exits.
