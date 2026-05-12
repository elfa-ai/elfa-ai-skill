---
name: elfa-grvt-bot
description: Set up a self-hosted bot that bridges Elfa AUTO conditions (RSI, MACD, price, LLM athena_condition, any EQL) to GRVT perpetual futures execution with atomic OTOCO take-profit and stop-loss. Use whenever the user wants to install or deploy an Elfa-to-GRVT trading bot, place trades from Elfa AUTO triggers, set up automated entry plus TP/SL on GRVT perps, author strategies driven by RSI/MACD/stochastic/price/LLM signals, build a receiver that turns Elfa fires into signed GRVT orders, or hook X social signals to live trades. Ships the full project source (FastAPI receiver, EIP-712 signer, SQLite registry, Telegram alerts, CLI, tests) plus references encoding every production gotcha. User just needs API credentials for Elfa, GRVT, Telegram. Trigger even when only one side is mentioned (e.g. "Elfa AUTO trade execution" or "GRVT TP/SL via API") because every piece flows through this same project.
---

# elfa-grvt-bot

A self-hosted automated trading bot. The user describes strategies in natural language to the agent; Elfa AUTO evaluates conditions; an always-on FastAPI receiver places GRVT perpetual futures orders with atomic TP and SL when conditions fire.

This skill bundles the full project under `assets/source/`. Drop it into the user's environment, fill in API credentials, and they have a working bot identical to the reference deployment.

## Architecture in one paragraph

