# 10 - Troubleshooting

Diagnostic recipes keyed on the user-visible signature. When a symptom is reported, find the row and follow the action.

## A note on GRVT signup

GRVT does NOT require KYC for signup. There is no identity verification, no document upload, no waiting period. Signup -> deposit -> generate API key -> ready. If the user reports being "stuck on verification", they are most likely on the wrong platform; confirm they are at `https://grvt.io` (signup link with referral: `https://grvt.io/?ref=LN2DP6F`).

## Symptom -> cause -> fix

### Symptom: `elfa create_query failed: 401`

- **Cause:** `ELFA_API_KEY` is wrong, expired, or missing.
- **Fix:** Re-copy from the Elfa developer portal. No leading/trailing whitespace. Restart the receiver after editing `.env`.
- **Prevention:** `elfa-grvt-bot preflight` catches this in ~200ms at install time.

### Symptom: `validate_query` returns `valid: false`

- **Cause:** Some part of the EQL is malformed.
- **Fix:** Read the `errors[]` array verbatim. Common errors:
  - Period below the documented minimum (`cron.*` and `llm.athena_condition` both require `period >= 1h` per `docs.elfa.ai/auto/triggers` and `auto/agent-quickstart`)
  - Missing `period` on a TA method that requires it (`ema`, `sma`)
  - `period` passed as string `"14"` instead of number `14`
- **Recovery:** Re-prompt Builder Chat with a clearer description, or ask the user to rephrase. Do NOT hand-edit the EQL JSON.

### Symptom: `elfa cancel_query failed: 409`

- **Cause:** The query is already terminal (`triggered`, `expired`, `cancelled`, `failed`).
- **Fix:** None needed. The bot's `cancel_query` should treat 409 as success-equivalent (see `02-protocols.md`).
- **Verification:** `GET /v2/auto/queries/<id>` will show one of the terminal statuses.

### Symptom: Receiver log: `dropping SSE 'notification' frame: missing fields [...]`

- **Cause:** Elfa changed the SSE payload schema upstream. The parser is fail-closed: it refuses to act on frames missing required fields.
- **Fix:**
  1. Capture a fresh frame using the procedure in `04-algorithms.md` (section "Capturing SSE bytes").
  2. Diff against `captured-frames/notification_<source>_<date>.txt`.
  3. If a required field was renamed or removed, update `_REQUIRED_EVENT_FIELDS` and the parser checks in `04-algorithms.md`. Add a regression test using the new captured frame.
  4. File the issue upstream (Elfa docs / API team).
- **User-visible:** The bot will raise `manual_intervention_required` alerts via poll-query reconciliation each time a fire happens. The user reviews GRVT manually until the parser is updated.

### Symptom: GRVT log: `_get_cookie ... Error getting cookie: 'NoneType' object has no attribute 'items'`

- **Cause:** GRVT returned HTTP 200 but no `Set-Cookie: gravity=...` header. This is the SDK's misleading symptom for what is actually an auth rejection (geo-block, invalid key, IP block, etc.).
- **Diagnostic:** Run the raw login call:
  ```bash
  curl -sX POST "https://edge.grvt.io/auth/api_key/login" \
       -H "Content-Type: application/json" \
       -d "{\"api_key\":\"$GRVT_TRADING_API_KEY\"}"
  ```
  Read the body:
  - `{"error":"Access from this location is not allowed.","status":"failure"}` -> **geo-block**. Move the bot to an allowed region (examples that worked around this spec date: Fly.io `iad`/`lax`, AWS `us-east-1`/`eu-west-1`, any non-blocked VPS) or run behind a VPN.
  - `{"error":"invalid api key", ...}` -> **bad / rotated key**. Re-generate on grvt.io Settings -> API Keys.
  - Anything else -> read the body, surface to the user, escalate upstream if unclear.
- **Prevention:** `elfa-grvt-bot preflight` catches this in ~200ms at install time, with a clear "GEO-BLOCK" or "no cookie" message.

### Symptom: Order placement returns `code=1000: You need to authenticate prior to using this functionality`

