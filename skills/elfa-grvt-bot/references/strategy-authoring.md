# Strategy authoring flow

How an agent turns a user's natural-language strategy description into an active Auto query plus a registered local strategy.

This file documents the chat flow. The same instructions ship as `assets/source/AGENTS.template.md`, which `bootstrap.py` copies to `AGENTS.md` in the user's project root. Any agent that supports the AGENTS.md convention (most do) will pick it up on session start in that directory.

## On every session start in the project

Before responding to anything else, run:

```bash
python -m pip show elfa-grvt-bot >/dev/null 2>&1 || pip install -e ".[dev]" --quiet
python src/registry_cli.py alerts --pending
```

If there are unacknowledged alerts, surface them at the top of the response in this format:

> N unacknowledged alert(s):
> - **#<id>** [category] <message> (strategy=<query_id>)
>
> Say `ack <id>` to clear, or `ack all` to clear all.

If the user says `ack <id>` or `ack all`, run `python src/registry_cli.py ack <id-or-all>`.

If there are no pending alerts, say nothing about alerts and continue.

## When the user describes a strategy

1. **Forward the description to Elfa Builder Chat** (`POST /v2/auto/chat`) with the user's natural-language prompt. API-key auth (`x-elfa-api-key` header).

   Before calling Builder Chat, prepend `Notify me when:` to the user's description unless the description is already framed as a notification request (e.g. it already starts with "notify me", "alert me", "tell me when", etc.). This is non-negotiable: the Elfa-side action must be `notify` (or another notify-style action), never an execute / trade action. Do not double-wrap if the user's description is already framed that way.

   Builder Chat returns a draft query with `conditions` and `actions`. **Pass the response through unchanged.** Do not strip or replace the actions block. The receiver consumes triggers via SSE on the query id, not via the actions block, so the actions content is irrelevant to the bot's execution path.

   **Never hand-write or hand-edit the `conditions` block.** Builder Chat is the only authority for EQL. If its conditions don't match the user's intent (wrong operator, missing leg of an AND, wrong timeframe, etc.), re-prompt Builder Chat with a clearer description or ask the user to rephrase; do not edit the JSON yourself. `references/elfa-eql.md` documents the syntax for understanding what Builder Chat returned, not as a spec for you to write against.

2. **Ask the user for any GRVT order params they did not volunteer**:
   - Symbol on GRVT (e.g. `SOL_USDT_Perp`). Before continuing, verify the symbol exists by calling `GrvtCcxt.fetch_market(symbol)` from the grvt-trading skill. If it raises or returns nothing, tell the user "GRVT doesn't have that token" and stop. There is no static allowlist; GRVT itself is the source of truth.
   - Size in base-asset units (or notional in USD if the user prefers; convert)
   - Order type (market or limit; default market)
   - Limit price if limit order
   - Leverage (optional; usually account-default applies anyway)
   - Time-in-force (optional; default `GOOD_TILL_TIME`)
   - `max_notional_usd` cap (default a small buffer over expected notional)
   - `tp_pct` and `sl_pct` (optional; if either provided, the receiver arms an OTOCO bracket)

3. **Validate the EQL** via `POST /v2/auto/queries/validate`. Catches malformed queries before the cost.

4. **Show the user the full plan**:
   - The EQL conditions
   - The order spec (side, size, symbol, type, leverage)
   - Cap, env, expiry
   - Whether `wouldTriggerNow` is true (if so, flag that it will fire immediately)
   - For LLM strategies: the estimated credits

   Wait for an explicit "yes".

5. **On approval**:
   a. `POST /v2/auto/queries` with the validated body unchanged. Default `expiresIn` is `24h` unless the user requested otherwise.
   b. `python src/registry_cli.py add ...` with all the strategy params. The CLI inserts a row keyed by `query_id`.
   c. Confirm to the user with `query_id`, status `active`, and expiry.
   d. The receiver's supervisor polls the registry every ~5s and will open an SSE stream for the new query automatically. If the receiver is not running, the user needs to start it (`python -m elfa_grvt_bot`).

## Defaults this project uses

| Param | Default |
|---|---|
| `expiresIn` | `24h` |
| Order type | `market` |
| Time-in-force | `GOOD_TILL_TIME` |
| `tp_pct` / `sl_pct` | None (no OTOCO bracket) unless user provides |
| Max notional cap | (no default; ALWAYS ask user explicitly) |
| Actions | passthrough - whatever Builder Chat returns; not read by the bot |

