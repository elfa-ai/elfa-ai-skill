# elfa_grvt_bot — agent session bootstrap

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

1. **Forward the description to Elfa Builder Chat verbatim** (`POST
   /v2/auto/chat`, body field `message`, API-key auth). **Always** frame
   the prompt as a notification request: prepend `Notify me when:` to the
   user's description before calling Builder Chat. The Elfa-side action
   must be notify-style (`notify`, `telegram_bot`, etc.), never an
   execute/trade action. If you already prepended once, do not double-wrap.

   Builder Chat's response is a draft query with `conditions` and
   `actions`. **Pass the response through unchanged.** Do not strip or
   replace the actions block. **Never hand-write or hand-edit the
   `conditions` block.** Builder Chat is the only authority for EQL — if
   its conditions don't match the user's intent (wrong operator, missing
   leg of an AND, wrong timeframe), re-prompt Builder Chat with a clearer
   description or ask the user to rephrase. Do not patch the JSON yourself.
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
3. Validate via `POST /v2/auto/queries/validate`.
4. Show the user the full plan (EQL + order spec + cap + env + expiry) and
   wait for an explicit "yes." Default `expiresIn` is `24h` unless the user
   requested otherwise.
5. On approval:
   a. `POST /v2/auto/queries` with the validated body unchanged.
   b. `python src/registry_cli.py add ...` with the returned `query_id`,
      symbol, side, amount, order_type, price, leverage, time_in_force,
      reduce_only flag, max_notional_usd, eql_json (the validated EQL),
      and the optional `--tp-pct` / `--sl-pct` flags if requested. `env`
      is hardcoded to `prod` (no flag).
   c. Confirm to the user with `query_id` and expiry.
   d. The receiver's supervisor polls the registry every ~5s; the new
      strategy gets an SSE stream opened automatically. If the receiver
      isn't running, the user needs to start it (`python -m elfa_grvt_bot`).

## How fires arrive (SSE + REST backfill)

The receiver (`python -m elfa_grvt_bot`) maintains one SSE connection per
`active` strategy. When Elfa's conditions evaluate true, the SSE stream
emits an `event: notification` with the trigger payload (including
`executionId`, used as the dedupe key), then closes. The receiver
processes the fire and the strategy transitions to `fired`.

If the receiver was offline when a strategy triggered, on next startup the
supervisor does a `GET /v2/auto/queries/:id` for each locally-active
strategy. Any executions returned that aren't already in our `fires` table
get replayed through the same handler. This is the safety net that
recovers from crashes/restarts.

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
the terminal status on its next reconcile/backfill and exits cleanly.

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
  session — the receiver owns order placement. Reading market data
  (`fetch_balance`, `fetch_market`, `fetch_ticker`) for sanity checks
  during authoring is fine.
- Don't try to wire up webhooks, public URLs, or cloudflared tunnels. SSE
  is outbound; no inbound HTTP needed.
- Don't write secrets into the registry or any committed file.
