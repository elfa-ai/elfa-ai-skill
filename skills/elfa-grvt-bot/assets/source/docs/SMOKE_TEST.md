# Manual Smoke Test (production)

Run this once after the full system is wired up, before relying on it for
real strategies. It places a small ($100-cap) order on GRVT prod to prove
every link in the chain works end-to-end.

## Pre-checks

- Receiver is running and reachable at `RECEIVER_PUBLIC_URL`. Verify:
  ```bash
  curl -s "$RECEIVER_PUBLIC_URL/healthz"
  # → {"ok":true}
  ```
- Telegram bot has spoken to you at least once and `TELEGRAM_CHAT_ID` is
  correct. Verify:
  ```bash
  curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
       -d "chat_id=$TELEGRAM_CHAT_ID&text=smoke test ping"
  ```
  You should see "smoke test ping" arrive in Telegram.
- GRVT account has at least $200 of free margin and supports
  `BTC_USDT_Perp` trading.
- `BTC_USDT_Perp` is in `SYMBOL_ALLOWLIST`.

## Run

In an agent session in this directory, say:

> "Create a smoke-test strategy: when BTC price > 0 (always true), buy
> 0.001 BTC_USDT_Perp market. Cap notional at $100. Expiry 1h."

The agent should:
1. Run `registry_cli.py alerts --pending` (no alerts expected on a fresh setup).
2. Forward "smoke test buy 0.001 BTC when BTC > 0" to Elfa Builder Chat.
3. Show you a draft EQL (single condition `price.current(BTC) > 0`).
4. Confirm the order spec with you.
5. Validate via `/v2/auto/queries/validate`.
6. Show the full strategy summary.
7. On your "yes," POST to `/v2/auto/queries` (API-key auth) and write the
   registry row.

Within seconds, you should see in Telegram:
1. Auto's native alert: "BTC > 0 fired" (or similar wording from your EQL).
2. The receiver's order receipt: "placed BUY 0.001 BTC_USDT_Perp market on
   prod — order_id=ord_..."

## Verify

```bash
python src/registry_cli.py list  # the strategy is now status=fired
python src/registry_cli.py alerts  # the order_placed alert is logged
```

In the GRVT UI: confirm the order filled and you have ~0.001 BTC long.

## Unwind

Manually, in the GRVT UI: market-sell 0.001 BTC_USDT_Perp to flatten.

(For v1 we don't have an exit-strategy primitive; smoke-test cleanup is
manual. Future work in spec §9 includes paired entry/exit strategies that
would handle this automatically.)

## Rollback

If anything goes wrong (signature mismatch, tunnel drop, GRVT auth):

- Logs are at the receiver's stdout (or PaaS log stream).
- Pending alerts are surfaced next agent session in this directory.
- Cancel the Auto query immediately:
  ```bash
  python src/registry_cli.py cancel <query_id>
  ```
