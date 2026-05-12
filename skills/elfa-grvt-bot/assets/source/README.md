# elfa_grvt_bot

Elfa AUTO → GRVT trading bot. You describe strategies in natural language to
your agent; Elfa Auto evaluates conditions; an always-on FastAPI receiver places
the GRVT orders when conditions fire.

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

- **Elfa**: `ELFA_API_KEY` (from your Elfa developer portal). All
  `/v2/auto/*` calls authenticate with this key alone; webhook delivery
  to the receiver is unauthenticated, so keep your `RECEIVER_PUBLIC_URL`
  private. The notional cap on each strategy is the last line of defense.
- **GRVT**: `GRVT_API_KEY`, `GRVT_PRIVATE_KEY`, `GRVT_TRADING_ACCOUNT_ID` (from
  GRVT → Settings → API Keys). `GRVT_ENV=prod` is the project default.
- **Telegram**: `TELEGRAM_BOT_TOKEN` (from `@BotFather`), `TELEGRAM_CHAT_ID`
  (your personal chat with the bot).
- **Receiver**: `RECEIVER_PUBLIC_URL` set after step 3. `REGISTRY_DB_PATH`
  defaults to `./registry.db`. Symbol validity is delegated to GRVT
  itself; if you author a strategy on a symbol GRVT doesn't list,
  the agent flags it during authoring (and the receiver's mid-price
  fetch fails fast at fire time as a backstop).

Source it:
```bash
set -a && source .env && set +a
```

### 3. Run the receiver locally with cloudflared

In one terminal:
```bash
python -m elfa_grvt_bot
```

In another:
```bash
cloudflared tunnel --url http://localhost:8000
```

Cloudflared prints something like
`https://random-words-1234.trycloudflare.com`. Set that as
`RECEIVER_PUBLIC_URL` in your shell and re-run any new strategy creations
from chat against the new URL. (For long-lived tunnels, set up a named
cloudflared tunnel.)

### 4. Author a strategy

Open your preferred agent (Claude Code, OpenCode, Cursor, Codex, or any
agent that supports `AGENTS.md`) in this directory.

Tell the agent what you want, e.g.:
> "Buy 0.05 BTC perp market on GRVT prod when 1h RSI dips below 30. Cap
> notional at $4000."

The agent follows the flow in `AGENTS.md`: forwards to Elfa Builder Chat,
validates EQL, asks for any missing params, shows the full plan, and waits
for your "yes" before creating the Auto query and writing the registry row.

### 5. Watch it fire

When the condition triggers, you'll see two Telegram messages: the Auto
native alert, and the receiver's order receipt. If anything fails, you'll
get an alert on Telegram and the agent will surface it next time you open
a session in this directory.

## Production deploy (Fly.io / Railway / Render)

The receiver is environment-agnostic — same code, same env vars. Two
operational requirements:

1. The PaaS must expose a stable public HTTPS URL — set this as
   `RECEIVER_PUBLIC_URL`.
2. Mount a persistent volume for `registry.db` (otherwise active strategies
   are lost on redeploy).

Example Fly.io:
```bash
fly launch  # creates fly.toml, app
fly secrets set $(grep -v '^#' .env | xargs)  # set env vars
fly volumes create registry --size 1
# add [mounts] block in fly.toml: source = "registry", destination = "/data"
# update REGISTRY_DB_PATH=/data/registry.db
fly deploy
```

Then re-authoring or migrating existing strategies: update
`RECEIVER_PUBLIC_URL` in your shell and let the agent rewire any
strategies you want to keep.

## Manual smoke test (one-time, on production wiring)

After everything is set up — and before relying on it — run the smoke test
in `docs/SMOKE_TEST.md`.
