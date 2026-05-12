# Architecture

High-level design of the elfa-grvt-bot.

## Two surfaces

```
┌─────────────────────────────┐
│  USER (chat with agent)     │
│  describes a strategy       │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐         ┌──────────────────────────┐
│  AGENT SESSION              │ ──────▶ │  Elfa Builder Chat       │
│  + this skill               │ ◀────── │  POST /v2/auto/chat      │
│  + grvt-trading skill       │         └──────────────────────────┘
│  (read-only sanity checks)  │
└──────────────┬──────────────┘
               │  on user "yes":
               │   POST /v2/auto/queries (API-key auth)
               │   then registry_cli.py add
               ▼
┌─────────────────────────────┐
│  Local SQLite registry      │   strategies, fires, alerts tables
│  (registry.db)              │   shared by agent session + receiver
└──────────────┬──────────────┘
               │
   ┌───────────▼─────────────────────────────────┐
   │  ELFA AUTO (managed condition engine)        │
   │  evaluates conditions; on fire:              │
   │  POSTs webhook to <RECEIVER_PUBLIC_URL>      │
   └───┬─────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  RECEIVER (always-on FastAPI)           │
│  1. dedupe by event_id (unsigned in)    │
│  2. lookup strategy in registry         │
│  3. silent status check                 │
│  4. spawn Telegram alert in background  │
│  5. fetch_mid_price                     │
│  6. guardrails (env, notional cap)      │
│  7. set_leverage (best-effort)          │
│  8. POST full/v2/bulk_orders OTOCO      │
│     (parent + TP + SL atomic)           │
│  9. emit success or error alert         │
└──────────────┬──────────────────────────┘
               ▼
┌─────────────────────────────┐    ┌────────────────────┐
│  GRVT (default: prod)       │    │  Telegram chat     │
└─────────────────────────────┘    └────────────────────┘
```

## Module breakdown

| Module | Responsibility |
|---|---|
| `config.py` | Read env vars; only place that touches `os.environ` |
| `registry.py` | SQLite schema + CRUD for strategies, fires, alerts |
| `guardrails.py` | Pure-function checks (notional cap, env match, status). Symbol existence is delegated to GRVT (fetch_mid_price/order placement). |
| `telegram_sender.py` | Bot API send; never raises (only constructed when TELEGRAM_* are configured) |
| `alerts.py` | AlertWriter: registry insert (always) plus Telegram push (when configured) |
| `elfa_client.py` | Thin client over `/v2/auto/*`: builder_chat, validate, create, cancel (API-key auth) |
| `grvt_executor.py` | High-level GRVT operations; wraps GrvtCcxt + trigger client |
| `grvt_trigger_client.py` | Raw API for trigger orders + bulk_orders (OTOCO/OCO/OTO) |
| `receiver.py` | FastAPI app: webhook endpoint + background processing |
| `__main__.py` | Production entrypoint (wires real clients, runs uvicorn) |
| `registry_cli.py` (top-level src) | CLI for add/list/cancel/alerts/ack |

## Data flow on a fire

1. Auto evaluates condition; transitions to true.
2. Auto POSTs webhook to `<RECEIVER_PUBLIC_URL>/auto/events` with headers `X-Auto-Event-Id` (required, dedupe key) and `X-Auto-Timestamp` (informational). Delivery is unsigned.
3. Receiver checks for `X-Auto-Event-Id` and returns 200 within 1 second.
4. Background task in the same process:
   - INSERT into `fires` with `outcome='pending'` (idempotent on `event_id` PK).
   - SELECT strategy by `query_id`. If missing, alert `unknown_strategy`, return.
   - If `strategy.status != 'active'`, log silently and return (suppresses retry-spam).
   - Spawn daemon thread to send `trigger_received` Telegram (non-blocking).
   - Fetch `current_mid` via `executor.fetch_mid_price`.
   - Run guardrails (`check_guardrails`).
   - If `strategy.leverage` is set, call `executor.set_leverage` (best-effort; deprecated API is logged-only).
   - Call `executor.place_entry_with_tpsl(...)`. Internally:
     - Compute TP/SL absolute prices from current_mid + percentages.
     - Round to instrument tick_size.
     - Build parent + TP + SL as Order dataclasses with EIP-712 signatures.
     - POST to `https://trades.grvt.io/full/v2/bulk_orders` with `{sub_account_id, orders: [...]}`.
   - On success: update `fires.outcome='placed'`, set `strategies.status='fired'`, emit `order_placed` and `tpsl_armed` alerts.
   - On any error path: update `fires.outcome='grvt_error'`, emit categorized alert (`insufficient_margin`, `grvt_set_leverage`, `manual_intervention_required`, etc.).

