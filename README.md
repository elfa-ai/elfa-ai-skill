# Elfa AI Skills

Real-time crypto social intelligence and automated condition-engine skills for AI agents. Track trending tokens, surface narratives, search mentions, run market analysis, build automated trigger-based workflows, and wire Elfa Auto signals into GRVT execution.

Works with **Claude Code**, **OpenCode**, **Cursor**, **GitHub Copilot**, **Codex**, and any tool that supports the [Agent Skills](https://agentskills.io) standard.

## Installation

### Quick (any agent)

```bash
npx skills add elfa-ai/skills
```

Installs both skills via the [Skills CLI](https://github.com/vercel-labs/skills); works with Claude Code, Cursor, Windsurf, Codex, and [40+ other agents](https://github.com/vercel-labs/skills#supported-agents). Add `--global` to install for all projects. Run `npx skills update` to refresh.

### Manual (spec-compliant `.agents/skills/`)

For any agent that supports the [Agent Skills](https://agentskills.io) standard:

```bash
git clone https://github.com/elfa-ai/skills elfa-skills
cd elfa-skills

# Project-level (current directory only)
mkdir -p .agents/skills && cp -r skills/. .agents/skills/

# OR user-level (all projects)
mkdir -p ~/.agents/skills && cp -r skills/. ~/.agents/skills/
```

### Manual (agent-specific paths)

If your agent doesn't yet scan `.agents/skills/`, copy into its native skills directory:

| Agent | Project-level | User-level |
|---|---|---|
| Spec-compliant | `.agents/skills/` | `~/.agents/skills/` |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| OpenCode | `.opencode/skills/` | `~/.config/opencode/skills/` |

```bash
# Example: install all skills into Claude Code globally
mkdir -p ~/.claude/skills && cp -r skills/. ~/.claude/skills/
```

<details>
<summary>Cursor (uses rule format)</summary>

Cursor doesn't read `SKILL.md` directly; wrap each skill in an always-on rule:

```bash
for SKILL in skills/*/; do
  NAME=$(basename "$SKILL")
  mkdir -p .cursor/rules
  {
    echo '---'
    echo "description: \"$NAME\""
    echo 'alwaysApply: true'
    echo '---'
    cat "$SKILL/SKILL.md"
  } > ".cursor/rules/$NAME.mdc"
done
```

</details>

<details>
<summary>GitHub Copilot</summary>

```bash
for SKILL in skills/*/SKILL.md; do
  cat "$SKILL" >> .github/copilot-instructions.md
  echo >> .github/copilot-instructions.md
done
```

</details>

<details>
<summary>Codex (AGENTS.md)</summary>

```bash
for SKILL in skills/*/SKILL.md; do
  cat "$SKILL" >> AGENTS.md
  echo >> AGENTS.md
done
```

</details>

<details>
<summary>Claude Desktop (attach file)</summary>

1. Start a conversation in Claude Desktop
2. Attach the relevant `skills/<name>/SKILL.md` as a file
3. Ask Claude to use the skill

For a bundled `.skill` package with API docs and scripts included (currently `elfa-ai` only), run `./skills/elfa-ai/scripts/build-skill.sh` and attach the generated `dist/elfa-ai.skill` instead.

</details>

### Setting up `elfa-grvt-bot`

`elfa-grvt-bot` ships a full Python project under `assets/source/`. After install, run the skill's bootstrap to create the bot's working directory, install dependencies, start the receiver, and open a public tunnel. From your agent, just ask:

> "Set up the Elfa GRVT bot."

The agent will execute `scripts/bootstrap.py` and walk you through credential gathering for Elfa, GRVT, and (optionally) Telegram. See [`skills/elfa-grvt-bot/SKILL.md`](skills/elfa-grvt-bot/SKILL.md) for the full setup walkthrough.

## Skills

| Skill | Description |
|---|---|
| [elfa-ai](skills/elfa-ai) | Crypto social intelligence + Auto condition engine — trending tokens, mentions, narratives, AI market analysis, and automated trigger workflows |
| [elfa-grvt-bot](skills/elfa-grvt-bot) | Self-hosted Elfa Auto to GRVT perpetual futures bot with FastAPI receiver, EIP-712 signing, SQLite registry, Telegram alerts, and OTOCO TP/SL execution |

## Spec validation

Every skill in this repo follows the [Agent Skills](https://agentskills.io/specification.md) directory format: `SKILL.md` at the skill root, optional `scripts/`, `references/`, and `assets/` resources, and spec-compatible frontmatter.

Validate a skill before publishing changes:

```bash
uvx --from skills-ref agentskills validate ./skills/elfa-ai
uvx --from skills-ref agentskills validate ./skills/elfa-grvt-bot
```

## Get an API key

Grab a free key (1,000 credits) at **https://go.elfa.ai/claude-skills**

Set it as an environment variable:

```bash
export ELFA_API_KEY=your_key_here
```

Free tier works with most endpoints. Trending narratives and AI chat require a paid plan — see the link above for details.

Alternatively, use **x402 keyless payments** to pay per request with USDC on Base (no signup required). See the [x402 docs](https://docs.elfa.ai/x402-payments) for setup.

## Example prompts

```
Show me the top trending tokens in the last 24 hours
```

```
What are the top mentions for $SOL this week?
```

```
Get smart stats for @elaborateelf on Twitter
```

```
Give me a curl example for the keyword mentions endpoint
```

```
Help me integrate the Elfa trending tokens endpoint in TypeScript
```

```
Alert me when BTC crosses above 100k
```

```
Set up a recurring 4h portfolio check on BTC, ETH, and SOL
```

```
Create an Auto query that triggers when ETH RSI drops below 30 on the 1h chart
```

```
Help me build a multi-condition trigger for BTC + ETH breakout confirmation
```

```
Set up the Elfa GRVT bot and create a SOL RSI dip-buy strategy with TP and SL
```

`elfa-grvt-bot` is live-trading infrastructure. It is prod-only for GRVT and asks for explicit confirmation before activating any strategy.

## API endpoints

| Endpoint | Description |
|---|---|
| `/v2/aggregations/trending-tokens` | Trending tokens by mention count |
| `/v2/account/smart-stats` | Smart follower & engagement stats |
| `/v2/data/top-mentions` | Top mentions for a ticker symbol |
| `/v2/data/keyword-mentions` | Search mentions by keyword |
| `/v2/data/event-summary` | AI event summaries (5 credits) |
| `/v2/data/trending-narratives` | Trending narrative clusters (5 credits) |
| `/v2/data/token-news` | Token-related news |
| `/v2/aggregations/trending-cas/twitter` | Trending contract addresses (Twitter) |
| `/v2/aggregations/trending-cas/telegram` | Trending contract addresses (Telegram) |
| `/v2/chat` | AI chat — market analysis, token intros, account reviews |

### Auto endpoints (Condition Engine)

| Endpoint | Description |
|---|---|
| `/v2/auto/chat` | Builder Chat — AI-assisted query building |
| `/v2/auto/queries/validate` | Validate EQL query and preview cost |
| `/v2/auto/queries/preview` | Preview a query without creating it |
| `/v2/auto/queries` | Create and list Auto queries |
| `/v2/auto/queries/:queryId` | Poll query status (GET) |
| `/v2/auto/queries/:queryId/cancel` | Cancel an `active` query (POST) |
| `/v2/auto/queries/:queryId` | Delete a terminal query (DELETE — `triggered` / `expired` / `cancelled` / `failed` only) |
| `/v2/auto/queries/:queryId/stream` | Stream notifications via SSE |
| `/v2/auto/queries/:queryId/sessions` | List/get LLM analysis sessions |
| `/v2/auto/queries/drafts` | Upsert, list, preview, convert, delete query drafts |
| `/v2/auto/executions` | List and get trigger execution records |
| `/v2/auto/exchanges` | Connect, list, disconnect exchange integrations |
| `/v2/auto/validate-tradable-symbol/:symbol` | Check whether a symbol is tradable as a Hyperliquid perp (pre-flight for `market_order`, `limit_order`, or `llm` trade callbacks) |

Auto endpoints require HMAC signing for trade-action mutations (`market_order`, `limit_order`, or `llm` callback to either) and exchange linking in API key mode; notification-only mutations (`notify`, `telegram_bot`, `webhook`, or `llm` callback to those) skip HMAC. x402 mode uses `x-elfa-agent-secret` instead of HMAC. Always-signing remains safe in API key mode — signed requests are accepted on every route. See [Auto docs](https://docs.elfa.ai/auto/overview).

Full details at [docs.elfa.ai](https://docs.elfa.ai).

---

Powered by [Elfa AI](https://go.elfa.ai/claude-visit) · [Documentation](https://docs.elfa.ai) · [Auto Docs](https://docs.elfa.ai/auto/overview)
