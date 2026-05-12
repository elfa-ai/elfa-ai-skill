# Elfa EQL reference

EQL (Elfa Query Language) is the JSON DSL inside the `query` field of a `POST /v2/auto/queries` body.

**The agent does not author or hand-edit EQL.** Builder Chat (`POST /v2/auto/chat`) is the only authority: it produces the `conditions` and `actions` blocks from the user's natural-language prompt, and the bot passes Builder Chat's response through to `POST /v2/auto/queries` unchanged. This file is a reference for *understanding* what Builder Chat returned — explaining operators to the user, sanity-checking that the EQL matches user intent before showing the plan, answering "what does this condition mean?" — not a spec to write against. If Builder Chat's output is wrong, re-prompt with a clearer description rather than editing the JSON.

## Skeleton

```json
{
  "title": "<short title shown in notifications>",
  "description": "<longer human-readable description>",
  "query": {
    "conditions": { "AND": [ /* one or more leaves */ ] },
    "actions": [
      {
        "stepId": "step_1",
        "type": "notify",
        "params": { "channel": "telegram" }
      }
    ],
    "expiresIn": "24h"
  }
}
```

The bot does not read the `actions` block; it consumes triggers via SSE (`GET /v2/auto/queries/:id/stream`). Whatever actions Builder Chat returns flow through unchanged to `POST /v2/auto/queries`. Telegram is still sent by the receiver (not by Auto) when configured.

## Top-level rules

- `conditions` root must be `AND` or `OR`.
- Nest groups up to depth 3.
- Maximum 10 leaf conditions total.
- `expiresIn`: one of `1h`, `2h`, `4h`, `8h`, `12h`, `24h`, `2d`, `3d`, `5d`, `7d`. Default 24h.
- `actions`: chainable; the bot does not read this field. Builder Chat fills it based on the user prompt framing; passthrough is fine.

## Condition sources

### `price`

| Method | Args | Returns |
|---|---|---|
| `current` | `symbol` | current price |
| `change` | `symbol`, `period` | percent change over period |
| `high` | `symbol`, `period` | high in period |
| `low` | `symbol`, `period` | low in period |
| `volume` | `symbol`, `period` | USD volume in period |

Symbols are bare tickers (e.g. `BTC`, `SOL`, `HYPE`). NOT GRVT-style `SOL_USDT_Perp`.

### `ta` (technical analysis)

| Method | Required args | Optional args | Returns |
|---|---|---|---|
| `rsi` | `symbol`, `timeframe` | `period` (default 14) | RSI 0-100 |
| `macd_value` | `symbol`, `timeframe` | (none) | MACD line |
| `macd_signal` | `symbol`, `timeframe` | (none) | MACD signal line |
| `macd_histogram` | `symbol`, `timeframe` | (none) | MACD histogram |
| `bbands_upper` | `symbol`, `timeframe` | `period` (default 20) | upper Bollinger Band |
| `bbands_middle` | `symbol`, `timeframe` | `period` (default 20) | middle Bollinger Band |
| `bbands_lower` | `symbol`, `timeframe` | `period` (default 20) | lower Bollinger Band |
| `ema` | `symbol`, `timeframe`, `period` | (period required) | exponential MA |
| `sma` | `symbol`, `timeframe`, `period` | (period required) | simple MA |
| `atr` | `symbol`, `timeframe` | `period` (default 14) | average true range |
| `stoch_k` | `symbol`, `timeframe` | (none) | stochastic %K |
| `stoch_d` | `symbol`, `timeframe` | (none) | stochastic %D |
| `cci` | `symbol`, `timeframe` | `period` (default 20) | CCI |
| `willr` | `symbol`, `timeframe` | `period` (default 14) | Williams %R |

