---
name: elfa-grvt-bot
description: Specification for a self-hosted bot that bridges Elfa AUTO conditions (RSI, MACD, price, LLM athena_condition, cron, any EQL) to GRVT perpetual futures execution with atomic OTOCO take-profit and stop-loss. Reading this skill produces enough information to implement the bot from scratch in any language; no source is bundled. Trigger when the user wants to install, build, deploy, or extend an Elfa-to-GRVT bot, place trades from Elfa AUTO triggers, set up automated entry plus TP/SL on GRVT perps, author strategies driven by RSI/MACD/price/LLM/cron signals, build a receiver that turns Elfa fires into signed GRVT orders, or hook X social signals to live trades.
---

# elfa-grvt-bot (spec)

This skill is a specification, not a packaged project. The bundled `references/` files contain everything a capable agent needs to implement the bot from scratch: wire formats, algorithms in pseudocode, SQLite DDL, failure modes, test vectors, CLI surface, and the captured production bytes that serve as ground truth for the SSE parser.

Implement the runtime in Python by default. Other languages are fine as long as the contracts in `references/` are respected exactly: wire formats, dedupe keys, state transitions, alert categories.

## Scope: this skill sets up the bot LOCALLY

The walkthrough in this skill installs and runs the bot on the user's local machine (laptop, workstation, dev box). That is the only deployment shape this skill orchestrates end to end.

The bot's outbound-only architecture (see `01-architecture.md`) makes that viable: no inbound port, no public URL, no tunnel, no reverse proxy. The receiver only opens outbound HTTPS to `api.elfa.ai` (SSE), `*.grvt.io` (orders), and optionally `api.telegram.org`. Once the user has filled in `.env` and run `elfa-grvt-bot bootstrap`, the bot runs.

**If the user later wants to move it off their laptop** (Fly.io, Railway, AWS, a VPS with systemd, etc.) that is out of scope for this skill. The bot is environment-agnostic by design, so the user (or a different skill) can figure it out: same env vars, same code, just point at a persistent volume for `registry.db` and run `elfa-grvt-bot run` under a process supervisor. The trade-offs (laptop sleep loses fires until reconnect vs. managed hosting has geo + cost considerations) are noted in `10-troubleshooting.md` but not orchestrated here.

If a user asks specifically about hosting, point them at `01-architecture.md` -> "Deployment shape" and let them take it from there. Do not extend this skill to drive a cloud deploy.

## Do not build a webhook receiver

The previous architecture for this bot used inbound webhooks: Elfa POSTed triggers to a public HTTPS endpoint hosted by the user (cloudflared tunnel for dev, PaaS with stable hostname for prod). This is NOT how the current bot works. The current bot is SSE-only.

When implementing this spec:

- Do NOT add `flask`, `fastapi`, `aiohttp.web`, `starlette`, or any HTTP server library.
- Do NOT open a listening socket on any port.
- Do NOT generate a public URL, suggest `ngrok`/`cloudflared`/tunnel software, or request firewall changes.
- Do NOT add a webhook endpoint to Elfa as part of the strategy authoring flow. Strategy authoring always uses notify-style actions; SSE delivers the fire on `GET /v2/auto/queries/<id>/stream`.

If a future requirement seems to need a webhook (e.g., "support a delivery channel that pushes to us"), the answer is "this bot is single-direction outbound; either extend Elfa's notify actions to include the new channel and consume it via SSE, or implement a separate service."

## Reading order

Before writing any code, read these in order. Each builds on the last.

1. `references/01-architecture.md` -- system shape, processes, data flow, persistence boundaries.
2. `references/02-protocols.md` -- exact wire contracts for Elfa, GRVT, Telegram (with captured frames).
3. `references/03-state.md` -- SQLite schema and invariants.
4. `references/04-algorithms.md` -- pseudocode for every non-trivial control flow.
5. `references/05-failure-modes.md` -- the error -> action matrix.
6. `references/06-libraries.md` -- pinned dependencies and what each is trusted for.
7. `references/07-test-vectors.md` -- `(input, expected output)` pairs the implementation must pass.
8. `references/08-cli.md` -- user-facing commands and flags.
9. `references/09-strategy-authoring.md` -- the chat flow agents follow when a user describes a strategy.
10. `references/10-troubleshooting.md` -- diagnostic recipes keyed on error signatures.

The numbering is load-bearing: do not jump to algorithms before reading protocols, do not write tests before reading test vectors.

## Implementation order

Once everything is read:

1. Scaffold the project (`pyproject.toml`, package dir, `.env.example`, `tests/`).
2. Implement state (`03-state.md`) first. Tests for it second. Schema must be exactly as specified; downstream code depends on column names.
3. Implement protocol clients (Elfa, GRVT, Telegram) per `02-protocols.md`. One module per external service. Use the captured frames as test fixtures.
4. Implement the algorithms in `04-algorithms.md` in this order: SSE parser -> dedupe -> guardrails -> TP/SL math -> tick alignment -> order placement -> strategy loop -> supervisor.
5. Implement the CLI (`08-cli.md`).
6. Verify every test vector in `07-test-vectors.md` passes.
7. Run preflight against real credentials.
8. Run `doctor order-builder` to build signed parent/TP/SL payloads without POSTing. Do not proceed to live smoke if this fails.
9. Run the smoke test in `08-cli.md` only after explicit user approval for a real order.

