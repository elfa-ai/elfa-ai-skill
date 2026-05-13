# Architecture

High-level design of the elfa-grvt-bot.

## Two surfaces

```
USER (chat with agent)
  -> AGENT SESSION (this skill + grvt-trading sanity checks)
  -> Elfa Builder Chat (POST /v2/auto/chat)
  -> user approves with "yes"
  -> POST /v2/auto/queries (API-key auth)
  -> registry_cli.py add
  -> Local SQLite registry (strategies, fires, alerts)
  -> RECEIVER (always-on outbound consumer)
     - polls registry every ~5s
     - opens one SSE task per active strategy
     - dedupes by eventId (INSERT OR IGNORE)
     - looks up strategy in registry
     - checks remote status
     - spawns Telegram alert in background
     - fetches mid price
     - runs guardrails (env, notional cap)
     - sets leverage (best-effort)
     - POSTs full/v2/bulk_orders OTOCO
     - emits success or error alert
  -> GRVT prod and optional Telegram chat
```

## Module breakdown

| Module | Responsibility |
|---|---|
| `config.py` | Read env vars; only place that touches `os.environ` |
| `registry.py` | SQLite schema + CRUD for strategies, fires, alerts |
| `guardrails.py` | Pure-function checks (notional cap, env match, status). Symbol existence is delegated to GRVT (fetch_mid_price/order placement). |
| `telegram_sender.py` | Bot API send; always constructed, but `send()` is a silent no-op when `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` is empty |
| `alerts.py` | AlertWriter: registry insert (always) plus Telegram push (when configured) |
| `elfa_client.py` | Thin client over `/v2/auto/*`: builder_chat, validate, create, cancel (API-key auth); `get_query` for status polling; `stream_notifications` async SSE consumer with fail-closed parser |
| `grvt_executor.py` | High-level GRVT operations; wraps GrvtCcxt + trigger client |
| `grvt_trigger_client.py` | Raw API for trigger orders + bulk_orders (OTOCO/OCO/OTO) |
| `receiver.py` | `supervisor` + per-strategy `_strategy_loop` (SSE consumer + status reconciliation) + `_process_fire` fire handler |
| `__main__.py` | Production entrypoint: wires real clients, traps SIGINT/SIGTERM, runs `asyncio.run(supervisor(...))` |
| `registry_cli.py` (top-level src) | CLI for add/list/cancel/alerts/ack |

## Data flow on a fire

1. Auto evaluates condition; transitions to true.
2. **Live SSE is the sole order-placement path.** The per-strategy task receives `event: query.triggered\ndata: <json>\n\n` on the open stream. The JSON carries `eventId` (canonical dedupe key per `docs.elfa.ai/auto/notifications`), `queryId`, `eventType`, `timestamp`, `channel`, `trigger`, `evaluation`, `action`. After emitting the trigger the stream closes (single-fire by design).
3. Fire handler (`_process_fire`) runs:
 - INSERT OR IGNORE into `fires` keyed by `eventId`. Duplicate -> no-op, return early.
 - SELECT strategy by `query_id`. If missing, alert `unknown_strategy`, bail.
 - If `strategy.status != 'active'`, log silently and bail (suppresses duplicate-fire noise).
 - Spawn daemon thread to send `trigger_received` Telegram alert (non-blocking).
 - Fetch `current_mid` via `executor.fetch_mid_price`.
 - Run guardrails (`check_guardrails`).
 - If `strategy.leverage` is set, call `executor.set_leverage` (best-effort).
 - Call `executor.place_entry_with_tpsl(...)`. Internally:
 - Compute TP/SL absolute prices from current_mid + percentages.
 - Round to instrument tick_size.
 - Build parent + TP + SL as Order dataclasses with EIP-712 signatures.
 - POST to `https://trades.grvt.io/full/v2/bulk_orders` with `{sub_account_id, orders: [...]}`.
 - On success: update `fires.outcome='placed'`, set `strategies.status='fired'`, emit `order_placed` and `tpsl_armed` alerts.
 - On any error path: update `fires.outcome='grvt_error'`, emit categorized alert (`insufficient_margin`, `grvt_set_leverage`, `manual_intervention_required`, etc.).

## Strategy lifecycle

Documented Auto status set (`docs.elfa.ai/auto/agent-quickstart`,
`v-2-auto.tag`):

