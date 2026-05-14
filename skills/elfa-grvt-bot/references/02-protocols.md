# 02 - Protocols

Wire contracts for every external dependency. Where production and `docs.elfa.ai` disagree, production wins. Captured frames in `captured-frames/` are the binding contract for the parser.

---

## Elfa Auto API

Base URL: `https://api.elfa.ai`. Authentication header on every request: `x-elfa-api-key: <ELFA_API_KEY>`. No other headers, no request signing.

### POST /v2/auto/chat (Builder Chat)

Authors EQL from a natural-language prompt. The bot ALWAYS prepends `Notify me when:` to the user's description before sending.

Request body:

```json
{"message": "Notify me when: 1h RSI dips below 30 on BTC.",
 "sessionId": "optional-session-id-for-iterative-refinement"}
```

Response (200):

```json
{
  "sessionId": "76554870-7db1-4db7-8354-490e9e356ed4",
  "response": "Markdown text. The EQL is embedded inside a ```json ... ``` code block.",
  "title": "BTC RSI oversold on 1h",
  "reasoning": null,
  "planIds": []
}
```

To extract the EQL, find the first ` ```json ` fence and the matching closing ` ``` ` in `response`, then `json.loads()` the contents. Pass the resulting dict through unchanged as the `query` field of the create-query body. Never hand-edit it. If it doesn't match user intent, re-prompt with `sessionId` set, or ask the user to rephrase.

### POST /v2/auto/queries/validate

Validates an EQL body without persisting it. Free; estimated cost is returned for the user to confirm.

Request body:

```json
{"query": {<the inner EQL: conditions, actions, expiresIn>}}
```

**Asymmetry to know:** this endpoint wraps its argument as `{query: arg}` internally if the implementation uses a wrapper, OR takes the full `{query: ...}` body directly. The Elfa REST API expects `{query: ...}`. If the wrapper passes `arg` directly, the client must pass only the inner EQL. The reference implementation's `validate_query()` accepts the inner EQL and wraps it internally.

Response (200):

```json
{
  "valid": true,
  "errors": [],
  "warnings": [],
  "simulationLlmCallsEstimate": {
    "conditionCallsUpperBound": 0,
    "actionCallsUpperBound": 0,
    "conditionCallsBySpeed": {"fast": 0, "expert": 0},
    "actionCallsBySpeed": {"fast": 0, "expert": 0}
  },
  "estimatedCredits": 0
}
```

If `valid` is false, surface the `errors` array to the user and STOP. Do not attempt to create the query.

### POST /v2/auto/queries (Create Query)

Persists an active query.

Request body:

```json
{
  "title": "BTC RSI oversold on 1h",
  "description": "Buy signal when 1h RSI dips below 30",
  "query": {
    "conditions": {"AND": [{"source": "ta", "method": "rsi",
                            "args": {"symbol": "BTC", "timeframe": "1h", "period": 14},
                            "operator": "crosses_below", "value": 30}]},
    "actions": [{"stepId": "step_1", "type": "notify",
                 "params": {"channel": "telegram"}}],
    "expiresIn": "24h"
  }
}
```

Response (200/201). **`docs.elfa.ai` is wrong about the response shape.** Production returns:

```json
{
  "id": "f12c99e3-c9dc-4aa0-85bd-a152a92f5bd3",
  "status": "active",
  "title": "...",
  "description": "...",
  "createdAt": "2026-05-13T06:36:34.914Z",
  "expiresAt": "2026-05-13T07:36:34.912Z",
  "evaluationType": "cron_driven",
  "timeframe": null,
  "preview": {
    "conditionStates": [...],
    "wouldTriggerNow": false,
    "matchingConditions": 0
  },
  "simulationLlmCallsEstimate": {...},
  "estimatedCredits": 0
}
```

The query identifier is `id`, NOT `queryId`. The docs show `{queryId, status, cost}`; do not write code against that shape.

### GET /v2/auto/queries/{id} (Poll Query)

Status reconciliation. Used on startup, on stream close, and on every reconcile cycle.

Response (200):