Two surfaces. **Authoring** happens in an agent chat session: the user describes a strategy, the agent builds an Elfa EQL query, validates it, creates it (API-key-authenticated POST), and writes a row to a local SQLite registry that maps `query_id` to the order spec. **Execution** happens in an always-on FastAPI receiver: when Elfa fires, the receiver receives the unsigned webhook (Elfa Auto delivers without a signature), looks up the strategy by `query_id`, fetches the current mark price, runs guardrails, and submits one atomic OTOCO bulk-order to GRVT containing the parent (entry) plus TP (limit, reduce-only) plus SL (trigger, reduce-only). Every notable event (trigger received, order placed, TP/SL armed, errors) lands in the local registry as the source of truth. Alerts surface through an in-chat registry check for agents that support project instructions and optionally through Telegram (real-time push if `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set; silently skipped otherwise).

## When to use this skill

Trigger when the user's request relates to any of:

- Setting up, installing, or bootstrapping the bot from scratch
- Authoring a new strategy (RSI / MACD / stochastic / price / LLM-condition triggers, or any combination)
- Cancelling or listing existing strategies
- Diagnosing a webhook that fired but did not place an order
- Authoring a strategy on a new symbol (the agent verifies it exists on GRVT during authoring; no allowlist to maintain)
- Migrating from cloudflared dev tunnel to a PaaS deploy
- Fixing or extending the OTOCO order placement code (see `references/grvt-api.md`)
- Investigating a Telegram alert that says `manual_intervention_required`
- Anything that involves both Elfa AUTO and GRVT in the same flow

If the user only mentions one side (e.g. just an Elfa AUTO query, or just a GRVT order), use this skill anyway because the reference patterns and gotchas are the cleanest source of truth for either side standalone.

## Active orchestration: do not stop until the user is "good to go"

When this skill is triggered for setup ("install the bot", "get this running", "set me up"), act as the orchestrator. Drive the install end to end. Do not return control to the user mid-flow with docs to read; walk them through every blocker until the system is in the ready state.

### Operational transparency: announce the plan, then narrate progress

**Before doing anything else**, post a single message to the user listing the milestones you will hit and a rough sense of where time will be spent. This sets expectations and lets them say "actually skip Telegram" up front. Use this template (adapt wording, but keep the structure):

> Setting up the elfa-grvt-bot. Here's the plan:
>
> 1. **Gather credentials** — Elfa API key, GRVT API key + private key + trading account ID, optionally Telegram bot token + chat ID. (You'll paste these into chat; I won't echo them back.)
> 2. **Bootstrap install** — copy source, create venv, install deps, run tests, validate .env. (Mostly automated; ~1–2 min.)
> 3. **Start the receiver** — FastAPI server on `localhost:8000`.
> 4. **Open a public tunnel** — `cloudflared` quick tunnel; capture the public URL into `.env`.
> 5. **Verify end to end** — health check the tunnel, optionally ping Telegram.
>
> I'll mark each milestone as we go. First up: credentials.

**At each milestone**, post a short progress callout so the user always knows where they are. Use round-number percentages keyed to the 5 milestones above (20% / 40% / 60% / 80% / 100%) — not the 9 internal bootstrap phases. Examples:

> Credentials saved. **(20% complete — bootstrap install next, ~1–2 min.)**

> Bootstrap exited clean, dependencies installed, env validated. **(40% complete — starting the receiver.)**

> Receiver healthy on localhost:8000. **(60% complete — opening the tunnel.)**

> Tunnel up, public URL captured into .env. **(80% complete — final end-to-end check.)**

> All checks passed. **(100% — ready.)**

If something fails and you have to retry, say so explicitly ("hit a snag on the tunnel — retrying, still around 60%") rather than silently looping. Bootstrap itself prints `[N% complete] phase X/9: ...` banners — feel free to surface those raw if it speeds things up, but the user-facing percentages above are the ones to lead with.

**Definition of "good to go":**
1. Bootstrap script (`scripts/bootstrap.py`) has been run and exited zero.
2. `~/elfa_grvt_bot/.env` has all required env vars filled (no blank values for ELFA / GRVT credentials). Telegram vars are OPTIONAL: if both are set, real-time push is enabled; if either is missing, alerts go in-chat only.
3. Receiver responds `{"ok":true}` on `http://localhost:8000/healthz`.
4. Cloudflared tunnel is running and responding `{"ok":true}` on the public URL captured in `.env`.
5. The user has been told they can now describe a strategy in chat.

Until all five are true, keep working. If a step fails, diagnose and try again. Do not say "now you do X" and stop; you do X, or guide the user through it inline, then verify and continue.

**The orchestration loop:**

1. Run `python3.11 <skill-path>/scripts/bootstrap.py` using the actual path where this skill is installed.
2. If it exits zero with the "Bootstrap complete" banner, jump to the readiness check.
3. If it exits with `env incomplete`, walk the user through credential gathering (next subsection). After they share each value, write it directly into `~/elfa_grvt_bot/.env` using the Edit tool. Never echo a secret back to the user in your response. Then re-run bootstrap.
4. If it exits with any other error, read the relevant log (`receiver.log` or `cloudflared.log` in the target dir) and address the specific cause. Do not guess. Common ones are documented in `references/troubleshooting.md`.
5. Repeat until bootstrap succeeds.
6. Run the readiness check (next subsection) and confirm to the user.

### Walking the user through credential gathering

When bootstrap reports missing env vars, address each one in order. For each:
- State which credential is missing.
- Give the exact URL or app where to get it.
- Walk through the click path inside that page or app.
- Tell the user how to identify the value (length, format).
- Ask them to paste the value into chat.
- Write it into `.env` immediately via the Edit tool. **Do not echo the value back in any subsequent message.** Just say "saved".
- Move on to the next missing var.

The walk-throughs themselves are in `references/setup.md` sections 3-5. Read that file before starting credential gathering so you have the URLs and steps in context.

Order of operations (least painful first):
1. **Elfa credentials** (free signup, single API key from the developer portal).
2. **GRVT credentials** (requires a funded account; this project is **prod-only** and refuses to start with any other `GRVT_ENV`. Confirm with the user that they have a funded prod account before they paste credentials).
3. **Telegram bot token + chat id** (OPTIONAL). Ask the user explicitly whether they want Telegram push alerts. In-chat registry alerts work without Telegram, so this is purely "do you want a phone notification too?" If they decline, leave both vars blank in `.env` and skip Telegram setup entirely. If they accept, walk them through `@BotFather` → `/newbot`, save the token, and continue:

For Telegram specifically (only if the user opted in), after they get the bot token, run the `getUpdates` curl call yourself via the Bash tool to extract `chat_id` (saves the user a manual step):

```bash
TOKEN=<bot_token>
curl -s "https://api.telegram.org/bot$TOKEN/getUpdates" | python -m json.tool
```

Tell the user "send your new bot any message in Telegram" before you run this; otherwise `getUpdates` returns an empty `result` array.

For GRVT, before the user pastes the private key, confirm with them: "This is live money on GRVT prod (the only env this bot supports). Continue?" If they want a sandbox first, the answer is "no, this project doesn't run on testnet"; they would need to fork the source and re-enable testnet in `Config`.

### Readiness check

Once bootstrap succeeds:

```bash
# 1. .env has every REQUIRED key set (Telegram is optional, not in this list)
grep -E '^(ELFA_API_KEY|GRVT_API_KEY|GRVT_PRIVATE_KEY|GRVT_TRADING_ACCOUNT_ID|RECEIVER_PUBLIC_URL|REGISTRY_DB_PATH)=' ~/elfa_grvt_bot/.env | grep -v '^[^=]*=$'
# every line should print "VAR=value", none with empty value

# 2. local receiver
curl -s http://localhost:8000/healthz
# {"ok":true}

# 3. public tunnel (read URL from .env)
URL=$(grep '^RECEIVER_PUBLIC_URL=' ~/elfa_grvt_bot/.env | cut -d= -f2-)
curl -s "$URL/healthz"
# {"ok":true}

# 4. (only if Telegram is configured) send a Telegram ping so the user sees push works
TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' ~/elfa_grvt_bot/.env | cut -d= -f2-)
CHAT=$(grep '^TELEGRAM_CHAT_ID=' ~/elfa_grvt_bot/.env | cut -d= -f2-)
if [ -n "$TOKEN" ] && [ -n "$CHAT" ]; then
  curl -s -X POST "https://api.telegram.org/bot$TOKEN/sendMessage" \
       -d "chat_id=$CHAT&text=elfa_grvt_bot setup complete - ready to send you notifications"
fi
```

If all required checks pass, tell the user. If Telegram is configured, say:

> Setup complete. Receiver, tunnel, in-chat alerts, and Telegram are all live. You can now describe a trading strategy and I will create it. For example: "Long 0.5 SOL_USDT_Perp at 20x when 1h RSI dips below 30, TP 1.5%, SL 1%."

If Telegram is NOT configured, say:

> Setup complete. Receiver, tunnel, and in-chat alerts are live (Telegram skipped, alerts will land in chat each turn). You can now describe a trading strategy and I will create it. For example: "Long 0.5 SOL_USDT_Perp at 20x when 1h RSI dips below 30, TP 1.5%, SL 1%."

Then stand by. The next request likely is a strategy description, and you should follow `references/strategy-authoring.md` (or `~/elfa_grvt_bot/AGENTS.md`, which is the same flow shipped into the working directory).

If any required readiness check fails, diagnose and fix; do not declare ready until receiver, tunnel, and required env vars are all healthy.

## Setup walkthrough (first-time install)

### Step 1: Run the bootstrap script

The skill ships an end-to-end orchestrator at `scripts/bootstrap.py` that does almost everything: copies the source, creates a venv, installs deps, runs the test suite, validates env vars, installs `cloudflared` if missing (via `brew` on macOS), starts the receiver in the background, starts the tunnel in the background, captures the public URL, writes it to `.env`, and verifies the wire end to end.

The user only has to:
1. Run the bootstrap once.
2. Fill in API credentials when bootstrap reports the env is incomplete.
3. Re-run bootstrap.

When this skill triggers on a fresh system, your default action should be to run `python3.11 <skill-path>/scripts/bootstrap.py` for the user. Use the actual path where the skill is unpacked.

```bash
python3.11 <skill-path>/scripts/bootstrap.py
```

The script defaults to `~/elfa_grvt_bot/` as the working directory; pass `--target <path>` to override. It is idempotent: rerunning detects an already-running receiver and cloudflared and reuses them where possible.

On the first run with an empty `.env`, the script prints the missing variables and exits. The user fills them in, then runs the script again. On the second run it picks up where it left off.

If anything in the script fails (e.g. dependency install error, cloudflared install error, receiver crash on boot), it prints the relevant log and exits non-zero. Look at `~/elfa_grvt_bot/receiver.log` and `~/elfa_grvt_bot/cloudflared.log` for details, then re-run after fixing.

After a successful run the receiver is healthy on `localhost:8000`, cloudflared is exposing it at the public URL, and `.env` has `RECEIVER_PUBLIC_URL` set. The user can open their preferred agent in `~/elfa_grvt_bot` and start authoring strategies immediately.

To stop everything: `bash ~/elfa_grvt_bot/teardown.sh` (the bootstrap drops this script too).

### Step 2: Manual setup (only if bootstrap is not an option)

If for some reason the user prefers manual installation, the steps are below. Otherwise skip this section.

```bash
mkdir -p ~/elfa_grvt_bot
cp -R <skill-path>/assets/source/. ~/elfa_grvt_bot/
cd ~/elfa_grvt_bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Tests should report `100+ passed`. If they do not, something is wrong with the install. See `references/troubleshooting.md`.

Then continue with credential gathering, running the receiver, and starting cloudflared as documented in `references/setup.md`.

### Step 3: Gather API credentials

The user needs:

| Var | Source |
|---|---|
| `ELFA_API_KEY` | Elfa developer portal |
| `GRVT_API_KEY` | grvt.io UI: Settings, API Keys |
| `GRVT_PRIVATE_KEY` | grvt.io UI: same page (the EVM private key paired with the API key) |
| `GRVT_TRADING_ACCOUNT_ID` | grvt.io UI: shown next to the API key (numeric sub-account id) |
| `TELEGRAM_BOT_TOKEN` | OPTIONAL. `@BotFather` on Telegram (`/newbot`) |
| `TELEGRAM_CHAT_ID` | OPTIONAL. Send your bot any message, then `curl https://api.telegram.org/bot<token>/getUpdates` and read `result[0].message.chat.id` |

**Important detail**: as of 2026-05-08 Elfa's `/v2/auto/*` mutations no longer require HMAC signing — API-key auth alone is sufficient. Inbound webhooks are also delivered unsigned, so the receiver does not verify signatures. `ELFA_API_KEY` is the only Elfa secret needed. See `references/elfa-webhooks.md`.

### Step 4: Populate `.env` and re-run bootstrap

After Step 3, edit `~/elfa_grvt_bot/.env` and fill in every key from the credentials table. `GRVT_ENV` is locked to `prod`; do not change it. There is no symbol allowlist: the agent verifies each new symbol against GRVT's `fetch_market` during authoring, and if it doesn't exist, says so and stops.

Leave `RECEIVER_PUBLIC_URL` blank; bootstrap will fill it in.

Re-run the bootstrap:

```bash
python3.11 <skill-path>/scripts/bootstrap.py
```

It now finds all required env vars, runs through install (idempotent if already done), starts the receiver, starts cloudflared, captures the URL into `.env`, and verifies the wire.

### Step 5: Verify end to end

Run the smoke test in `assets/source/docs/SMOKE_TEST.md`. It opens a tiny ($5+ notional) market position with TP and SL, then has the user manually unwind. This proves every link in the chain works.

## Strategy authoring flow

When an agent session is opened in the user's working directory, the project's generated `AGENTS.md` drives the authoring flow. At a high level, when the user describes a strategy in chat:

1. Read pending alerts first (`python src/registry_cli.py alerts --pending`). Surface any unacked alerts at the top of the response.
2. Forward the user's description to Elfa Builder Chat (`POST /v2/auto/chat`, body field `message`, API-key auth). Take ONLY the `conditions` block from the response and discard whatever `actions` block Builder Chat returned — it will be `market_order`/`limit_order`/`telegram` and violates the strict rule below. Hand-edit the conditions if Builder Chat got them wrong (see `references/elfa-eql.md`).
3. Ask the user for any GRVT order params they did not volunteer: symbol (verify via `GrvtCcxt.fetch_market(symbol)` from the grvt-trading skill before continuing — if it raises, tell the user "GRVT doesn't have that token" and stop), size, order type, optional limit price, optional leverage, optional time-in-force, `max_notional_usd` cap, optional `tp_pct` and `sl_pct`.
4. Build the create body yourself: pair the conditions from step 2 with EXACTLY ONE action — a `webhook` action targeting `<RECEIVER_PUBLIC_URL>/auto/events`. Validate it via `POST /v2/auto/queries/validate`.
5. Show the full plan and wait for an explicit "yes".
6. On approval, `POST /v2/auto/queries` with the same body. Then `python src/registry_cli.py add ...` to register locally.

Specifics, defaults, and constraints are in `references/strategy-authoring.md`.

## Things to never do

- **Never use em-dashes** in chat output, code, commits, alerts, or any external API content. Replace with parentheses, colons, commas, or hyphens. Project-wide convention for ASCII-only output.
- **Never ask Elfa Auto to execute trades directly.** The only allowed action types in queries this project creates are `webhook` (and only the webhook action targeting our receiver). Never use `/v2/auto/exchanges`, never use `market_order` or `limit_order` action types, never add `telegram` actions on the Auto side (the receiver sends Telegram itself).
- **Never set up `I_UNDERSTAND_REAL_MONEY=yes` or any equivalent gate.** This project is prod-only. The safety layer is the explicit per-strategy "yes" in chat before activation, and the per-strategy `max_notional_usd` cap.
- **Never rely on session memory for live position state.** Before reporting positions, balance, or open orders, poll GRVT live (`fetch_positions`, `fetch_balance`, `fetch_open_orders`). Local registry holds strategy metadata only.
- **Never write secrets into the registry or any file that gets committed.** `.env` is in `.gitignore`.

## Reference layout

| File | When to read |
|---|---|
| `references/setup.md` | Detailed setup walkthrough; if the quick start above is insufficient |
| `references/elfa-eql.md` | Building an EQL query (operators, condition sources, depth limits) |
| `references/elfa-webhooks.md` | Inbound webhook details: actual headers, unsigned delivery, ms timestamps |
| `references/grvt-api.md` | OTOCO via `full/v2/bulk_orders`, EIP-712 signing, deprecated endpoints, tick alignment |
| `references/strategy-authoring.md` | The full chat flow when a user describes a strategy |
| `references/troubleshooting.md` | Common errors and what they mean |
| `assets/source/AGENTS.template.md` | Agent-neutral project instruction template; `bootstrap.py` copies it to `AGENTS.md` in the user's project |

## Helper scripts

| Script | Purpose |
|---|---|
| `scripts/bootstrap.py` | **Default.** End-to-end orchestrator: copy source, venv + install + tests, env-var validation, cloudflared install, receiver + tunnel start, URL capture, end-to-end verify |
| `scripts/install.sh` | Manual install only: copy source + venv + deps + tests |
| `scripts/start_receiver.sh` | Manually start receiver after `.env` is sourced |
| `scripts/start_tunnel.sh` | Manually start cloudflared |
| `teardown.sh` (written into target dir by bootstrap.py) | Stop the receiver and cloudflared started by bootstrap |

`bootstrap.py` is the path you should default to. The other scripts exist for users who want to run individual steps manually.