- **Cause:** Same as the cookie issue above; GRVT's order endpoint requires the cookie that login failed to issue.
- **Fix:** See "Error getting cookie" row above.

### Symptom: Order placement returns insufficient margin / collateral

- **Cause:** GRVT rejected the order because the account doesn't have enough free margin for the requested notional + leverage combo.
- **Fix:**
  1. Lower the strategy's `amount` or `leverage`.
  2. Deposit more collateral on GRVT.
  3. Check whether the user has open positions on other strategies consuming margin.
- **Verification:** `grvt_client.fetch_balance()` shows the available collateral.
- **Alert category:** `insufficient_margin`.

### Symptom: GRVT rejects with `Order size too granular`

- **Cause:** Amount is not aligned to GRVT's quantity step. In current instrument metadata, use `min_size` as the quantity step unless GRVT exposes a more specific field.
- **Fix:** Round amount up to an integer multiple of `min_size`, then re-check min_notional and max_notional. Example: BTC with `min_size=0.001` rejects `0.00126`; use `0.002` as the next valid size above 100 USDT near an 80k price.
- **Prevention:** The authoring flow and smoke test must compute amount with Decimal: `ceil(min_notional / mid / min_size) * min_size`.
- **Alert category:** `grvt_other`.

### Symptom: API says bulk order accepted but GRVT UI shows parent Rejected and TP/SL Cancelled

- **Cause:** The `/full/v2/bulk_orders` response was syntactically accepted, but the parent order failed downstream validation. TP/SL were cancelled as part of the OTOCO group.
- **Fix:** Treat this as not placed. Fetch order history or positions by `client_order_id` immediately after submit. Surface the parent rejection reason.
- **Prevention:** Do not emit `order_placed` from the POST response alone. Confirm parent accepted/filled first.

### Symptom: Receiver started but no SSE task spawned for a registered strategy

- **Cause:** The supervisor checks the registry every 5 seconds. Wait 5 seconds. If still no `spawning SSE task for <id>` log line:
  1. Verify the strategy is in the registry: `elfa-grvt-bot list`.
  2. Verify its status is `active`. If `failed` or any terminal, the supervisor will not spawn.
  3. Check `REGISTRY_DB_PATH` in the receiver's env matches what the authoring side wrote to. Two different `.env` files pointing at different DBs is a common confusion.
- **Fix:** Point both at the same `registry.db` (mounted volume in production).

### Symptom: SSE stream opens but no fire arrives after the trigger condition is true

- **Cause:** Elfa's condition evaluator runs on its own schedule. For cron and `llm.athena_condition`, the documented minimum `period` is `1h`. For TA conditions, evaluation cadence depends on the `timeframe`. The trigger may not actually have happened yet.
- **Diagnostic:** Poll `GET /v2/auto/queries/<id>` and read `latestEvaluation.wouldTriggerNow`. If `false`, Elfa doesn't think the condition has met yet.
- **Fix:** Confirm your data assumption (e.g., is the price actually below the threshold right now?). For TA, confirm the timeframe and period are what you expect.

### Symptom: Two receivers running simultaneously against the same registry