## Strategy lifecycle

```
       active ───── fire (success) ─────▶ fired
         │                                  ▲
         │                                  │
         ├───── fire (terminal grvt) ───────┘
         │
         ├───── Auto expiresIn elapsed ───▶ expired
         │
         └───── user runs cancel ──────────▶ cancelled
                  (POST /v2/auto/queries/:id/cancel)
```

Single-fire by design. To re-arm a strategy, create a new one.

## Notification channels

The receiver is the sole emitter of alerts. Every alert is first written to the SQLite `alerts` table (the source of truth), then optionally pushed to Telegram if both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set. Auto queries created by this project use ONLY a `webhook` action — never a `telegram` action — to keep the narrative single-source.

The three alerts a normal fire produces:

1. `trigger_received` (background thread): immediately on accepted webhook, before order placement
2. `order_placed`: after successful entry submission
3. `tpsl_armed`: after TP and SL are confirmed (or `manual_intervention_required` if either failed)

Two delivery channels read from the registry:

- **In-chat (via `AGENTS.md`).** Generated `AGENTS.md` instructs the agent to run `python src/registry_cli.py alerts --pending` on every session start and surface unacked alerts before doing anything else. After surfacing, the agent runs `python -m registry_cli ack all` to clear the queue. Agents that support session-start or per-prompt hooks can wire `scripts/show_pending_alerts.sh` for automatic injection — see your agent's docs.
- **Telegram (optional, real-time push).** When configured, `AlertWriter.emit()` calls `telegram_sender.send()` synchronously after the registry write. When unconfigured, it is silently skipped — no error, no retry queue. Telegram exceptions never bubble up; they're logged as warnings so a flaky bot can't break order placement.

This dual design means the user always gets alerts in chat (free, no extra credentials), and optionally also gets push notifications on their phone for real-time visibility while away from the agent.

## Idempotency

`event_id` is the PK of `fires`. INSERT OR IGNORE means a duplicate webhook delivery (Auto retry) becomes a no-op insert; the receiver returns early without re-firing. At-least-once webhook delivery becomes exactly-once order placement.

## Real-money safety primitives

In layered order:

1. Per-strategy `max_notional_usd` cap.
2. Strategy-vs-receiver `env` match.
3. `status='active'` gate (silent log-only on retry of fired/cancelled strategies).
4. Symbol existence is enforced by GRVT itself: `fetch_mid_price` (and downstream order placement) fail loudly on unknown instruments and surface as `grvt_other` / `grvt_error` alerts. The user-facing safeguard is authoring-time verification — the agent calls `GrvtCcxt.fetch_market(symbol)` before creating the strategy and refuses to proceed if GRVT doesn't list it.
5. Top-level safety net: any unhandled exception in the background task emits a `receiver_internal_error` alert.
6. Post-order DB write is wrapped: if it fails, emit `manual_intervention_required` with the order ID so the operator can manually reconcile.

## Out of scope for v1

These are explicit YAGNI cuts in the spec:

- Position-state-aware lifecycle (re-arming exits after entry fills)
- Daily-loss kill switch / equity drawdown circuit breakers
- Per-strategy cooldown / refire prevention
- Price-drift sanity check at fire time
- Max concurrent open positions cap
- Web UI / dashboard
- Multi-user / multi-account support
- Wait-for-flat semantics

Each can be added later without changing the wire protocol or core architecture.