```json
{
  "queryId": "f12c99e3-c9dc-4aa0-85bd-a152a92f5bd3",
  "status": "active",
  "latestEvaluation": {
    "evaluatedAt": "2026-05-13T06:53:25.000Z",
    "wouldTriggerNow": false
  },
  "executions": [
    {
      "id": "94631fa0-05db-482a-9040-cfbaf13ece71",
      "queryId": "f12c99e3-c9dc-4aa0-85bd-a152a92f5bd3",
      "type": "notification",
      "status": "success",
      "createdAt": "2026-05-13T06:53:25.405Z"
    }
  ]
}
```

`executions[i].id` is the same UUID namespace as SSE `data.executionId`. Dedupe across SSE and poll-query using this field. The bot does not replay poll-query executions through the order path: a stale execution (receiver was offline) cannot be safely turned into a fresh order because prices may have moved. Surface as `manual_intervention_required` instead (see `05-failure-modes.md`).

### POST /v2/auto/queries/{id}/cancel

Cancels an active query. Two-step lifecycle: this transitions status to `cancelled`. Hard-deletion (`DELETE /v2/auto/queries/:id`) is allowed only after cancel and is intentionally NOT used by this bot so strategies stay auditable.

Returns 200 on success.

Returns **409** if the query is already terminal (`triggered`/`expired`/`cancelled`/`failed`). Treat 409 as success-equivalent. The reference implementation coerces it to `{"id": query_id, "status": "already_terminal"}`.

### GET /v2/auto/queries/{id}/stream (SSE)

Long-lived stream. Sends one trigger event per fire. The bot opens one stream per active strategy.

Required client header: `Accept: text/event-stream`. Auth via `x-elfa-api-key`.

Response codes:

| Code | Meaning | Bot behavior |
|---|---|---|
| 200 | Stream established | Parse frames |
| 204 | No content | Treat as empty stream; exit cleanly |
| 401 | Bad API key | Hard fail; surface to alerts |
| 404 | Query not found | Strategy mismatch; mark local `failed`, alert |
| 410 | Query already terminal at connect time | Fall back to poll-query for reconciliation |
| 5xx | Transient | Exponential backoff (start 2s, cap 60s), reconnect |

#### Production wire format (verified 2026-05-14 against `api.elfa.ai`)

Lead this section with what production actually emits today. The docs at `docs.elfa.ai/auto/notifications` describe a different schema (`event: notification:new`); as of 2026-05-14 that schema has **NOT** been observed in production. The parser accepts the documented schema for forward compatibility but the captured-frames fixtures are real bytes from this schema.

```
id: <sse-level uuid, e.g. ce6ef797-aa9b-47eb-80c0-07b3ff118347>
event: notification
data: {
  "status": "triggered",
  "queryId": "<uuid>",
  "executionId": "<uuid>",
  "triggerTime": "2026-05-14T10:15:32.965Z",
  "timestamp": 1778815732965,
  "title": "Auto Plan Alert",
  "body": "...",
  "message": "...",
  "queryTitle": "...",
  "queryIdShort": "...",
  "queryDisplayTitle": "...",
  "autoDetails": "...",
  "conditionsMet": 1
}
```

Required fields (parser drops with WARN if missing):

