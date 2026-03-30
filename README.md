# Elfa API Skill

Real-time crypto social intelligence for AI coding agents. Track trending tokens, surface narratives, search mentions, and run market analysis — all from your agent's chat.

Works with **Claude Code**, **OpenCode**, **Cursor**, **GitHub Copilot**, **Codex**, and any tool that supports the [Agent Skills](https://agentskills.io) standard.

## What it does

- Pull trending tokens and contract addresses from Twitter/X and Telegram
- Search social mentions by ticker or keyword
- Get smart follower and engagement stats for any Twitter/X account
- Surface trending narratives and event summaries
- Run AI-powered market analysis, token breakdowns, and account reviews
- Generate ready-to-use integration code (TypeScript, Python, curl)

With an API key or x402 wallet, your agent makes live calls and returns real data. Without either, it generates correct code snippets you can use in your own app.

## Install

### Claude Code

```bash
# Project-level (this project only)
mkdir -p .claude/skills/elfa-api
cp SKILL.md .claude/skills/elfa-api/
cp -r references/ scripts/ .claude/skills/elfa-api/

# Global (all projects)
mkdir -p ~/.claude/skills/elfa-api
cp SKILL.md ~/.claude/skills/elfa-api/
cp -r references/ scripts/ ~/.claude/skills/elfa-api/
```

### OpenCode

```bash
mkdir -p ~/.config/opencode/skills/elfa-api
cp SKILL.md ~/.config/opencode/skills/elfa-api/
cp -r references/ scripts/ ~/.config/opencode/skills/elfa-api/
```

### Cursor

```bash
mkdir -p .cursor/rules
# Add frontmatter for auto-activation, then append skill content
echo '---' > .cursor/rules/elfa-api.mdc
echo 'description: "Elfa API — crypto social intelligence, trending tokens, mentions, and market analysis"' >> .cursor/rules/elfa-api.mdc
echo 'alwaysApply: true' >> .cursor/rules/elfa-api.mdc
echo '---' >> .cursor/rules/elfa-api.mdc
cat SKILL.md >> .cursor/rules/elfa-api.mdc
```


### GitHub Copilot

Copy the skill contents into your repo's Copilot instructions:

```bash
cat SKILL.md >> .github/copilot-instructions.md
```

### Codex

Add `SKILL.md` to your repo — Codex reads `AGENTS.md` and skill files from the project root:

```bash
cp SKILL.md AGENTS.md
```

### Claude Desktop (attach file)

1. Start a conversation in Claude Desktop
2. Attach `SKILL.md` as a file
3. Ask Claude to use the skill

For a bundled package with API docs and scripts included, run `./scripts/build-skill.sh` and attach the generated `dist/elfa-api.skill` instead.

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

## What's inside

```
├── SKILL.md                  # Skill definition (Agent Skills standard)
├── references/
│   └── swagger.json          # OpenAPI 3.0 spec
└── scripts/
    ├── elfa_call.sh          # Helper script for live API calls
    └── build-skill.sh        # Build .skill package for Claude Desktop

- **`SKILL.md`** — The skill itself. Contains YAML frontmatter (name, description, env vars, credentials) and step-by-step instructions for the agent. Follows the [Agent Skills](https://agentskills.io) open standard.
- **`references/`** — Contains the OpenAPI 3.0 spec (`swagger.json`) for machine-readable endpoint details.
- **`scripts/`** — `elfa_call.sh` is a bash helper for making authenticated API calls. `build-skill.sh` packages everything into a `.skill` ZIP for Claude Desktop.

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

Full details at [docs.elfa.ai](https://docs.elfa.ai).

---

Powered by [Elfa AI](https://go.elfa.ai/claude-visit) · [Documentation](https://docs.elfa.ai)