## Operator selection

Default to the operator that best matches the user's intent. Common cases:

- "RSI dips below 30" -> `crosses_below 30` (transition; fires on the cross)
- "RSI is below 30" -> `<` (state; fires whenever true at eval)
- "Price breaks above 100k" -> `crosses_above 100000`
- "Price greater than X" or "X+" -> `>`
- "When @account posts about Y" -> `llm.athena_condition` (==, value=true)

When ambiguous, ask. If the current value is already on the trigger side (e.g. price already > X), surface that explicitly so the user knows the strategy will fire immediately on creation if they pick the state operator.

## Position stacking

By default, strategies stack: if a strategy fires while the user has an existing open position on the same symbol, the receiver places another entry plus its own TP/SL bracket. GRVT will net the positions; total reduce-only across both brackets must remain at-or-below the position size (GRVT enforces with code 2402).

If the user wants wait-for-flat semantics ("don't fire if I already have a position"), this is not implemented in v1 but is a known follow-up. Offer it as a future enhancement.

## Strict rules

These are non-negotiable for this project:

- **No em-dashes** (U+2014) anywhere: chat, code, commits, alert text, API request bodies. Replace with hyphens, parens, colons, or commas. Project-wide convention for ASCII-only output.

- **Never author or hand-edit EQL.** Builder Chat is the only authority. If its output is wrong, re-prompt with a clearer description; do not patch the JSON. `references/elfa-eql.md` is a reference for understanding Builder Chat's output, not a spec to write against.

- **Don't manipulate Builder Chat's actions block.** Pass it through unchanged. The receiver consumes triggers via SSE on the query id, not via the actions block.

- **Elfa-side action must always be notify-style** (`notify`, `telegram_bot`, etc.). Always prepend `Notify me when:` to the user's description before calling Builder Chat. Never use `/v2/auto/exchanges` or any exchange-execution action type. Order placement happens in our receiver via `grvt-pysdk`, full stop. Auto's role is conditions plus notification only.

- **`GRVT_ENV` is locked to `prod`** for this project. The receiver's `Config` rejects any other value at boot. Do NOT add `I_UNDERSTAND_REAL_MONEY=yes` or any equivalent gate. The safety layer is the explicit per-strategy "yes" in chat.

- **Always poll GRVT for live position state**, never rely on session memory. Before reporting a position, balance, or open order, run `c.fetch_positions(...)`, `c.fetch_balance()`, `c.fetch_open_orders(...)`. Local registry holds strategy metadata only; that is fine to read locally.

## Cancelling a strategy

If the user asks to cancel a strategy by `query_id`:

```bash
python src/registry_cli.py cancel <query_id>
```

This calls Elfa `POST /v2/auto/queries/:id/cancel` and updates the local registry status to `cancelled`. (Hard-deletion via `DELETE /v2/auto/queries/:id` is allowed only after cancel; the project intentionally does not delete so the strategy stays auditable.)

## Reading state

- Active strategies: `python src/registry_cli.py list --status active`
- All strategies: `python src/registry_cli.py list`
- Pending alerts: `python src/registry_cli.py alerts --pending`
- All alerts: `python src/registry_cli.py alerts`
- Live GRVT state: query GRVT directly via `pysdk.grvt_ccxt.GrvtCcxt`

## Things to NOT do

- Don't place orders directly via the grvt-trading skill from the chat session. The receiver owns order placement. Read-only operations (fetch_balance, fetch_market, fetch_ticker, fetch_positions) for sanity checks during authoring are fine.
- Don't commit secrets to the registry or any tracked file. `.env` is in `.gitignore`.
- Don't strip or replace Builder Chat's actions block. Pass it through unchanged. The receiver consumes SSE on the query id, not the actions block.
- Don't hand-write or hand-edit the `conditions` block ever. Builder Chat is the only authority for EQL. If its output is wrong, re-prompt with a clearer description; do not patch the JSON yourself.
- Don't ask Elfa to execute trades. The Elfa-side action must always be notify-style. Order placement is owned by our receiver.
- Don't skip validation. Always `validate_query` before `create_query`.