- `status == "triggered"`
- `queryId` (string UUID, must match the stream URL's query id)
- `executionId` (string UUID; **canonical idempotency key**, matches `executions[i].id` from poll-query)
- `triggerTime` (ISO 8601 string)

Informational fields (passthrough): `timestamp`, `title`, `body`, `message`, `queryTitle`, `queryIdShort`, `queryDisplayTitle`, `autoDetails`, `conditionsMet`.

#### Stream lifecycle events

In addition to trigger events, Elfa sends two control events on the SSE stream:

```
: keep-alive
```

Sent every ~15s as an SSE comment (line starts with `:`). The parser MUST skip these silently; they exist only to keep the TCP connection from being reaped by intermediate proxies.

```
event: end
data: {"code": "QUERY_STREAM_CLOSED", "status": "triggered", "queryId": "<uuid>"}
```

Sent after a fire when Elfa is closing the stream from its side. The parser drops this silently (it is not a trigger). The strategy-loop catches the subsequent connection close, loops back to poll-query for status reconciliation, observes `status: triggered`, and exits cleanly.

#### Forward-compat: documented schema (not yet observed in production)

`docs.elfa.ai/auto/notifications` (as of 2026-05-14) describes a different schema:

```
id: 12345
event: notification:new
data: {
  "id": 12345,
  "type": "athena_query_notify_only",
  "category": "alerts",
  "title": "...",
  "body": "...",
  "data": {"queryId": "<uuid>"},
  "priority": "high",
  "createdAt": "2026-04-01T12:00:00.000Z"
}
```

Required fields per docs: `id` (number), `type`, `category`, `title`, `body`, `data.queryId`, `priority`, `createdAt`. Dedupe key per docs: `id` (numeric, cast to string).

The parser accepts this format for the day Elfa rolls it out, but as of 2026-05-14 every captured frame has used the production schema above. If you start seeing `event: notification:new` in production, capture a fresh fixture and add it to `captured-frames/`.

#### Parser algorithm (dispatch on `event:` line)

1. **Skip if line starts with `:`** (SSE comment / keep-alive). No-op.
2. **`event: notification` + `status == "triggered"`** -> parse the production schema. Dedupe key = `payload["executionId"]`. Verify `payload["queryId"]` matches stream URL.
3. **`event: notification:new`** -> parse the documented schema. Dedupe key = `str(payload["id"])`. Verify `payload["data"]["queryId"]` matches.
4. **`event: query.triggered`** -> parse the older canonical envelope. Dedupe key = `payload["eventId"]`. Verify `payload["queryId"]` matches.
5. **`event: end`** -> silently skip. Not a trigger; the strategy-loop's outer reconnect/reconcile logic handles stream closure.
6. **Any other `event:` value** -> silently skip (heartbeats, unknown control events).
7. **`event:` present but matches no schema above** -> drop with WARN. Increment a per-query drift counter; emit a `parser_drift` ERROR alert if 3+ drops in a 10-minute window for the same query.

#### Captured-frames fixtures

`captured-frames/notification_cron_once_2026-05-13.txt` and `notification_price_current_2026-05-13.txt` are real bytes from production (same schema as the 2026-05-14 verification). They are the parser's regression test. Add new date-stamped captures whenever a fresh schema or close-frame variant is observed.

### Symbol validation: GET /v2/auto/validate-tradable-symbol/{symbol}

Documented but not used by this bot directly. The bot verifies tradability against GRVT itself (`fetch_market` on the GRVT side), since Elfa's view of tradable symbols and GRVT's may differ.

Supported formats:

- Crypto majors: bare uppercase ticker (`BTC`, `ETH`, `SOL`)
- HIP-3 (provider-prefixed tokenized stocks/commodities): `xyz:TSLA`, `flx:OIL`, `vntl:OPENAI`

### EQL reference (Elfa Query Language)

The bot never authors EQL itself. Builder Chat is the only authority; the bot extracts the JSON block from the response and passes it through unchanged to validate/create. This section is a read-only summary for understanding what Builder Chat returns and sanity-checking it before showing the plan to the user.

#### Structural rules

- Root group must be `AND` or `OR`.
- Max condition tree depth: 3.
- Max leaf conditions per query: 10.
- `expiresIn`: one of `1h, 2h, 4h, 8h, 12h, 24h, 2d, 3d, 5d, 7d`. Default `24h`.
- **One `actions` step per query.** Athena currently enforces this. Builder Chat will emit a single notify-style step; pass it through.

#### Supported condition sources

| Source | Methods | Notes |
|---|---|---|
| `price` | `current`, `change`, `high`, `low`, `volume` | Args: `symbol` (bare ticker like `BTC`), and `period` for change/high/low/volume |
| `ta` | `rsi`, `macd_value`, `macd_signal`, `macd_histogram`, `bbands_upper/middle/lower`, `ema`, `sma`, `atr`, `stoch_k`, `stoch_d`, `cci`, `willr` | Args: `symbol`, `timeframe` (`1m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 12h, 1d`), optional `period`. `ema`/`sma` require `period`. `period` must be a number, not a string. |
| `cron` | `once`, `every`, `onceRemainTrue` | Arg: `period`. **Minimum `1h`** per `docs.elfa.ai/auto/triggers`. Accepted values: `1h, 2h, 4h, 8h, 12h, 24h, 1d, 7d`. |
| `llm` | `athena_condition` | Args: `query` (natural-language predicate string), `period`, optional `speed` (`fast` default, `expert` more expensive). **Minimum `period: "1h"`** per `docs.elfa.ai/auto/agent-quickstart`. |
| `tweet` (Signal: X/Twitter Post) | `semantic` | Args: `username` (without `@`, must be on Elfa's monitored-accounts list), `text`, `minConfidence` (integer 0-100) |
| `news` (Signal: Event) | `semantic` | Args: `text`, `minConfidence` (integer 0-100). Matches event-like mentions from news-tagged sources. |

#### Operators

`>`, `<`, `>=`, `<=`, `==`, `!=`, `crosses_above`, `crosses_below`.

`crosses_*` requires previous-state tracking and only fires on the transition (the right choice for "RSI dips below 30"). The state-form operators (`<`, `>`, etc.) fire whenever the condition is true, which means immediate-fire if already true at creation.

For the bot's single-fire architecture both behave the same, but `crosses_*` avoids surprise immediate fires on creation.

#### Dynamic comparisons

`value` can reference another live source rather than a literal:

```json
{
  "source": "price", "method": "current", "args": {"symbol": "ETH"},
  "operator": "crosses_above",
  "value": {"source": "ta", "method": "bbands_upper",
            "args": {"symbol": "ETH", "timeframe": "4h"}}
}
```

#### Allowed action types

`webhook`, `notify`, `telegram_bot`, `llm`, `market_order`, `limit_order`. The bot only ever creates notify-style queries (the authoring flow always prepends `Notify me when: ...`), so Builder Chat emits one of `notify` / `telegram_bot` / `webhook` (or `llm` with a notify callback). Trade-flavoured actions (`market_order` / `limit_order`) require an exchange connection on Elfa's side (`GET /v2/auto/exchanges` preflight, Hyperliquid integration) and are explicitly OUT OF SCOPE for this bot. Order placement is owned by the receiver, not by Elfa.

#### Validation: always before create

The bot must call `POST /v2/auto/queries/validate` before `POST /v2/auto/queries`. The validate endpoint is free and returns `{valid, errors, warnings, estimatedCredits, simulationLlmCallsEstimate}`. If `valid: false`, surface errors and stop.

---

## GRVT API

**Primary reference: `gravity-technologies/grvt-skills/skills/perpetual-trading`.** GRVT publishes an official agent skill that documents the trading API surface (login, fetch_market, single-order create, position config). This spec depends on it; do not duplicate. For each operation below, the official skill is authoritative on:

- SDK setup (`GrvtCcxt` parameters, env enum)
- Login flow with auto-discovery of `sub_account_id` and `funding_account_address`
- Symbol format and market discovery
- Single-order `create_order` (limit/market, params: `time_in_force`, `post_only`, `reduce_only`, `client_order_id`)
- Individual TP/SL trigger order placement via `create_trigger_order` helper
- `set_position_config` helper (full EIP-712 typed-data signing for margin type + leverage)
- Position / balance / order queries
- Min notional (100 USDT platform-wide; instrument metadata may set higher per-asset floors)
- Order response shape (`order_id: 0x00` placeholder; track by `client_order_id`)

This spec adds two things on top of that reference:

1. **Atomic OTOCO submission via `POST /full/v2/bulk_orders`** (the official skill submits trigger orders independently after entry fills; this bot needs entry + TP + SL atomic to avoid a naked-position window between entry fill and TP/SL placement).
2. **Auto-discovery of `GRVT_TRADING_ACCOUNT_ID`** at preflight time from the login response (so the user does not have to paste it manually).

Two surfaces (same as the perpetual-trading skill):

- **Public market data**: `https://market-data.grvt.io/...`. Unauthenticated. Used for instrument metadata and prices.
- **Authenticated trading**: `https://edge.grvt.io/...` for auth, `https://trades.grvt.io/...` for orders. Requires API key + EIP-712 signing.

The bot **must** be prod-only. `GRVT_ENV != prod` is a startup error. No testnet path.

### Authentication and trading_account_id auto-discovery

`POST https://edge.grvt.io/auth/api_key/login` with body `{"api_key": "<GRVT_TRADING_API_KEY>"}`.

The response (JSON) includes:

```json
{
  "sub_account_id": "3151991409725740",
  "funding_account_address": "0x...",
  ...
}
```

Plus the `Set-Cookie: gravity=...` header and `X-Grvt-Account-Id` response header.

**Preflight uses this response to populate `.env`** with `GRVT_TRADING_ACCOUNT_ID=<sub_account_id>` automatically. Receiver startup also calls this endpoint on boot to refresh the cookie; auto-discovery is idempotent (the value never changes for a given API key).

**Gotcha: HTTP 200 does NOT mean success.** GRVT (via Cloudflare) returns 200 with an error body when the request is rejected for geo-block, bad key, or other policy reasons. The reliable signal is `Set-Cookie: gravity=...`. If the cookie is absent, parse the response body for the actual error:

```json
{"error": "Access from this location is not allowed.", "status": "failure"}
```

Preflight uses cookie presence (not status code) as the success signal. Surface the body's `error` field if the cookie is missing.

### Market data, single-order create, position queries

See the official perpetual-trading skill (`grvt-skills/skills/perpetual-trading/SKILL.md`). No spec-level additions for these.

### Order placement: POST https://trades.grvt.io/full/v2/bulk_orders (OTOCO)

This is the spec-specific addition not covered by the official skill.

The bot submits an atomic 3-order OTOCO envelope (entry + TP + SL) so that a strategy fire that survives guardrails always produces a protected position, not a naked one.

**Path:** `POST https://trades.grvt.io/full/v2/bulk_orders`. **Auth:** `Cookie: gravity=<token>` from login + `X-Grvt-Account-Id: <account>` response header.

**Request body shape** (canonical per GRVT api-spec):

```json
{
  "sub_account_id": "<from GRVT_TRADING_ACCOUNT_ID>",
  "orders": [
    {<parent Order>},
    {<TP Order>},
    {<SL Order>}
  ],
  "order_i_ds": [],
  "client_order_i_ds": [],
  "time_to_live_ms": "500"
}
```

Each `Order` in the array is a fully signed Order payload (use the perpetual-trading skill's `get_grvt_order` + `get_order_payload` helpers to build and sign). All three Orders must satisfy these OTOCO rules per the api-spec:

- Same `instrument` across all three.
- Same `size` across all three.
- Parent side is **opposite** of TP and SL sides (long entry -> TP and SL are sells; short entry -> TP and SL are buys).
- Parent is a plain Order (no `metadata.trigger`).
- TP is an Order with `metadata.trigger.trigger_type = "TAKE_PROFIT"` and `metadata.trigger.tpsl = {trigger_by, trigger_price, close_position, is_split_position}`.
- SL is an Order with `metadata.trigger.trigger_type = "STOP_LOSS"` and the same `tpsl` structure.

**`tpsl.trigger_by` options:** `INDEX`, `LAST`, `MID`, `MARK`. **Default for this bot: `MARK`** (index-and-mark are less manipulation-prone than last/mid for perps).

**`tpsl.close_position`:** `false` for OTOCO (TP/SL carry their own size and reduce_only=true). Set to `true` only for standalone trigger orders that should close the entire position regardless of size. For OTOCO, the size is fixed at the parent's size and `close_position=false` is correct.

**`tpsl.is_split_position`:** `false` for this bot. Set `true` only when partitioning a single position across multiple TP/SL levels (out of scope for v1).

**`time_to_live_ms`:** `"500"` is a safe default. Bounds: 0 to 5000 (capped server-side), rounded down to 100ms granularity.

**Critical: `metadata.trigger` is NOT part of the EIP-712 signature.** The trigger fields live in `metadata`, which is documented as "never signed, never transmitted to the smart contract." The pattern (from the perpetual-trading skill's `create_trigger_order` helper):

1. Build the Order skeleton via `get_grvt_order(...)`.
2. Sign it via `get_order_payload(order, private_key=..., env=..., instruments=...)`.
3. **After signing**, inject `payload["order"]["metadata"]["trigger"] = {...}`.
4. Add the assembled payload to the `orders` array.

Repeat for parent (no trigger injection), TP (trigger_type=TAKE_PROFIT), and SL (trigger_type=STOP_LOSS).

**Why the bot uses this v2 endpoint specifically:** the pysdk's `create_order` and the perpetual-trading skill's `create_trigger_order` both submit single orders. They cannot atomically submit entry + TP + SL together. The bot needs atomicity because a fire that fills entry but fails to place TP/SL leaves a naked position (the trigger that fired may be stale by the time the bot reconnects to retry; user is left with risk). `/full/v2/bulk_orders` with OTOCO is the only path GRVT provides for atomic 3-leg submission. The pysdk does not wrap this endpoint; build the request body manually as described above and POST via `httpx` or `requests` with the auth cookie and `X-Grvt-Account-Id` headers.

**TP and SL prices** are computed from the reference mid (or mark) price at trigger time, then aligned to `tick_size`. See `04-algorithms.md` for the algorithm.

### Position config (margin type and leverage)

Use the `set_position_config` helper from the perpetual-trading skill verbatim. The bot calls it from the strategy-loop's leverage step when a strategy specifies a non-null `leverage`. Path: `POST https://trades.grvt.io/full/v1/set_position_config` with EIP-712 signed payload.

**The legacy `set_initial_leverage_v1` endpoint is officially deprecated** per the GRVT api-spec (it returns an explicit deprecation marker; some receivers see error code 2106). Do not use it. Use `set_position_config` only.

### Geo restrictions

GRVT geo-blocks certain regions. Confirmed blocked as of 2026-05-13: Hong Kong (Cloudflare datacenter `HKG`). The error body says `"Access from this location is not allowed."`.

Preflight surfaces this. Hosting recommendation: Fly.io `iad` / `lax`, AWS `us-east-1` / `eu-west-1`, or any VPS in a non-blocked region. Users in blocked regions can VPN.

---

## Telegram Bot API (optional)

Base URL: `https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}`. The token is in the URL; no other auth.

Both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` must be set for push to be enabled. If either is missing, push is silently skipped; in-chat alerts continue to work.

### POST /sendMessage

```
POST https://api.telegram.org/bot{token}/sendMessage
form-encoded:
  chat_id=<TELEGRAM_CHAT_ID>
  text=<alert text>
```

Response (200): `{"ok": true, "result": {...}}`. The bot does not need to inspect `result`; treat `ok: true` as success.

Failure modes:

- 401: `TELEGRAM_BOT_TOKEN` is wrong.
- 400 with `"chat not found"`: `TELEGRAM_CHAT_ID` is wrong, or the user has never messaged the bot (Telegram requires the user to initiate the conversation before the bot can DM them).

### GET /getMe

Sanity-check the token without sending any message. Used by preflight.

Response (200):

```json
{"ok": true, "result": {"id": 8915255901, "is_bot": true, "username": "elfa_grvt_2_bot", ...}}
```

### GET /getUpdates (one-time, for chat_id discovery)

Used only during initial setup when the user runs through the credential walkthrough. Returns messages sent to the bot; the first one tells you the chat id.

```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" \
  | jq '.result[0].message.chat.id'
```

If `result` is empty, the user hasn't messaged the bot yet, OR the bot has a webhook configured (which drains updates). Check `getWebhookInfo` first.

---

## Captured-frames as contract

The two files in `captured-frames/` are the binding wire-format contract:

- `notification_cron_once_2026-05-13.txt`: cron-driven fire
- `notification_price_current_2026-05-13.txt`: price-condition fire

Both are byte-identical to what production transmits. Parser tests must load these and assert `_build_event()` yields a valid `{event_id, data}` dict. If Elfa ever changes the schema, those tests break and the fix is a re-capture, not an investigation.

To capture a fresh frame, see the algorithm in `04-algorithms.md` (section "Capturing SSE bytes"). Date-stamp filenames as `notification_<source>_<YYYY-MM-DD>.txt` and check them into the spec alongside the existing ones.
