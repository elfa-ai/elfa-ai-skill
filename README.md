# Elfa AI Skills

Real-time crypto social intelligence and automated condition engine for AI coding agents. Track trending tokens, surface narratives, search mentions, run market analysis, and build automated trigger-based workflows — all from your agent's chat.

Works with **Claude Code**, **OpenCode**, **Cursor**, **GitHub Copilot**, **Codex**, and any tool that supports the [Agent Skills](https://agentskills.io) standard.

## Installation

### Any Agent (Recommended)

```bash
npx skills add elfa-ai/elfa-ai-skill
```

This uses the [Skills CLI](https://github.com/vercel-labs/skills) to install skills via symlink. Works with Claude Code, Cursor, Windsurf, Codex, and [40+ other agents](https://github.com/vercel-labs/skills#supported-agents). Skills stay up to date — run `npx skills update` to pull the latest.

Install globally (available across all projects):

```bash
npx skills add elfa-ai/elfa-ai-skill --global
```

### Manual Install

<details>
<summary>Claude Code</summary>

```bash
# Project-level (this project only)
mkdir -p .claude/skills/elfa-ai
cp skills/elfa-ai/SKILL.md .claude/skills/elfa-ai/
cp -r skills/elfa-ai/references/ skills/elfa-ai/scripts/ .claude/skills/elfa-ai/

# Global (all projects)
mkdir -p ~/.claude/skills/elfa-ai
cp skills/elfa-ai/SKILL.md ~/.claude/skills/elfa-ai/
cp -r skills/elfa-ai/references/ skills/elfa-ai/scripts/ ~/.claude/skills/elfa-ai/
```

</details>

<details>
<summary>OpenCode</summary>

```bash
mkdir -p ~/.config/opencode/skills/elfa-ai
cp skills/elfa-ai/SKILL.md ~/.config/opencode/skills/elfa-ai/
cp -r skills/elfa-ai/references/ skills/elfa-ai/scripts/ ~/.config/opencode/skills/elfa-ai/
```

</details>

<details>
<summary>Cursor</summary>

```bash
mkdir -p .cursor/rules
echo '---' > .cursor/rules/elfa-ai.mdc
echo 'description: "Elfa AI — crypto social intelligence, trending tokens, mentions, and market analysis"' >> .cursor/rules/elfa-ai.mdc
echo 'alwaysApply: true' >> .cursor/rules/elfa-ai.mdc
echo '---' >> .cursor/rules/elfa-ai.mdc
cat skills/elfa-ai/SKILL.md >> .cursor/rules/elfa-ai.mdc
```

</details>

<details>
<summary>GitHub Copilot</summary>

```bash
cat skills/elfa-ai/SKILL.md >> .github/copilot-instructions.md
```

</details>

<details>
<summary>Codex</summary>

```bash
cp skills/elfa-ai/SKILL.md AGENTS.md
```

</details>

<details>
<summary>Claude Desktop (attach file)</summary>

1. Start a conversation in Claude Desktop
2. Attach `skills/elfa-ai/SKILL.md` as a file
3. Ask Claude to use the skill

For a bundled package with API docs and scripts included, run `./skills/elfa-ai/scripts/build-skill.sh` and attach the generated `dist/elfa-ai.skill` instead.

</details>

## Skills

| Skill | Description |
|---|---|
| [elfa-ai](skills/elfa-ai) | Crypto social intelligence + Auto condition engine — trending tokens, mentions, narratives, AI market analysis, and automated trigger workflows |

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
| `/v2/auto/queries` | Create, list, poll Auto queries |
| `/v2/auto/queries/:queryId/stream` | Stream notifications via SSE |
| `/v2/auto/queries/:queryId/sessions` | List/get LLM analysis sessions |
| `/v2/auto/validate-symbol/:symbol` | Check symbol support for conditions |

Auto endpoints require HMAC signing for mutations (API key mode) or x-elfa-agent-secret (x402 mode). See [Auto docs](https://docs.elfa.ai/auto/overview).

Full details at [docs.elfa.ai](https://docs.elfa.ai).

---

Powered by [Elfa AI](https://go.elfa.ai/claude-visit) · [Documentation](https://docs.elfa.ai) · [Auto Docs](https://docs.elfa.ai/auto/overview)
