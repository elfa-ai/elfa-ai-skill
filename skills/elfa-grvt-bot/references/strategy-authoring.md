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

1. **Forward the description to Elfa Builder Chat** (`POST /v2/auto/chat`) with the user's natural-language prompt. API-key auth (`x-elfa-api-key` header). Builder Chat returns a draft query containing both conditions and an actions block.

   **Use ONLY the `conditions` block.** Builder Chat does not know about this project's webhook-only rule and routinely returns `market_order`, `limit_order`, or `telegram` actions, which violate the strict rules below. **Discard the `actions` block in its entirety.** You will splice our own webhook action in step 5a.

   If Builder Chat returns conditions that don't match what the user asked for (wrong operator, missing leg of an AND, wrong timeframe), edit them by hand using `references/elfa-eql.md` as the spec. Builder Chat is fluent on common indicators (RSI, MACD, stochastic, price) but can be wrong on multi-source ANDs or LLM conditions.

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
   - Cap, env, expiry, webhook target
   - Whether `wouldTriggerNow` is true (if so, flag that it will fire immediately)
   - For LLM strategies: the estimated credits

   Wait for an explicit "yes".

5. **On approval**:
   a. `POST /v2/auto/queries`. Construct the body yourself: take the `conditions` block from step 1 (Builder Chat's output, optionally hand-edited) and pair it with EXACTLY ONE action — a `webhook` action with `params.url=<RECEIVER_PUBLIC_URL>/auto/events`. Whatever actions Builder Chat returned in step 1 are discarded. Do NOT add a `telegram` action on the Auto side; the receiver sends Telegram itself. Default `expiresIn` is `24h` unless the user requested otherwise.
   b. `python src/registry_cli.py add ...` with all the strategy params. The CLI inserts a row keyed by `query_id`.
   c. Confirm to the user with `query_id`, status `active`, and expiry.

## Defaults this project uses

| Param | Default |
|---|---|
| `expiresIn` | `24h` |
| Order type | `market` |
| Time-in-force | `GOOD_TILL_TIME` |
| `tp_pct` / `sl_pct` | None (no OTOCO bracket) unless user provides |
| Max notional cap | (no default; ALWAYS ask user explicitly) |
| Action | webhook only, target = `<RECEIVER_PUBLIC_URL>/auto/events` |

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

- **One webhook action per query.** Never add `telegram`, `notify`, `llm`, or any other action type. The receiver is the sole owner of order placement and Telegram messaging.

- **Never use `/v2/auto/exchanges`** or any exchange-execution action type. Order placement happens in our receiver via `grvt-pysdk`, full stop. Auto's role is conditions only.

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
- Don't pass Builder Chat's `actions` block through to `create_query`. It will contain `market_order`, `limit_order`, or `telegram` actions that the project forbids. Use only its `conditions`; splice in your own webhook action.
- Don't hand-write the `conditions` block from scratch when Builder Chat would do; it's fluent on common indicators (RSI, MACD, stochastic, price) and saves you from EQL syntax mistakes. Hand-edit only when Builder Chat got the conditions wrong.
- Don't skip validation. Always `validate_query` (with the spliced webhook action) before `create_query`.
