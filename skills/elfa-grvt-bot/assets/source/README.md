# elfa_grvt_bot

Elfa AUTO → GRVT trading bot. You describe strategies in natural language to
Claude; Elfa Auto evaluates conditions; a long-running outbound consumer
subscribes to per-query SSE streams and places GRVT orders when conditions
fire.

## Setup

### 1. Install

```bash
git clone <this-repo>
cd elfa_grvt_bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in:

- **Elfa**: `ELFA_API_KEY` (from your Elfa developer portal). HMAC is
  not needed because this bot only creates notify-style Auto queries
  (`Notify me when: ...`); trade-flavoured actions would require HMAC.
- **GRVT**: `GRVT_API_KEY`, `GRVT_PRIVATE_KEY`, `GRVT_TRADING_ACCOUNT_ID`
  (from GRVT → Settings → API Keys). `GRVT_ENV=prod` is the project default.
- **Telegram**: `TELEGRAM_BOT_TOKEN` (from `@BotFather`), `TELEGRAM_CHAT_ID`
  (your personal chat with the bot).
- **Receiver**: `REGISTRY_DB_PATH` defaults to `./registry.db`. Symbol
  validity is delegated to GRVT itself; if you author a strategy on a
  symbol GRVT doesn't list, Claude flags it during authoring (and the
  receiver's mid-price fetch fails fast at fire time as a backstop).

Source it:
```bash
set -a && source .env && set +a
```

### 3. Run the receiver

```bash
python -m elfa_grvt_bot
```

That's it - no tunnel, no public URL, no inbound HTTP server. The receiver
makes an outbound SSE connection to Elfa for each active strategy in the
registry and processes triggers as they arrive. New strategies authored
while it's running get picked up automatically (the supervisor polls the
registry every ~5s). Trigger Ctrl-C to stop.

### 4. Author a strategy

In a separate terminal, open Claude Code in this directory:
```bash
claude
```

Tell Claude what you want, e.g.:
> "Notify me when 1h RSI on BTC dips below 30. Buy 0.05 BTC perp market on
> GRVT prod, cap notional at $4000."

Claude follows the flow in `CLAUDE.md`: forwards to Elfa Builder Chat as a
"Notify me when…" prompt, validates the EQL, asks for any missing GRVT
order params, shows the full plan, and waits for your "yes" before creating
the Auto query and writing the registry row.

### 5. Watch it fire

When the condition triggers, the receiver sees the SSE notification, runs
guardrails, places the GRVT order (with TP/SL if configured), and pushes
two Telegram messages: a "trigger received" ping in parallel with order
placement, then an "order placed" / "tpsl_armed" confirmation. Failures
get a Telegram alert too, and Claude will surface anything you missed at
the start of your next chat session.

## Production deploy (Fly.io / Railway / Render / VPS)

The receiver is environment-agnostic. Two operational requirements:

1. The host needs outbound HTTPS to `api.elfa.ai` and `trades.grvt.io` -
   no inbound ports.
2. Mount a persistent volume for `registry.db` (otherwise active
   strategies are lost on redeploy).

Example Fly.io:
```bash
fly launch  # creates fly.toml, app
fly secrets set $(grep -v '^#' .env | xargs)
fly volumes create registry --size 1
# add [mounts] in fly.toml: source = "registry", destination = "/data"
# set REGISTRY_DB_PATH=/data/registry.db
fly deploy
```

Author strategies via Claude as usual - the registry is the shared
hand-off point. If your authoring environment and the receiver write to
different `registry.db` files, they need to agree on the same database for
the receiver to know about new strategies.

## Manual smoke test

Before relying on it, run the smoke test in `docs/SMOKE_TEST.md`.