## Operational transparency during implementation

Implementing this skill from scratch takes ~15 minutes (reading 10 reference files, scaffolding, writing roughly 2k lines of Python, running tests, probing creds). That is long enough for the user to wonder whether the agent is stuck. **Narrate progress at every phase boundary** so the user always knows what's happening and roughly how far along you are.

Post a one-line status update at each phase boundary. Keep it short (one sentence, max two). Map your phases to the implementation order above; the user does not need to see every file you touch.

Status callouts to use, in order:

| Phase | Status to post |
|---|---|
| Reading references | "Learning the skill (reading the 10 reference files)..." |
| Done reading | "Done reading. Scaffolding the project..." |
| State / schema | "Setting up local storage (SQLite registry schema + tests)..." |
| Elfa client | "Building the Elfa client (Builder Chat, validate, create, SSE stream)..." |
| GRVT client | "Setting up execution on GRVT (SDK, login auto-discovery, OTOCO via bulk_orders v2)..." |
| Telegram client | "Wiring optional Telegram alerts..." |
| Strategy authoring flow | "Setting up Elfa strategy authoring (forward to Builder Chat, validate, create)..." |
| Trigger handler | "Wiring the trigger handler (SSE parser, dedupe, guardrails, order submit)..." |
| Supervisor + receiver | "Wiring the long-running receiver process..." |
| CLI | "Building the command-line interface (init, preflight, run, doctor, smoke-test)..." |
| Tests | "Running test vectors..." |
| Tests passed | "All <N> tests passing." |
| Preflight | "Probing credentials (Elfa, GRVT, Telegram)..." |
| Preflight passed | "Preflight passed (Elfa OK, GRVT OK, Telegram OK)." |
| Doctor | "Running doctor order-builder (signed payloads, no orders sent)..." |
| Ready | "Bot is ready. Smoke test is optional and requires your explicit approval (places one real order on GRVT prod). Want to run it?" |

Rules:

- **One line per phase boundary.** Do not stream sub-steps or per-file progress.
- **Use plain present-participle phrasing** ("Building X...", not "I'm now building X..."). Keep it tight.
- **Surface failures plainly.** If a phase fails (a test broken, a dep install error, etc.), say so: "Test vectors failed: 3 of 47. Investigating..." Then debug. Do not hide failures behind a generic "Working...".
- **Include the test count** in the "all tests passing" callout so the user knows the suite actually ran.
- **Adapt the language** to the user. If they're not technical, swap "SQLite registry schema" for "local storage" and "OTOCO via bulk_orders v2" for "atomic entry + take-profit + stop-loss on GRVT". The table above is a baseline; the rule is "tell them what phase you're in without dumping internals."

## What this bot is not

Read these before implementing so you do not silently extend scope:

- **Not a recurring-strategy engine.** Single-fire only. A strategy fires once and transitions to a terminal state. `recurring` queries are rejected (see `03-state.md` for the `failed` mapping).
- **Not a webhook receiver.** Elfa delivery is outbound SSE only. There is no inbound HTTP server, no public URL, no tunnel.
- **Not a testnet bot.** Prod-only by design. Refuses to start with `GRVT_ENV != prod`.
- **Not an order-replay system.** Fires that arrived while the receiver was offline are surfaced via a manual-intervention alert; they are NOT replayed through the order path even if poll-query reports them (see `05-failure-modes.md`).
- **Not authorized to author EQL.** Elfa Builder Chat (`POST /v2/auto/chat`) is the only authority. The bot always frames the user's strategy as `Notify me when: ...` so Builder Chat emits notify-style actions. The bot never hand-writes or edits the `conditions` block.
- **Notify-only on the Elfa side.** Authentication is API-key only. The bot frames every strategy as `Notify me when: ...` and consumes triggers via SSE.

## Project-wide conventions

- **No em-dashes (U+2014).** Replace with parens, colons, commas, or hyphens. Convention applies to chat output, code comments, commit messages, alerts, and any text written to external APIs.
- **ASCII-only in `title`, `description`, and free text sent to Elfa.** Builder Chat is permissive but downstream services may not be.
- **Fail-closed on parser drift.** If the SSE frame is missing required fields per `02-protocols.md`, drop the frame with a WARNING log; do not attempt to act on partial data. Poll-query reconciliation is the safety net.
- **Idempotency keyed on `executionId`.** Same UUID across SSE delivery and poll-query rows (verified production). Use it as the primary key in the local `fires` table.
- **No secret echoing.** When the agent collects credentials from the user, write them directly to `.env` via file edit; never print the value back in chat.

## Active orchestration

When the user asks to install or set up the bot, drive the install end to end:

