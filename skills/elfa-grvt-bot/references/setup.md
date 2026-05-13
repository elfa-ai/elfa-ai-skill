# Setup walkthrough (detailed)

For when the SKILL.md quick start needs more depth.

## Prerequisites

- macOS or Linux. Windows works via WSL.
- Python 3.11+
- An Elfa developer account at `https://go.elfa.ai/claude-skills` (free signup).
- A funded GRVT account at `https://grvt.io`. Even small balances work; the bot will respect `max_notional_usd` per strategy.
- A Telegram account (OPTIONAL -- only needed if you want real-time push alerts on top of the in-chat channel).

## Step-by-step

### 1. Deploy source from the skill bundle

```bash
mkdir -p ~/elfa_grvt_bot
cp -R <skill-path>/assets/source/. ~/elfa_grvt_bot/
cd ~/elfa_grvt_bot
```

The skill bundles approximately 35 files (`pyproject.toml`, source, tests, docs, `AGENTS.template.md`). Total size around 400KB.

### 2. Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Confirm by running tests:

```bash
pytest -q
```

The full suite (around 100 tests) should pass. If a test fails, that is the first thing to fix; do not proceed.

### 3. Get Elfa credentials

1. Sign in at `https://go.elfa.ai/claude-skills`.
2. Generate an API key. Copy it.

You only need `ELFA_API_KEY`. Per `docs.elfa.ai/api/rest/auto-create-query-v-2`, HMAC signing is conditional on action type: trade-flavoured actions (`market_order`, `limit_order`) require HMAC; notify-style actions (`notify`, `telegram_bot`, `webhook`) do not. This bot only creates notify-style queries (the authoring flow prepends `Notify me when:` before calling Builder Chat), so API-key auth is always sufficient. Stream + validate are always API-key-only. See `elfa-sse.md` for the trigger-delivery protocol.

### 4. Get GRVT credentials

1. At `https://grvt.io`, navigate to Settings, then API Keys.
2. Create a new API key. You will see three values:
 - The API key string itself
 - An EVM private key paired with this API key (used for EIP-712 order signing)
 - A trading account ID (the sub-account this key is scoped to)
3. Copy all three.

You need:
- `GRVT_API_KEY`
- `GRVT_PRIVATE_KEY`
- `GRVT_TRADING_ACCOUNT_ID`

This project is prod-only. `GRVT_ENV=prod` is set in `.env.example` and the receiver's `Config` rejects any other value at boot. There is no testnet path. The user must have a funded GRVT prod account to use this bot.

### 5. Set up Telegram (OPTIONAL)

Telegram is optional. The bot has two alert channels:

- **In-chat (always on).** Generated `AGENTS.md` instructs the agent to surface pending alerts on every session start so the agent relays them to the user directly.
- **Telegram (optional, real-time push).** Only enabled when both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set in `.env`.

If you don't want Telegram push notifications, leave both vars blank in `.env` and skip this section. To enable Telegram:

1. In Telegram, message `@BotFather` and `/newbot`. Save the bot token.
2. Open your new bot in Telegram and send it any message (this is required for `getUpdates` to return data).
3. From a terminal:
   ```bash
   curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" | python -m json.tool
   ```
   Find `result[0].message.chat.id`. That is your `TELEGRAM_CHAT_ID`. Positive numbers are 1:1 chats; negative numbers (with `-100` prefix) are group chats.

If you opt in, you need:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

#### Optional: wire `show_pending_alerts.sh` for hook-driven alerts

The bundled `scripts/show_pending_alerts.sh` prints any unacked alerts on stdout and prints the ack command to run after relaying them. Agents that support session-start, pre-prompt, or shell hooks can call this script so alerts surface automatically without the agent having to remember `AGENTS.md`.

Examples:

- **Agent hooks** -- point your session-start or pre-prompt hook at `<project>/scripts/show_pending_alerts.sh`. The script self-locates its project root, reads `.env`, and exits silently if there is nothing to surface.

The `AGENTS.md` instruction (poll on every session start) remains the agent-neutral baseline; the hook above is a faster, turn-driven alternative for clients that support it.