- **Live**: `active`
- **Unsupported by this bot**: `recurring` (documented live status, rejected locally)
- **Terminal**: `triggered`, `expired`, `cancelled`, `failed`

```
       active ----- fire (success) ------> fired
         |                                  ^
         |                                  |
         +----- fire (terminal grvt) -------+
         |
         +----- Auto expiresIn elapsed ---> expired
         |
         +----- user runs cancel ---------> cancelled
         |        (POST /v2/auto/queries/:id/cancel)
         |
         +----- Elfa marks failed --------> failed
         |
         +----- terminal status detected --> (manual_intervention_required)
                  on poll-query while         alert if executions occurred
                  receiver was offline        while we were not on SSE
```

When poll-query reports a terminal remote status, the supervisor reconciles the local status and emits an alert. It does NOT replay missed executions through the order path (see "REST is status-only" below). The per-strategy SSE task exits cleanly on the next reconcile cycle.

Single-fire by design. `recurring` is documented by Elfa as a live status, but this bot rejects it to local `failed` because it cannot safely dedupe repeated fires across SSE `eventId` and poll-query `exec_xxx` namespaces.

## Notification channels

The receiver is the sole emitter of alerts. Every alert is first written to the SQLite `alerts` table (the source of truth), then optionally pushed to Telegram if both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set. The receiver consumes SSE on the query id directly; it does not depend on any notification actions Builder Chat may have embedded in the query definition.

The three alerts a normal fire produces:

1. `trigger_received` (background thread): immediately on accepted notification, before order placement
2. `order_placed`: after successful entry submission
3. `tpsl_armed`: after TP and SL are confirmed (or `manual_intervention_required` if either failed)

Two delivery channels read from the registry:

- **In-chat (via `AGENTS.md`).** Generated `AGENTS.md` instructs the agent to run `python src/registry_cli.py alerts --pending` on every session start and surface unacked alerts before doing anything else. After surfacing, the agent asks the user to say `ack <id>` or `ack all`; then it runs `python src/registry_cli.py ack <id-or-all>`. Agents that support session-start or per-prompt hooks can wire `scripts/show_pending_alerts.sh` for automatic injection - see your agent's docs.
- **Telegram (optional, real-time push).** When configured, `AlertWriter.emit()` calls `telegram_sender.send()` synchronously after the registry write. When unconfigured, it is silently skipped - no error, no retry queue. Telegram exceptions never bubble up; they're logged as warnings so a flaky bot can't break order placement.

This dual design means the user always gets alerts in chat (free, no extra credentials), and optionally also gets push notifications on their phone for real-time visibility while away from the agent.

## Idempotency

`eventId` (canonical per `docs.elfa.ai/auto/notifications`) is the PK of `fires`. INSERT OR IGNORE means a duplicate SSE delivery of the same event becomes a no-op insert and the handler returns early. At-least-once SSE delivery becomes exactly-once order placement.

## REST is status-only

`GET /v2/auto/queries/:id` returns an `executions` array whose elements are internal Athena execution records keyed by `executions[i].id` (`exec_xxx`). This is a **different identifier namespace** from the SSE `eventId` (`evt_xxx`). The bot does not attempt to dedupe SSE fires against poll-query executions because the two namespaces would never collide and the same trigger would be processed twice -- once via SSE, once via poll-query replay. Instead, poll-query is used strictly for status reconciliation: the local strategy status is synced when remote terminal status is observed, and if executions were reported while the receiver was offline the user gets a `manual_intervention_required` alert to review GRVT manually.

Trade-off accepted: restart-during-fire = miss one trade. Mitigated operationally by running the receiver under systemd / a PaaS auto-restarter.

## Real-money safety primitives

In layered order:

1. Per-strategy `max_notional_usd` cap.
2. Strategy-vs-receiver `env` match.
3. `status='active'` gate (silent log-only on retry of fired/cancelled strategies).
4. Symbol existence is enforced by GRVT itself: `fetch_mid_price` (and downstream order placement) fail loudly on unknown instruments and surface as `grvt_other` / `grvt_error` alerts. The user-facing safeguard is authoring-time verification - the agent calls `GrvtCcxt.fetch_market(symbol)` before creating the strategy and refuses to proceed if GRVT doesn't list it.
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

Each can be added later without changing the core architecture.