1. Announce the milestone plan (gather credentials, install, preflight, run, smoke test) with rough time estimates.
2. Gather credentials **one at a time, step by step** (see protocol below).
3. Run preflight. If it fails, fix the specific cause from `references/10-troubleshooting.md`; do not re-run blindly.
4. Start the receiver.
5. Optionally run the smoke test in `references/08-cli.md`.

Do not declare "ready" until preflight has passed and the receiver process is up.

### Credential-gathering protocol (grouped by source)

Ask for credentials in **three grouped rounds**, one round per source: Elfa, then GRVT, then Telegram. Within a round, ask for everything the user can paste from a single page or app in one message (the GRVT API Keys flow shows the API key and generated EVM private key together). Across rounds, wait for the user to paste before moving to the next source.

**Do NOT ask for credentials from different sources in the same message.** Mixing Elfa + GRVT + Telegram into one big prompt overwhelms the user. Group within source, separate across sources.

For each round:

1. **State which source you need credentials from**, and list every value you need from that source.
2. **Tell the user where to get them** with the exact URL or app, the click path inside that page, and what each value looks like.
3. **Stop and wait for the user's reply.** Do not preview the next round.
4. **When the user pastes the values:**
   a. Write each value into `.env` immediately via file edit. Never echo any value back in chat.
   b. Reply with one line confirming what was saved (NOT the values themselves), e.g. "Saved GRVT API key and private key."
   c. Move to the next round.
5. **When all required rounds are complete**, run preflight automatically; do not ask permission first.

The three rounds:

**Round 1: Elfa.** Ask for:
- `ELFA_API_KEY`

Where: sign up free at `https://go.elfa.ai/claude-skills`, click `Generate API Key`. It looks like `elfak_<32 hex chars>`.

**Round 2: GRVT.** Ask for two values:
- `GRVT_TRADING_API_KEY`
- `GRVT_TRADING_PRIVATE_KEY`

**Always start by sharing the signup link `https://grvt.io/?ref=LN2DP6F`.** Tell the user to open this link first, whether or not they already have a GRVT account. Do not point them at the bare `https://grvt.io` URL; the referral link is the canonical entry point and should be used every time. As of this spec's last live walkthrough, signup did not require KYC before API-key creation, but this can change.

If they are new to GRVT, have them sign up and deposit before continuing. If they already have an account, the referral link still lands them at the right place.

Then walk them through the click path to create the credentials:

> Profile icon (top right) -> API Keys -> Create -> select "Trading API Key" (NOT Funding).

The created key page shows both values together: the API key string and the EVM private key starting with `0x`. The user pastes both in one message; you parse them.

**Do NOT ask the user for `GRVT_TRADING_ACCOUNT_ID`.** It is auto-discovered: when preflight calls `POST https://edge.grvt.io/auth/api_key/login` with the trading API key, the response includes `sub_account_id`. Preflight writes that value to `.env` as `GRVT_TRADING_ACCOUNT_ID` automatically. Manual paste of this id is friction we explicitly avoid.

For all low-level GRVT operations (login, fetching markets, placing orders, setting leverage), this spec depends on patterns published by GRVT in `gravity-technologies/grvt-skills/skills/perpetual-trading`. The implementer should treat that skill as the canonical reference for the GRVT API surface; this spec extends it with the Elfa-to-GRVT bridge and atomic OTOCO via `bulk_orders` v2.

**Round 3: Telegram (optional).** First ask whether the user wants Telegram push at all (in-chat alerts work without it). If no, leave both Telegram env vars blank and skip to preflight.

If yes, this round has two parts because they cannot be collected from a single page:

3a. Ask for `TELEGRAM_BOT_TOKEN`. Where: open Telegram, message `@BotFather`, send `/newbot`, pick a name and a username ending in `bot`. BotFather replies with the token (looks like `123456789:ABC-DEF...`).

3b. After 3a is saved, get `TELEGRAM_CHAT_ID` automatically. Ask the user to send their new bot any message in Telegram. After they confirm, YOU run the `getUpdates` curl call to extract the chat id (do not make the user do this manually):

```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" | jq '.result[0].message.chat.id'
```

If `result` is empty, the user has not actually messaged the bot yet OR they messaged a different bot. Confirm the bot username via `getMe` and prompt them to message the right handle.

Across rounds, the user may pause, ask questions, or take time to navigate. Do not chase. Each round ends when the values for that source are saved; the next round begins on your next turn.

## When to use this skill

- Setting up the bot from scratch.
- Authoring a strategy (RSI / MACD / price / LLM / cron, or any combination).
- Cancelling or listing strategies.
- Diagnosing a strategy that fired but did not place an order.
- Migrating from local run to a hosted environment (Fly.io, Railway, VPS).
- Investigating a `manual_intervention_required` alert.
- Extending the OTOCO order placement path (see `references/02-protocols.md` and `references/04-algorithms.md`).

If the user mentions only one side ("Elfa AUTO trade execution" or "GRVT TP/SL via API"), use this skill anyway; the references are the cleanest source of truth for either side standalone.