### 6. Populate `.env`

```bash
cp .env.example .env
```

Edit `.env` and fill in all required values:

- `ELFA_API_KEY`
- `GRVT_API_KEY`, `GRVT_PRIVATE_KEY`, `GRVT_TRADING_ACCOUNT_ID`
- `REGISTRY_DB_PATH` (defaults to `./registry.db`)
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` if you opted in above

There is no symbol allowlist. GRVT itself is the source of truth for which symbols are tradable, and the authoring flow verifies each new symbol against `GrvtCcxt.fetch_market(symbol)` before creating the strategy. If GRVT doesn't list the symbol, the agent says so and stops. As a runtime backstop, the receiver's mid-price fetch will fail loudly with a `grvt_other` alert if a strategy somehow ends up pointing at a symbol GRVT doesn't have.

### 7. Start the receiver

```bash
source .venv/bin/activate
set -a && source .env && set +a
python -m elfa_grvt_bot
```

The receiver boots, authenticates to GRVT (you will see GRVT cookies refresh in the log), loads the instruments cache, and starts the strategy supervisor. There is no inbound HTTP server, no public URL, and no tunnel required. The receiver makes outbound SSE connections to Elfa -- one per active strategy -- and processes triggers as they arrive. The supervisor polls the registry every ~5s so newly authored strategies are picked up automatically.

Verify the receiver is running from another terminal:

```bash
pgrep -f elfa_grvt_bot
```

You should see one process. Alternatively, check the receiver's stdout for `supervisor started` followed by `spawning SSE task for <query_id>` for each active strategy.

If the receiver fails on `Config.load()` with a missing-env-var error, the `.env` is incomplete or not sourced. If GRVT auth fails, the credentials in step 4 are wrong or your IP is geo-blocked.

### 8. Run the smoke test

Follow `assets/source/docs/SMOKE_TEST.md`. It places a small live order with TP/SL and confirms every link in the chain (Auto evaluation, SSE delivery, GRVT execution, registry persistence, and Telegram push if configured).

### 9. Author your first real strategy

Open your preferred agent in the working directory (any agent that supports `AGENTS.md` or skills works).

Tell the agent what you want, for example:
> "Long 0.5 SOL_USDT_Perp at 20x leverage when 1h RSI dips below 30. TP 1.5%, SL 1%. Cap notional at $30."

The agent follows the flow in `AGENTS.md` (generated by `bootstrap.py` from `AGENTS.template.md`): forwards to Builder Chat, asks for any missing GRVT params, validates EQL, shows the full plan, waits for "yes", creates the Auto query, and writes the local registry row.

## Production deploy

The receiver is environment-agnostic: same code, same env vars, any host that can run a long-running Python process. It makes only outbound HTTPS connections (to Elfa for SSE streams, to GRVT for order placement, to Telegram for push). No inbound ports are needed.

Two operational requirements wherever you host:

1. The host must sustain a persistent, long-running process. PaaS options (Fly.io, Railway, Render) work; so does a plain VPS with systemd.
2. Mount a persistent volume for `registry.db`. Without this, redeploying loses all active strategies.

Example Fly.io recipe:

```bash
fly launch
fly secrets set $(grep -v '^#' .env | xargs)
fly volumes create registry --size 1
# in fly.toml:
#   [mounts]
#     source = "registry"
#     destination = "/data"
# in .env (or fly secrets):
#   REGISTRY_DB_PATH=/data/registry.db
fly deploy
```

Example systemd unit on a VPS (`/etc/systemd/system/elfa-grvt-bot.service`):

```ini
[Unit]
Description=Elfa GRVT bot receiver
After=network-online.target

[Service]
User=elfa
WorkingDirectory=/home/elfa/elfa_grvt_bot
EnvironmentFile=/home/elfa/elfa_grvt_bot/.env
ExecStart=/home/elfa/elfa_grvt_bot/.venv/bin/python -m elfa_grvt_bot
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Author strategies as usual -- the registry is the shared hand-off point between your authoring environment and the receiver process. If they write to different `registry.db` files, point both at the same database (or the same mounted volume path) so the receiver picks up new strategies.
