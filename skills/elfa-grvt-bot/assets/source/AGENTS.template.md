# elfa_grvt_bot - agent session bootstrap

This project is the Elfa AUTO → GRVT trading bot.

The bot listens to Elfa Auto triggers via **per-query Server-Sent Events**
(`GET /v2/auto/queries/:id/stream`). There is no inbound HTTP server, no
public URL, no webhook, and no cloudflared tunnel. The receiver is a
long-running outbound consumer started with `python -m elfa_grvt_bot`.

## On every session start in this project

Before responding to anything else, run:

```bash
python -m pip show elfa-grvt-bot >/dev/null 2>&1 || pip install -e ".[dev]" --quiet
python src/registry_cli.py alerts --pending
```

If there are unacknowledged alerts, **surface them at the top of the response
before doing anything else**. Use this format:

> N unacknowledged alert(s):
> - **#<id>** [category] <message> (strategy=<query_id>)
>
> Say `ack <id>` to clear, or `ack all` to clear all.

If the user says `ack <id>` or `ack all`, run:

```bash
python src/registry_cli.py ack <id-or-all>
```

If there are no pending alerts, say nothing about alerts and continue.

## Strategy authoring flow

When the user describes a strategy:

1. **Forward the description to Elfa Builder Chat** (`POST /v2/auto/chat`,
   body field `message`, API-key auth). **Always** frame the prompt as a
   notification request: prepend `Notify me when:` to the user's
   description. If you already prepended once, do not double-wrap.

   Builder Chat returns `{sessionId, response, title, reasoning, planIds}`.
   The EQL draft is embedded as a fenced JSON code block inside the
   markdown `response` field. **Extract that JSON code block verbatim**
   and use it as the `query` field of the create-query body. Take
   `title` from Builder Chat (or write a short one from the user's
   intent) and a 1-2 sentence `description` so notifications are
   self-explanatory weeks later.

   **Never hand-write or hand-edit the EQL `conditions` block.** Builder
   Chat is the only authority. If the draft doesn't match the user's
   intent (wrong operator, missing leg of an AND, wrong timeframe),
   re-prompt with `sessionId` set for context, or ask the user to
   rephrase. Do not patch the JSON yourself.

   The action block Builder Chat emits (`notify` / `telegram_bot`) does
   not affect this bot's execution path: triggers are consumed via SSE
   on the query id, not via the actions block. Pass actions through
   unchanged anyway.
2. Ask the user for any GRVT order params they didn't volunteer:
 - Symbol on GRVT (verify it exists by calling
     `GrvtCcxt.fetch_market(symbol)` from the grvt-trading skill; if it
     raises, tell the user "GRVT doesn't have that token" and stop)
 - Size, order type, optional limit price, optional leverage, optional
     time-in-force, `max_notional_usd` cap
 - Optional `tp_pct` / `sl_pct` (take-profit / stop-loss percentages,
     e.g. `1.5` = 1.5%). If the user opts in, TP/SL are computed from the
     current mid at trigger time and submitted atomically with the entry
     as one OTOCO `full/v2/bulk_orders` request.
3. Validate via `POST /v2/auto/queries/validate`. Response is
   `{valid, errors, warnings, estimatedCost, simulationLlmCallsEstimate}`.
   If `valid` is false, surface the errors and stop. Validate is free.
4. Show the user the full plan (EQL + order spec + cap + env + expiry +
   estimated credits) and wait for an explicit "yes." Default
   `expiresIn` is `24h` unless the user requested otherwise.
5. On approval:
   a. `POST /v2/auto/queries`. Response is `{queryId, status, cost}`.
   b. `python src/registry_cli.py add ...` with the returned `queryId`
      (stored as local `query_id`), symbol, side, amount, order_type,
      price, leverage, time_in_force, reduce_only flag, max_notional_usd,
      eql_json (the validated EQL), and the optional `--tp-pct` /
      `--sl-pct` flags if requested. `env` is hardcoded to `prod`.
   c. Confirm to the user with `queryId` and expiry.
   d. The receiver's supervisor polls the registry every ~5s; the new
      strategy gets an SSE stream opened automatically. If the receiver
      isn't running, the user needs to start it (`python -m elfa_grvt_bot`).

## How fires arrive (live SSE only)

The receiver (`python -m elfa_grvt_bot`) maintains one SSE connection per
live strategy (status `active` or `recurring`). When Elfa's conditions
evaluate true, the SSE stream emits `event: query.triggered` with the
canonical event payload from `docs.elfa.ai/auto/notifications`
(top-level fields: `eventId`, `eventType`, `version`, `timestamp`,
`queryId`, `channel`, `trigger`, `evaluation`, `action`). The receiver
keys idempotency on `eventId` and processes the fire; the strategy
transitions to `fired`.

If the receiver was offline when a strategy triggered, the fire is not
recovered automatically: SSE eventIds (`evt_xxx`) and poll-query
executions (`exec_xxx`) are different identifier namespaces per the
documented schemas, so we cannot dedupe across channels safely. The
supervisor still calls `GET /v2/auto/queries/:id` on startup for status
reconciliation, and if it sees the remote query already in a terminal
status with executions reported, it emits
`manual_intervention_required` so the user reviews the GRVT side
manually. Run the receiver under systemd / a PaaS auto-restarter to keep
the offline window minimal.

## Environment defaults (project-specific)

`GRVT_ENV` defaults to `prod` for this project, overriding the grvt-trading
skill's testnet-default. **Never** add a `I_UNDERSTAND_REAL_MONEY=yes` gate
or any equivalent. The safety layer is the explicit per-strategy "yes" in
chat before activation.

## Cancelling a strategy

If the user asks to cancel a strategy by `query_id`:

```bash
python src/registry_cli.py cancel <query_id>
```

This calls Elfa `POST /v2/auto/queries/:id/cancel` and updates the local
registry to `status=cancelled`. The receiver's per-strategy SSE task notices
the terminal status on its next poll and exits cleanly.

## Reading state

- Active strategies: `python src/registry_cli.py list --status active`
- All strategies: `python src/registry_cli.py list`
- Pending alerts: `python src/registry_cli.py alerts --pending`
- All alerts: `python src/registry_cli.py alerts`

## Don't do these

- Don't author or hand-edit EQL. Builder Chat is the only authority. If
  its output is wrong, re-prompt with a clearer description or ask the
  user to rephrase. Don't patch the JSON yourself.
- Don't ask Elfa to execute trades. Always prepend `Notify me when:` so
  Builder Chat returns a notify-style action. Order placement is owned
  by our receiver, not Elfa.
- Don't place orders directly via the grvt-trading skill from this chat
  session - the receiver owns order placement. Reading market data
  (`fetch_balance`, `fetch_market`, `fetch_ticker`) for sanity checks
  during authoring is fine.
- Don't try to wire up webhooks, public URLs, or cloudflared tunnels. SSE
  is outbound; no inbound HTTP needed.
- Don't write secrets into the registry or any committed file.