- **Cause:** A second `python -m elfa_grvt_bot` was started without stopping the first. Both opened SSE streams; both will attempt to place orders.
- **Risk:** If a fire arrives, the dedupe-by-executionId logic ensures only one order is placed (the second's `insert_fire_marker` fails on PK collision). Safe but wasteful.
- **Fix:** `elfa-grvt-bot teardown` (or kill the older PID). Audit the `.receiver.pid` file vs `ps` output.

### Symptom: Receiver eats CPU at idle

- **Cause:** Almost certainly a busy-wait somewhere. The supervisor should be sleeping 5s between cycles; each strategy loop should be awaiting bytes from the SSE stream.
- **Diagnostic:** `py-spy top --pid <receiver_pid>` shows what's hot.
- **Common culprits:**
  - SSE stream reconnect loop without backoff (check 5xx handling).
  - `httpx.AsyncClient` is being created/closed per request instead of reused.
  - SQLite calls are not parameterized and the query planner is slow.

### Symptom: Telegram alerts arrive but in-chat alerts don't (or vice versa)

- **Cause:** The two channels are independent. Telegram is real-time push (only when both env vars set); in-chat surfaces via the agent's session-start hook.
- **Fix:**
  - Missing in-chat: confirm the agent reads `elfa-grvt-bot alerts --pending` on session start. The shipped `AGENTS.md` says this; if the user uses a different `AGENTS.md` or a different agent, manually run `alerts --pending` to verify.
  - Missing Telegram: check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are both set in `.env`. Re-run `preflight`. The user must have messaged the bot once for `getUpdates` (and therefore the chat) to work.

### Symptom: `manual_intervention_required` alert raised for a strategy I didn't expect

- **Cause:** The receiver was offline (or crashed, or restarted) at the moment the strategy fired on Elfa. On startup, poll-query reported the executed row but no local `fires` row matched; the bot raised the alert to avoid replaying a stale fire.
- **Fix:**
  1. Open GRVT and inspect: was the trade somehow placed? (Should not be, but verify.)
  2. Decide: enter manually at the current price, or skip this fire.
  3. `elfa-grvt-bot ack <id>` to clear the alert.
- **Prevention:** Run the receiver in a managed environment with restart-on-failure (systemd, Fly.io, etc.). Don't run on a laptop that may sleep.

### Symptom: A strategy is stuck in `active` but the SSE task for it isn't running

- **Cause:** Either the supervisor crashed (entire receiver dead), or the strategy task is in a backoff retry loop after repeated stream failures.
- **Diagnostic:** `tail -50 receiver.log | grep <query_id>`.
- **Fix:**
  - If supervisor is dead, `elfa-grvt-bot run` to restart.
  - If task is in backoff for a real reason (404 on stream, auth failure), the strategy should have been marked `failed`. If it's still `active`, that's a bug in the strategy_loop; file an issue and manually transition: `elfa-grvt-bot cancel <id>`.

### Symptom: Tests fail with `executionId is required` against captured frames

- **Cause:** A captured-frames fixture is malformed or was edited.
- **Fix:** Re-capture the frame using the procedure in `04-algorithms.md`. Don't hand-edit the fixture files; they're verbatim production bytes.

## When to escalate

- The captured-frames contract no longer matches production AND the parser-drift signature is appearing: file with Elfa, capture a fresh frame, update the spec.
- GRVT order placement is failing with an error not in any row above: GRVT support, attach the order body (with API keys redacted) and the response.
- Preflight passes but operational failures persist: probably a real bug. File with the spec maintainer; include the receiver log, the failing fire's `raw_payload` from `fires.raw_payload`, and the strategy spec.

## Useful diagnostic commands

```bash
# List all strategies and statuses
elfa-grvt-bot list

# All pending alerts (default) or all alerts ever
elfa-grvt-bot alerts --pending
elfa-grvt-bot alerts --all

# Raw SQL access for ad-hoc queries
sqlite3 registry.db
> SELECT event_id, query_id, outcome, error FROM fires ORDER BY received_at DESC LIMIT 20;
> SELECT * FROM strategies WHERE status = 'failed';
> SELECT severity, category, message FROM alerts WHERE acked_at IS NULL;

# Raw Elfa poll-query
curl -s "https://api.elfa.ai/v2/auto/queries/<id>" -H "x-elfa-api-key: $ELFA_API_KEY" | jq

# Raw GRVT login probe
curl -sX POST "https://edge.grvt.io/auth/api_key/login" \
     -H "Content-Type: application/json" \
     -d "{\"api_key\":\"$GRVT_TRADING_API_KEY\"}" -i

# Raw mid-price
curl -sX POST "https://market-data.grvt.io/full/v1/mini" \
     -H "Content-Type: application/json" \
     -d '{"instrument":"BNB_USDT_Perp"}' | jq

# Telegram bot identity
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe" | jq

# Receiver process
ps aux | grep "python.*-m elfa_grvt_bot" | grep -v grep
pgrep -f "python.*-m elfa_grvt_bot"

# Receiver log tail
tail -F receiver.log | grep -E "trigger|order|alert|ERROR|dropping SSE"
```
