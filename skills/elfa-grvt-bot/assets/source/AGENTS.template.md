# elfa_grvt_bot — agent session bootstrap

This project is the Elfa AUTO → GRVT trading bot.

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

1. Forward the description to Elfa Builder Chat (`POST /v2/auto/chat`,
   API-key auth, body field is `message`).
   Builder Chat returns a draft query containing both `conditions` and
   `actions`. **Use ONLY the `conditions` block.** Builder Chat does
   not know our webhook-only rule and routinely returns
   `market_order`/`limit_order`/`telegram` actions; **discard the
   `actions` block** and splice in our webhook action in step 5a. If
   the conditions look wrong (wrong operator, missing leg, wrong
   timeframe), hand-edit them using `references/elfa-eql.md` (in the
   skill bundle) as the spec.
2. Show the conditions to the user and ask for any GRVT order params
   they didn't volunteer: symbol on GRVT side (verify it exists by
   calling `GrvtCcxt.fetch_market(symbol)` from the grvt-trading skill;
   if it raises or returns nothing, tell the user "GRVT doesn't have
   that token" and stop), size, order type, price (if limit), leverage
   (optional), time-in-force (optional), `max_notional_usd` cap, and
   optional `tp_pct` / `sl_pct` (take-profit / stop-loss percentages, e.g.
   `1.5` (= 1.5%)). If the user opts in to either, TP/SL are computed
   from the current mid at trigger time (slippage-tolerant) and submitted
   atomically with the entry as one OTOCO `full/v2/bulk_orders` request,
   so the position is never open without its protective bracket.
3. Validate via `POST /v2/auto/queries/validate` (API-key auth).
4. Show the user the full plan (EQL + order spec + cap + env + expiry +
   webhook target) and wait for an explicit "yes."
5. On approval:
   a. `POST /v2/auto/queries` (API-key auth). Body must include EXACTLY ONE
      action:
      - a `webhook` action with `params.url = <RECEIVER_PUBLIC_URL>/auto/events`

      Auto's role ends at delivering the webhook to our receiver. **Do NOT
      add a `telegram` action on the Auto side** — Telegram notifications
      are sent by our receiver (one "trigger received" ping when the
      webhook arrives, then a second message with the order outcome).
      Single sender = consistent narrative + no double pings if Auto and
      the receiver disagree.

      Default `expiresIn` is `24h` unless the user requested otherwise.

      **STRICT RULE — never ask Elfa to execute trades directly.** Order
      placement is OWNED by our receiver via `grvt-pysdk`, full stop.
      NEVER include any action that touches `/v2/auto/exchanges` or asks
      Auto to place orders on a connected exchange. Auto's role is
      conditions only. If you find yourself reaching for an "execute" or
      "trade" or "order" action type, stop and re-read this paragraph.
   b. `python src/registry_cli.py add ...` with the returned `query_id`,
      symbol, side, amount, order_type, price, leverage, time_in_force,
      reduce_only flag, max_notional_usd, eql_json (the validated EQL),
      and the optional `--tp-pct` / `--sl-pct` flags if the user
      requested them. `env` is hardcoded to `prod` (no flag).
   c. Confirm to the user with `query_id` and expiry.

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
registry to `status=cancelled`. (Hard-deletion via `DELETE` is intentionally
not done so the strategy stays auditable.)

## Reading state

- Active strategies: `python src/registry_cli.py list --status active`
- All strategies: `python src/registry_cli.py list`
- Pending alerts: `python src/registry_cli.py alerts --pending`
- All alerts: `python src/registry_cli.py alerts`

## Don't do these

- Don't place orders directly via the grvt-trading skill from this chat
  session — the receiver owns order placement. Reading market data
  (`fetch_balance`, `fetch_market`, `fetch_ticker`) for sanity checks during
  authoring is fine.
- Don't ask Elfa Auto to execute trades, and don't add a `telegram`
  action on the Auto side. The only action in any query we author is
  `webhook` → our receiver. The receiver sends Telegram messages itself,
  so Auto doesn't need to. Never use `/v2/auto/exchanges` or any
  exchange-execution action type.
- Don't pass Builder Chat's `actions` block through to `validate_query`
  or `create_query`. Builder Chat will hand back `market_order`,
  `limit_order`, or `telegram` actions; those violate the rule above.
  Use only Builder Chat's `conditions`; build the actions block
  yourself with our single webhook action.
- Don't write secrets into the registry or any committed file.