`timeframe` accepts: `1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `8h`, `12h`, `1d`.

Critical TA rules:
- `ema` and `sma` require `period`; it is NOT optional.
- `period` must be a JSON number (`14`), never a string (`"14"`).
- Use `period`, not `length`.

### `cron`

| Method | Args | Description |
|---|---|---|
| `once` | `period` | True at first eval at-or-after creation+period |
| `onceRemainTrue` | `period` | True on first due eval and stays true |
| `every` | `period` | True at each period interval |

### `llm`

| Method | Args | Description |
|---|---|---|
| `athena_condition` | `query`, `period`, `speed?` | LLM-evaluated natural-language condition |

The `query` field is a natural-language question. Elfa's LLM (codename "Athena") evaluates it against social and market context.

`speed` defaults to fast (5 credits per evaluation). `expert` is more expensive but more thorough.

### Scheduling period for cron and llm

Documented minimum is `1h`. **Empirically as of 2026-05-06 the validator accepts `5m` for `llm.athena_condition`** even though the docs say 1h. The runtime appears to evaluate at the requested cadence. Use this with care; LLM evaluation is metered (around 12 credits per eval at 5m). A 24h strategy at 5m cadence costs around 3,500 credits.

## Operators

`>`, `<`, `>=`, `<=`, `==`, `!=`, `crosses_above`, `crosses_below`.

`crosses_above` and `crosses_below` require previous-state tracking and only fire on transition. They are the right choice for "RSI dips below 30" semantics. `<` and `>` evaluate state at each eval, so they fire continuously while true (and immediately on creation if already true).

For the bot's single-fire architecture, both behave the same way (the strategy fires once and is consumed), but `crosses_*` is more accurate for transition language and avoids immediate-fire surprises.

## Dynamic comparisons

`value` can reference another live data source instead of a literal:

```json
{
  "source": "price",
  "method": "current",
  "args": { "symbol": "ETH" },
  "operator": "crosses_above",
  "value": {
    "source": "ta",
    "method": "bbands_upper",
    "args": { "symbol": "ETH", "timeframe": "4h" }
  }
}
```

Useful for "ETH crosses above its 4h Bollinger upper band" without hardcoding the band's value.

## Examples

### RSI dip on 1h

```json
{
  "title": "BTC RSI oversold on 1h",
  "description": "Buy entry signal when 1h RSI dips below 30",
  "query": {
    "conditions": { "AND": [
      { "source": "ta", "method": "rsi",
        "args": { "symbol": "BTC", "timeframe": "1h", "period": 14 },
        "operator": "crosses_below", "value": 30 }
    ]},
    "actions": [{ "stepId": "step_1", "type": "notify",
      "params": { "channel": "telegram" }}],
    "expiresIn": "24h"
  }
}
```

### Multi-condition confluence

```json
{
  "conditions": { "AND": [
    { "source": "ta", "method": "stoch_k",
      "args": { "symbol": "HYPE", "timeframe": "1h" },
      "operator": "<", "value": 20 },
    { "source": "ta", "method": "rsi",
      "args": { "symbol": "HYPE", "timeframe": "1h", "period": 14 },
      "operator": ">", "value": 50 },
    { "source": "ta", "method": "macd_histogram",
      "args": { "symbol": "HYPE", "timeframe": "1h" },
      "operator": ">", "value": 0 }
  ]}
}
```

All three must be true at the same eval. This is a classic oversold-bounce-with-trend-confirmation pattern.

### Social/LLM signal

```json
{
  "conditions": { "AND": [
    { "source": "llm", "method": "athena_condition",
      "args": {
        "query": "Has the X account @example posted in the past 10 minutes about trader sentiment from the past 48 hours?",
        "period": "5m"
      },
      "operator": "==", "value": true }
  ]}
}
```

## Validation

Always validate before creating. The bot's `ElfaClient.validate_query()` method calls `POST /v2/auto/queries/validate` and returns `{valid, errors, warnings, simulationLlmCallsEstimate, estimatedCredits, wouldTriggerNow}`. If `valid: false`, surface the errors to the user; do not call `create_query`.

## Convention: keep query content ASCII-only

Use only ASCII characters in `title`, `description`, and any other free text in the query body. Replace em-dashes (U+2014) with parens, hyphens, colons, or commas. Project-wide convention for ASCII-only output.
