# Setup walkthrough (detailed)

For when the SKILL.md quick start needs more depth.

## Prerequisites

- macOS or Linux. Windows works via WSL.
- Python 3.11+
- `cloudflared` (Cloudflare's quick-tunnel CLI). On macOS, `bootstrap.py` will install it via `brew` automatically. On Linux, install manually from `https://github.com/cloudflare/cloudflared/releases`.
- An Elfa developer account at `https://go.elfa.ai/claude-skills` (free signup).
- A funded GRVT account at `https://grvt.io`. Even small balances work; the bot will respect `max_notional_usd` per strategy.
- A Telegram account (OPTIONAL — only needed if you want real-time push alerts on top of the in-chat channel).

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

You only need `ELFA_API_KEY`. All `/v2/auto/*` calls authenticate with this key alone (HMAC signing was removed in May 2026 after Elfa stopped requiring it). Inbound webhook delivery is also unsigned, so there is no signing secret to provision. See `elfa-webhooks.md`.

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

### 6. Populate `.env`

```bash
cp .env.example .env
```

Edit `.env`. Fill in everything except `RECEIVER_PUBLIC_URL` (you will get that in Step 8).

There is no symbol allowlist. GRVT itself is the source of truth for which symbols are tradable, and the authoring flow verifies each new symbol against `GrvtCcxt.fetch_market(symbol)` before creating the strategy. If GRVT doesn't list the symbol, the agent says so and stops. As a runtime backstop, the receiver's mid-price fetch will fail loudly with a `grvt_other` alert if a strategy somehow ends up pointing at a symbol GRVT doesn't have.

### 7. Start the receiver

In one terminal:

```bash
source .venv/bin/activate
set -a && source .env && set +a
python -m elfa_grvt_bot
```

The receiver boots, authenticates to GRVT (you will see GRVT cookies refresh in the log), loads the instruments cache, and starts listening on `http://localhost:8000`.

Sanity check from another terminal:

```bash
curl -s http://localhost:8000/healthz
# {"ok":true}
```

If the receiver fails on `Config.load()` with a missing-env-var error, the `.env` is incomplete or not sourced. If GRVT auth fails, the credentials in step 4 are wrong or your IP is geo-blocked.

### 8. Start cloudflared

In a second terminal:

```bash
cloudflared tunnel --url http://localhost:8000
```

Cloudflared prints a banner with your URL:
```
https://your-tunnel-name.trycloudflare.com
```

That is your `RECEIVER_PUBLIC_URL`. Add it to `.env`:
```
RECEIVER_PUBLIC_URL=https://your-tunnel-name.trycloudflare.com
```

`set -a && source .env && set +a` in the agent session terminal so authoring uses the new URL.

The receiver itself does NOT use `RECEIVER_PUBLIC_URL`; only the strategy-authoring flow reads it (to fill in the webhook target on each Auto query). The receiver continues to listen on `:8000`.

Verify the tunnel works end to end:

```bash
curl -s "$RECEIVER_PUBLIC_URL/healthz"
# {"ok":true}
```

If it fails, your local DNS may not yet have propagated the new trycloudflare hostname. This does not affect Elfa; Elfa's edge resolves global DNS fine.

### 9. Run the smoke test

Follow `assets/source/docs/SMOKE_TEST.md`. It places a small live order with TP/SL and confirms every link in the chain (Auto evaluation, webhook delivery, signature handling, GRVT execution, registry persistence, and Telegram push if configured).

### 10. Author your first real strategy

Open your preferred agent in the working directory (any agent that supports `AGENTS.md` or skills works — Claude Code, OpenCode, Cursor, Codex, etc.).

Tell the agent what you want, for example:
> "Long 0.5 SOL_USDT_Perp at 20x leverage when 1h RSI dips below 30. TP 1.5%, SL 1%. Cap notional at $30."

The agent follows the flow in `AGENTS.md` (generated by `bootstrap.py` from `AGENTS.template.md`): forwards to Builder Chat, asks for any missing GRVT params, validates EQL, shows the full plan, waits for "yes", creates the Auto query, and writes the local registry row.

## Production deploy (move off cloudflared quick-tunnels)

`trycloudflare.com` URLs are ephemeral. For sustained use, move to one of:

- A named cloudflared tunnel with a stable hostname.
- A small PaaS: Fly.io, Railway, or Render. The receiver is environment-agnostic; same code, same env vars, just a different host.
- Self-hosted on a VPS with a reverse proxy.

Two operational requirements wherever you host:

1. The receiver must be reachable at a stable HTTPS URL set as `RECEIVER_PUBLIC_URL`.
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
# in .env:
#   REGISTRY_DB_PATH=/data/registry.db
fly deploy
```

After deployment, update `RECEIVER_PUBLIC_URL` in your local shell so the next strategy authored points at the deployed URL. Existing strategies on Elfa still point at the old cloudflared URL; cancel and re-create them.
