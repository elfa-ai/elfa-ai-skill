---
name: elfa-ai
description: >
  Interact with the Elfa API — a crypto social intelligence platform that provides real-time
  sentiment, trending tokens, narrative tracking, AI-powered market analysis from Twitter/X
  and Telegram, and a managed condition engine (Auto) for building automated trigger-based
  agent workflows. Use this skill whenever the user wants to query crypto social data, check
  trending tokens or narratives, look up mentions for a ticker or keyword, get smart stats for
  a Twitter account, retrieve token news, find trending contract addresses, chat with Elfa's AI
  for market analysis, or set up automated monitoring and triggers via Auto. Also trigger when
  the user asks how to integrate the Elfa API, wants example code or curl commands for Elfa
  endpoints, mentions "elfa" in the context of crypto data, or wants to build condition-based
  alerts and agent workflows. This skill covers both making live API calls (via API key or x402
  keyless payments) and generating correct code snippets for developers integrating the Elfa API
  into their own products. Supports two access modes: traditional API key authentication and
  x402 pay-per-request via USDC on Base (no registration required).
env:
  - name: ELFA_API_KEY
    description: >
      Elfa API key for authenticated requests. Optional — only needed for API key mode.
      Get a free key at https://go.elfa.ai/claude-skills. Not required if using x402 mode.
    required: false
  - name: ELFA_HMAC_SECRET
    description: >
      HMAC secret for signing Auto mutation requests (/v2/auto/* POST/DELETE).
      Required only for API-key mode Auto mutations. Get this from the Elfa Developer Portal.
    required: false
  - name: ELFA_AGENT_SECRET
    description: >
      Persistent agent identity secret for x402 Auto. Generate once with
      `openssl rand -hex 32` and reuse for all query lifecycle calls.
      Required only for x402 Auto mode.
    required: false
credentials:
  primary: ELFA_API_KEY (optional — x402 mode requires no credentials from the user)
  hmac: ELFA_HMAC_SECRET (optional — only for API-key Auto mutations)
  agent_secret: ELFA_AGENT_SECRET (optional — only for x402 Auto identity)
  x402: Wallet-based signing handled client-side by @x402/fetch or @x402/axios libraries
---

# Elfa API Skill

This skill enables agents to work with the [Elfa API](https://api.elfa.ai) — a social listening,
market context layer, and automated condition engine for crypto. Elfa ingests real-time data
from Twitter/X, Telegram, and other sources, then structures sentiment, narratives, and
attention shifts into actionable trading insights. The **Auto** subsystem adds a managed
condition engine and trigger pipeline — describe what to watch for, and Auto evaluates
continuously and fires actions when conditions are met.

Full documentation: [docs.elfa.ai](https://docs.elfa.ai)

## When to use this skill

- User asks about **trending tokens, narratives, or contract addresses** in crypto
- User wants **social mentions** for a specific ticker or keyword
- User wants **smart stats** (smart followers, engagement) for a Twitter/X account
- User wants an **AI-generated market summary, macro overview, or token analysis**
- User asks how to **integrate, call, or use the Elfa API**
- User wants **code examples** (curl, Python, JavaScript/TypeScript) for Elfa endpoints
- User mentions "elfa" in a crypto or trading data context
- User wants to **set up automated alerts or monitoring** on price, indicators, or narratives
- User wants to **build condition-based triggers** (e.g., "alert me when BTC crosses 100k")
- User mentions **Auto**, **EQL**, **condition engine**, or **trigger pipeline** in a crypto context
- User wants **agent workflows** that react to market conditions automatically
- User wants to **build queries with Builder Chat** using natural language

## API Overview

**Base URL:** `https://api.elfa.ai`
**Version:** v2 (current)
**Docs:** [docs.elfa.ai](https://docs.elfa.ai)

### Two access modes

Elfa supports two independent ways to authenticate requests:

| Mode | Endpoint prefix | Auth header | Best for |
|---|---|---|---|
| **API key** | `/v2/` | `x-elfa-api-key: YOUR_KEY` | Humans & apps with a registered key |
| **x402 (keyless)** | `/x402/v2/` | `PAYMENT-SIGNATURE: <signed-payload>` | Agents & wallets — no signup needed |

Both modes access the same data. The only difference is how you authenticate:
- **API key** — register at https://go.elfa.ai/claude-skills, get 1,000 free credits.
- **x402** — pay per request with USDC on Base. No registration, no API key. Currently in beta
  with a 70% discount on Auto endpoints.

### Endpoints at a glance

#### Data endpoints

All endpoints below work with both `/v2/` (API key) and `/x402/v2/` (keyless) prefixes,
except `key-status` which is API key mode only.

| Endpoint | Method | Description | Credits |
|---|---|---|---|
| `/v2/key-status` | GET | API key usage & limits (API key only) | Free |
| `/v2/aggregations/trending-tokens` | GET | Trending tokens by mention count | 1 |
| `/v2/account/smart-stats` | GET | Smart follower & engagement stats | 1 |
| `/v2/data/top-mentions` | GET | Top mentions for a ticker symbol | 1 |
| `/v2/data/keyword-mentions` | GET | Search mentions by keywords or account | 1 |
| `/v2/data/event-summary` | GET | AI event summaries from keyword mentions | 5 |
| `/v2/data/trending-narratives` | GET | Trending narrative clusters | 5 |
| `/v2/data/token-news` | GET | Token-related news mentions | 1 |
| `/v2/aggregations/trending-cas/twitter` | GET | Trending contract addresses (Twitter) | 1 |
| `/v2/aggregations/trending-cas/telegram` | GET | Trending contract addresses (Telegram) | 1 |
| `/v2/chat` | POST | AI chat with multiple analysis modes | Speed-based |

#### Auto endpoints (Condition Engine)

Auto endpoints are available under `/v2/auto/` (API key + HMAC) and `/x402/v2/auto/` (keyless).
See [Auto docs](https://docs.elfa.ai/auto/overview) for full details.

**API key mode (`/v2/auto/*`):**

_Query lifecycle:_

| Endpoint | Method | Description | Auth |
|---|---|---|---|
| `/v2/auto/chat` | POST | Builder Chat — AI-assisted query building | HMAC |
| `/v2/auto/queries/validate` | POST | Validate EQL and preview cost | API key only |
| `/v2/auto/queries/preview` | POST | Preview a query without creating it | API key |
| `/v2/auto/queries` | POST | Create and activate a query | HMAC |
| `/v2/auto/queries` | GET | List queries | API key |
| `/v2/auto/queries/:queryId` | GET | Poll query status and executions (resolves query or draft) | API key |
| `/v2/auto/queries/:queryId` | DELETE | Cancel a query | HMAC |
| `/v2/auto/queries/:queryId/stream` | GET | Stream notifications via SSE | API key |

_Query drafts (editable, not yet active):_

| Endpoint | Method | Description | Auth |
|---|---|---|---|
| `/v2/auto/queries/drafts` | POST | Create or update (upsert) a query draft | API key |
| `/v2/auto/queries/drafts` | GET | List editable query drafts | API key |
| `/v2/auto/queries/drafts/:draftId` | GET | Get a specific draft (legacy — prefer `GET /queries/{queryId}`) | API key |
| `/v2/auto/queries/drafts/:draftId` | DELETE | Delete a query draft | API key |
| `/v2/auto/queries/drafts/:draftId/preview` | POST | Preview a stored draft | API key |
| `/v2/auto/queries/drafts/:draftId/convert` | POST | Convert a draft into an active query | HMAC |

_LLM sessions (for `action.type: "llm"` queries):_

| Endpoint | Method | Description | Auth |
|---|---|---|---|
| `/v2/auto/queries/:queryId/sessions` | GET | List LLM sessions | API key |
| `/v2/auto/queries/:queryId/sessions/:sessionId` | GET | Get full LLM session details | API key |

_Executions (trigger fire records):_

| Endpoint | Method | Description | Auth |
|---|---|---|---|
| `/v2/auto/executions` | GET | List execution records | API key |
| `/v2/auto/executions/:executionId` | GET | Get a single execution record | API key |

_Exchange connections (for live trade actions):_

| Endpoint | Method | Description | Auth |
|---|---|---|---|
| `/v2/auto/exchanges` | POST | Connect an exchange integration | HMAC |
| `/v2/auto/exchanges` | GET | List connected exchanges | API key |
| `/v2/auto/exchanges/:exchange` | DELETE | Disconnect an exchange | HMAC |

_Other:_

| Endpoint | Method | Description | Auth |
|---|---|---|---|
| `/v2/auto/validate-symbol/:symbol` | GET | Check symbol support for conditions | API key |
| `/v2/auto/unmet-intent` | POST | Report an unsupported use case (after substitution ladder) | API key |

**x402 mode (`/x402/v2/auto/*`)** — note: some routes use POST instead of GET:

| Endpoint | Method | Description |
|---|---|---|
| `/x402/v2/auto/chat` | POST | Builder Chat |
| `/x402/v2/auto/queries/validate` | POST | Validate EQL and preview cost |
| `/x402/v2/auto/queries` | POST | Create and activate a query |
| `/x402/v2/auto/queries/:queryId` | POST | Poll query status (POST, not GET) |
| `/x402/v2/auto/queries/:queryId` | DELETE | Cancel a query |
| `/x402/v2/auto/queries/:queryId/stream` | GET | Stream notifications via SSE |
| `/x402/v2/auto/queries/:queryId/sessions` | POST | List LLM sessions (POST, not GET) |
| `/x402/v2/auto/queries/:queryId/sessions/:sessionId` | POST | Get LLM session details (POST, not GET) |
| `/x402/v2/auto/validate-symbol/:symbol` | GET | Check symbol support |
| `/x402/v2/auto/unmet-intent` | POST | Report an unsupported use case |

> **Note on x402 Auto scope.** Trade execution actions are not available via x402. Exchange connections, drafts, and executions endpoints are API-key-mode only. x402 Auto covers the core monitoring lifecycle (chat, validate, create, poll, cancel, stream, sessions).

For full parameter details, see the [Elfa API documentation](https://docs.elfa.ai).

**Machine-readable manifest:** an endpoint manifest is published at
`https://docs.elfa.ai/assets/files/endpoints.manifest-*.json` (path rotates per release) —
each entry includes method/path, docs route, required headers, HMAC requirement with mounted
signature path template, payment requirement, and request/response examples. Useful for
auto-generating client code.

## How to use this skill

### Step 1: Determine the mode

Check whether the user wants to **make a live call**, **get code/integration help**, or
**set up automated monitoring**.

- If the user says things like "show me trending tokens", "what's the sentiment on SOL",
  "get me the top mentions for ETH" → they want **live data**. Proceed to Step 2a.
- If the user says things like "how do I call the trending tokens endpoint", "give me a
  curl example", "help me integrate Elfa" → they want **code snippets**. Skip to Step 4.
- If the user mentions **x402**, **keyless**, **pay-per-request**, or **wallet-based access**
  → they want **x402 mode**. See Step 2b for live calls or Step 4 for code snippets.
- If the user mentions **Auto**, **alerts**, **triggers**, **monitoring**, **conditions**,
  **"alert me when"**, **"notify me if"**, **EQL**, or **Builder Chat** → they want
  **Auto**. Proceed to Step 3.

### Step 2a: Making live API calls (API key mode)

Use the `bash_tool` to call the Elfa API via curl.

**Getting the API key:**
1. Check if the `ELFA_API_KEY` environment variable is set. This is the preferred method.
2. If the env var is not set, **stop and prompt the user.** Offer both options:

   > To make live calls, you have two options:
   >
   > **Option A — API key (free tier):** Get a free key with 1,000 credits at
   > **https://go.elfa.ai/claude-skills** — then set it as the `ELFA_API_KEY` environment
   > variable (do not paste it directly into the chat).
   >
   > **Option B — x402 keyless payments:** Pay per request with USDC on Base — no signup
   > needed. See the [x402 docs](https://docs.elfa.ai/x402-payments) for setup.

   Do not attempt any authenticated API calls without a key or x402 setup. Wait for the user.
3. **Credential safety:**
   - Always read the API key from the `ELFA_API_KEY` environment variable, never ask the
     user to paste it into the conversation.
   - Never log or expose the full API key in outputs — mask it when displaying curl commands.
   - If a user does paste a key in chat, warn them to rotate it and set it as an env var instead.

**Free tier limitations:**
The free tier provides 1,000 credits that work on most endpoints. However, some endpoints
(such as trending narratives and AI chat) require a paid plan (PAYG, Grow, or Enterprise).
Check https://go.elfa.ai/claude-skills for the latest tier requirements.

If a user hits an authorization error on one of these endpoints, let them know they can
upgrade their plan or use x402 payments instead. Full details at https://go.elfa.ai/claude-skills.

**Making the call:**

```bash
curl -s -H "x-elfa-api-key: $ELFA_API_KEY" "https://api.elfa.ai/v2/aggregations/trending-tokens?timeWindow=24h&pageSize=10"
```

### Step 2b: Making live API calls (x402 keyless mode)

x402 lets any wallet pay per request using USDC on Base — no API key, no registration.
This is ideal for agents, bots, and programmatic access.

**How x402 works:**
1. Send a request to the `/x402/v2/` version of any endpoint (no auth header).
2. The server responds with HTTP **402** containing payment requirements.
3. Your wallet signs a USDC transfer authorization (no gas fees).
4. Resend the request with the signed payment in the `PAYMENT-SIGNATURE` header.
5. Server verifies payment, serves the response, and settles on-chain.

**x402 signing and security:**
- Signing happens **entirely client-side** using the `@x402/fetch` or `@x402/axios`
  libraries. The agent never handles, stores, or transmits private keys.
- The user's wallet private key is used only locally by the x402 library to sign
  EIP-712 typed data authorizing a specific USDC amount for a specific request.
- Never ask the user to share their wallet private key or seed phrase in the conversation.
- When generating x402 code examples, use `"0xYOUR_PRIVATE_KEY"` as a placeholder and
  advise the user to load it from an environment variable (e.g., `process.env.PRIVATE_KEY`).

**x402 details:**
- **Chain:** Base (`eip155:8453`)
- **Currency:** USDC on Base (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`)
- **Status:** Currently in beta
- **Facilitator:** [xpay.sh](https://xpay.sh/)

**x402 pricing (data endpoints):**

| Tier | Credits | USDC Cost | Endpoints |
|---|---|---|---|
| Standard | 1 | $0.009 | trending-tokens, smart-stats, keyword-mentions, token-news, top-mentions, trending-cas |
| Extended | 5 | $0.045 | event-summary, trending-narratives |
| Chat — fast | 5 | $0.045 | chat (speed: "fast") |
| Chat — expert | 18 | $0.162 | chat (speed: "expert", default) |

**Making an x402 call with curl (manual flow):**

```bash
# Step 1: Send request without payment — get 402 with payment requirements
curl -s https://api.elfa.ai/x402/v2/aggregations/trending-tokens?timeWindow=24h

# Step 2: After signing the payment payload with your wallet, resend with payment header
curl -s -H "PAYMENT-SIGNATURE: <base64-encoded-payment-payload>" \
  "https://api.elfa.ai/x402/v2/aggregations/trending-tokens?timeWindow=24h"
```

**Recommended: use the `@x402/fetch` library** which handles payment automatically:

```javascript
import { wrapFetchWithPayment } from "@x402/fetch";
import { ExactEvmScheme, toClientEvmSigner } from "@x402/evm";
import { x402Client } from "@x402/core/client";
import { createPublicClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { base } from "viem/chains";

const account = privateKeyToAccount("0xYOUR_PRIVATE_KEY");
const publicClient = createPublicClient({ chain: base, transport: http() });
const signer = toClientEvmSigner(account, publicClient);

const client = new x402Client().register(
  "eip155:8453",
  new ExactEvmScheme(signer));

const x402Fetch = wrapFetchWithPayment(fetch, client);

// Use x402Fetch exactly like regular fetch — payment is handled automatically on 402 responses
const response = await x402Fetch(
  "https://api.elfa.ai/x402/v2/aggregations/trending-tokens?timeWindow=24h");
const data = await response.json();
```

**x402 with the Chat endpoint (POST):**

```javascript
const response = await x402Fetch(
  "https://api.elfa.ai/x402/v2/chat",
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: "What is the current sentiment on BTC?",
      analysisType: "chat",
      speed: "fast", // "fast" = 5 credits ($0.045), "expert" = 18 credits ($0.162)
    }),
  });
const data = await response.json();
console.log(data.data.message);
```

**Presenting results:**
- Parse the JSON response and present it in a clean, readable format.
- For trending tokens: show a ranked table with token name, mention count, and change %.
- For mentions: show tweet links, engagement metrics, and account info.
  Note: Elfa returns tweet IDs but not tweet text content — let the user know they'll
  need their own X (Twitter) API key to fetch the actual tweet content.
- For narratives/summaries: present the narrative text with source links.
- For the chat endpoint: display the AI response cleanly.
- If the response contains an error, explain what went wrong and suggest fixes.

### Step 3: Auto — Condition Engine and Trigger Pipeline

Auto is a managed **condition engine + trigger pipeline** for agents. You describe what to
watch for (price, technical indicators, LLM-evaluated conditions, scheduled checks), and
Auto evaluates continuously and fires actions when conditions resolve to true.

Full Auto docs: [docs.elfa.ai/auto/overview](https://docs.elfa.ai/auto/overview)

#### When to suggest Auto

- User wants alerts based on **price thresholds** ("alert me when BTC crosses 100k")
- User wants alerts based on **technical indicators** ("notify when RSI drops below 30")
- User wants **scheduled checks** ("check every 4 hours")
- User wants **narrative/sentiment monitoring** ("alert when AI token narrative shifts")
- User wants **multi-condition triggers** ("BTC above 100k AND ETH above 3500")
- User wants to **compare live metrics** ("alert when price crosses above Bollinger Band")
- User wants **LLM analysis on trigger** ("when it triggers, run a full analysis")

#### Auto access models

| Mode | Route prefix | Auth | Best for |
|---|---|---|---|
| API key + HMAC | `/v2/auto/*` | `x-elfa-api-key` on all + HMAC on mutations | Apps, dashboards |
| x402 keyless | `/x402/v2/auto/*` | x402 payment + `x-elfa-agent-secret` | AI agents, bots |

#### HMAC signing (API key mode — mutations only)

Mutation endpoints under `/v2/auto/*` (POST, DELETE) require HMAC signing in addition to
`x-elfa-api-key`. Read-only endpoints (GET) only need the API key.

**Required headers for mutations:**

```
x-elfa-api-key: <api_key>
x-elfa-timestamp: <unix_seconds>
x-elfa-signature: <hex_hmac_sha256>
```

**Signing payload:**

```
timestamp + method + mounted_path + body
```

**CRITICAL:** `mounted_path` is the path **inside** `/v2/auto`, NOT the full URL path.
- Request URL: `/v2/auto/queries` → signed path: `/queries`
- Request URL: `/v2/auto/chat` → signed path: `/chat`
- Request URL: `/v2/auto/queries/q_123` → signed path: `/queries/q_123`

**Replay protection:** timestamp must be within 30 seconds.

**TypeScript signing example:**

```typescript
import crypto from "crypto";

const hmacSecret = process.env.ELFA_HMAC_SECRET!;
const apiKey = process.env.ELFA_API_KEY!;

function signAutoRequest(method: string, mountedPath: string, body: string = "") {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const payload = `${timestamp}${method}${mountedPath}${body}`;
  const signature = crypto
    .createHmac("sha256", hmacSecret)
    .update(payload)
    .digest("hex");
  return { timestamp, signature };
}

// Example: Create a query
const body = JSON.stringify({
  title: "BTC breakout alert",
  description: "Notify when BTC trades above 100k.",
  query: {
    conditions: {
      AND: [{ source: "price", method: "current", args: { symbol: "BTC" }, operator: ">", value: 100000 }]
    },
    actions: [{ stepId: "step_1", type: "notify", params: { message: "BTC crossed 100k" } }],
    expiresIn: "24h"
  }
});

const { timestamp, signature } = signAutoRequest("POST", "/queries", body);

const response = await fetch("https://api.elfa.ai/v2/auto/queries", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "x-elfa-api-key": apiKey,
    "x-elfa-timestamp": timestamp,
    "x-elfa-signature": signature,
  },
  body,
});
```

**Bash signing example:**

```bash
TIMESTAMP=$(date +%s)
METHOD="POST"
PATH_SIGN="/queries"
BODY='{"title":"BTC alert","query":{"conditions":{"AND":[{"source":"price","method":"current","args":{"symbol":"BTC"},"operator":">","value":100000}]},"actions":[{"stepId":"step_1","type":"notify","params":{"message":"BTC crossed 100k"}}],"expiresIn":"24h"}}'
SIGNATURE=$(echo -n "${TIMESTAMP}${METHOD}${PATH_SIGN}${BODY}" | openssl dgst -sha256 -hmac "$ELFA_HMAC_SECRET" | cut -d' ' -f2)

curl -s -X POST "https://api.elfa.ai/v2/auto/queries" \
  -H "Content-Type: application/json" \
  -H "x-elfa-api-key: $ELFA_API_KEY" \
  -H "x-elfa-timestamp: $TIMESTAMP" \
  -H "x-elfa-signature: $SIGNATURE" \
  -d "$BODY"
```

#### x402 Auto (keyless agent mode)

For x402 Auto, no API key or HMAC is needed. Instead:
- Send x402 payment headers (`PAYMENT-SIGNATURE` preferred, `X-PAYMENT` legacy)
- Include `x-elfa-agent-secret` on all query lifecycle routes

**Agent secret management:**
Generate a strong secret once and reuse it for all calls:

```bash
openssl rand -hex 32
# or: node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

Persist as `ELFA_AGENT_SECRET`. **Do not rotate per request** — x402 session ownership is
derived from `SHA256(secret)`. If you change secrets, your agent identity changes and
existing queries/sessions may become inaccessible.

#### Auto pricing (both modes)

**API-key mode (`/v2/auto/*`) — charged against your credit balance:**

| Operation | Credits | Notes |
|---|---|---|
| `POST /v2/auto/chat` (Builder Chat) | `1 + dynamic` | Base 1 credit + `ceil(request_cost * 750)` dynamic charge based on LLM usage |
| `POST /v2/auto/queries` (Create) | Simulation-driven | Baseline `5` + per simulated LLM call: fast `+5`, expert `+18` |
| `POST /v2/auto/queries/validate` | Free | Returns cost estimate — always call before Create |
| `POST /v2/auto/queries/preview` | Free | Preview without creating |
| `GET /v2/auto/queries/*` (list, poll, stream, sessions) | Free | |
| `DELETE /v2/auto/queries/:queryId` (Cancel) | Free | |
| `GET /v2/auto/validate-symbol/:symbol` | Free | |
| `POST /v2/auto/unmet-intent` | Free | Not billed |

> Reference USD values for Create: baseline `$0.045`, fast call `+$0.045`, expert call `+$0.162`. Use `/queries/validate` to preview exact cost before committing.

**x402 mode (`/x402/v2/auto/*`) — 70% discount, limited-time, pay-per-request in USDC on Base:**

| Operation | Credits | USDC Cost |
|---|---|---|
| Builder Chat — fast | 5 | $0.045 |
| Builder Chat — expert | 18 | $0.162 |
| Query creation — baseline | 5 | $0.045 |
| Per fast LLM call | +5 | +$0.045 |
| Per expert LLM call | +18 | +$0.162 |
| Validate, poll, cancel, sessions, stream | Free | Free |
| Unmet-intent | Free | Free |

**x402 Auto example:**

```javascript
// Validate a query (x402 Auto)
const response = await x402Fetch(
  "https://api.elfa.ai/x402/v2/auto/queries/validate",
  {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-elfa-agent-secret": process.env.ELFA_AGENT_SECRET,
    },
    body: JSON.stringify({
      query: {
        conditions: { AND: [{ source: "price", method: "current", args: { symbol: "BTC" }, operator: ">", value: 100000 }] },
        actions: [{ stepId: "step_1", type: "notify", params: { message: "BTC crossed 100k" } }],
        expiresIn: "24h"
      }
    }),
  });
```

#### Recommended call sequence (deterministic agent path)

**API key mode (`/v2/auto/*`):**

1. `POST /v2/auto/chat` — Ask Builder Chat to draft a query
2. `POST /v2/auto/queries/validate` — Validate EQL and preview cost
3. `POST /v2/auto/queries` — Create and activate
4. `GET /v2/auto/queries/{queryId}/stream` — Stream notifications (or poll)
5. `GET /v2/auto/queries/{queryId}/sessions` + `/sessions/{sessionId}` — Fetch LLM output (if using `llm` action)

**x402 mode (`/x402/v2/auto/*`):**

1. `POST /x402/v2/auto/chat` — Ask Builder Chat to draft a query
2. `POST /x402/v2/auto/queries/validate` — Validate EQL and preview cost
3. `POST /x402/v2/auto/queries` — Create and activate
4. `GET /x402/v2/auto/queries/{queryId}/stream` — Stream notifications (or poll via POST)
5. `POST /x402/v2/auto/queries/{queryId}/sessions` + `/sessions/{sessionId}` — Fetch LLM output

**Always validate before create.** Validate returns structured errors you can iterate on
without spending credits.

**Failure handling order** (apply in sequence):

1. Retry transient network errors with exponential backoff.
2. On `400` / `422` validation failure → repair query using [Validation Errors table](#validation-errors--next-action-table), re-validate.
3. On `401` / `403` auth failure → refresh credentials or verify Auto is enabled for the API key.
4. On `402` x402 payment failure → re-price and retry with valid payment payload.
5. On `410` (SSE stream closed) → re-open stream or fall back to polling.

**Common agent flows:**

_Poll-based LLM flow:_
```
POST /v2/auto/queries                               → create query with action.type = "llm"
GET  /v2/auto/queries/{queryId}                     → poll until execution with sessionId appears
GET  /v2/auto/queries/{queryId}/sessions/{sessionId} → fetch full analysis
```

_Webhook-based LLM flow:_
```
POST /v2/auto/queries                               → create with action.type = "llm" + callback webhook
(wait for webhook)                                  → receive session reference / output
(optional) GET session fetch                        → /v2/auto/queries/{queryId}/sessions/{sessionId}
```

#### Builder Chat

Builder Chat (`POST /v2/auto/chat` or `POST /x402/v2/auto/chat`) uses AI to translate
natural language into EQL queries. Use `sessionId` for multi-turn conversations.

```json
{
  "message": "Alert me when BTC breaks 100k with RSI confirmation above 55",
  "speed": "expert",
  "sessionId": "optional-session-id"
}
```

Response (API-key mode):

```json
{
  "sessionId": "session-uuid",
  "response": "I can help with that... (markdown + EQL JSON code block)",
  "title": "BTC Breakout Alert",
  "reasoning": null,
  "planIds": []
}
```

The response message contains the AI's reply in Markdown. When it generates EQL, it will be
in a JSON code block — extract, validate via `/queries/validate`, then submit via `/queries`.

**Prompting tips for Builder Chat:**
- Include `title` and `description` (shown in notifications so recipients know what fired hours/days later)
- Specify symbols, timeframe, trigger behavior (one-time vs recurring), delivery target
- Append `"If anything is unsupported, return the closest supported query and list substitutions"` to handle edge cases gracefully
- Prefer `expiresIn` of `24h`–`3d` for fresh signals
- Persist `sessionId` and reuse it for follow-up prompts so the model keeps context across turns

**High-impact prompt pack** — drop these into `POST /v2/auto/chat` as the `message` field:

_1) Complex TA breakout with direction filter:_

```
Build an Auto query:
- title + description: short human-readable summary and 1-2 sentence thesis
- symbols: BTC, ETH, SOL
- timeframe: 5m
- trigger when price breaks previous 1h range high or low
- confirm direction with RSI(14): >55 for upside, <45 for downside
- actions: telegram alert + webhook to https://your-runner.example/auto/events
- one-time trigger, expires in 48h
If anything is unsupported, return the closest supported query and list substitutions.
```

_2) CEX + DEX monitoring pack:_

```
Build an Auto query pack for supported CEX + DEX symbols:
- title + description per query: short summary and thesis sentence
- watchlist: WBTC, ETH, SOL, HYPE
- trigger when 15m volume surge aligns with price momentum
- action: webhook to https://your-runner.example/auto/events
- include symbol and trigger summary in payload
- expires in 24h
If any symbol/source is unsupported, skip it and report skipped items.
```

_3) News + X sentiment context on trigger:_

```
Build an Auto query:
- title + description: short summary and the thesis behind watching this
- monitor BTC and ETH for abnormal 1h move + volume confirmation
- on trigger run llm action that adds:
  - latest market news context
  - X sentiment summary
  - risk note
- also send telegram alert with a short summary
- expires in 24h
```

_4) Prediction-market thesis watcher:_

```
Build an Auto query:
- title + description: name the thesis basket and state the catalyst you're watching for
- monitor tokens in my prediction-market thesis basket
- trigger on rapid momentum shift with volume confirmation
- action: llm to produce catalyst hypothesis + invalidation level
- deliver to webhook: https://your-runner.example/auto/events
- include decision priority: high/medium/low
- expires in 2d
```

_5) Portfolio risk guardrail:_

```
Build an Auto query:
- title + description: portfolio guardrail label and the risk scenario it covers
- watch my portfolio symbols: BTC, ETH, SOL, HYPE
- trigger on downside acceleration and momentum weakness
- action: telegram alert with severity and suggested next check
- recurring checks, expires in 3d
```

_6) Agent handoff with strict execution contract:_

```
Build an Auto query:
- title + description: breakout playbook name and the execution intent
- trigger on breakout + trend-confirmation conditions
- action: webhook to https://your-runner.example/auto/events
- include fields: eventId, symbol, triggerReason, priority, queryId
- objective: downstream agent decides next action under policy constraints
- expires in 24h
```

#### Query model (EQL)

A query contains `conditions`, `actions`, and `expiresIn`:

```json
{
  "title": "BTC RSI oversold on 1h",
  "description": "Mean-reversion entry: if BTC 1h RSI dips under 30, consider scaling in.",
  "conditions": {
    "AND": [
      {
        "source": "ta",
        "method": "rsi",
        "args": { "symbol": "BTC", "timeframe": "1h", "period": 14 },
        "operator": "<",
        "value": 30
      }
    ]
  },
  "actions": [
    { "stepId": "step_1", "type": "webhook", "params": { "url": "https://your-endpoint.example/events" } }
  ],
  "expiresIn": "24h"
}
```

**Condition rules:**
- Root group must be `AND` or `OR`
- Nest groups up to depth 3, max 10 leaf conditions
- Multi-symbol: a single query can require BTC AND ETH AND SOL conditions jointly

**Allowed `expiresIn` values:** `1h`, `2h`, `4h`, `8h`, `12h`, `24h`, `2d`, `3d`, `5d`, `7d`

**Allowed action types:** `webhook`, `notify`, `telegram`, `llm` — chainable (multiple
actions per query).

#### Triggers — condition sources

**Price source (`price`):**

| Method | Args | Returns | Description |
|---|---|---|---|
| `current` | `symbol` | number | Current price |
| `change` | `symbol`, `period` | number | % change over period |
| `high` | `symbol`, `period` | number | High in period |
| `low` | `symbol`, `period` | number | Low in period |
| `volume` | `symbol`, `period` | number | Volume (USD) over period |

**TA source (`ta`) — technical indicators:**

| Method | Required args | Optional args | Returns |
|---|---|---|---|
| `rsi` | `symbol`, `timeframe` | `period` (default 14) | RSI (0-100) |
| `macd_value` | `symbol`, `timeframe` | — | MACD line |
| `macd_signal` | `symbol`, `timeframe` | — | MACD signal line |
| `macd_histogram` | `symbol`, `timeframe` | — | MACD histogram |
| `bbands_upper` | `symbol`, `timeframe` | `period` (default 20) | Upper Bollinger Band |
| `bbands_middle` | `symbol`, `timeframe` | `period` (default 20) | Middle Bollinger Band |
| `bbands_lower` | `symbol`, `timeframe` | `period` (default 20) | Lower Bollinger Band |
| `ema` | `symbol`, `timeframe`, `period` | — | Exponential MA |
| `sma` | `symbol`, `timeframe`, `period` | — | Simple MA |
| `atr` | `symbol`, `timeframe` | `period` (default 14) | Average True Range |
| `stoch_k` | `symbol`, `timeframe` | — | Stochastic %K |
| `stoch_d` | `symbol`, `timeframe` | — | Stochastic %D |
| `cci` | `symbol`, `timeframe` | `period` (default 20) | CCI |
| `willr` | `symbol`, `timeframe` | `period` (default 14) | Williams %R |

**TA critical rules:**
- `ema` and `sma` **require** `period` — it is NOT optional
- `period` must be a JSON number (`14`), not a string (`"14"`)
- Use `period` not `length` — `length` is not a recognized alias
- `timeframe` values: `1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `8h`, `12h`, `1d`

**Cron source (`cron`):**

| Method | Args | Description |
|---|---|---|
| `once` | `period` | True on first due evaluation at/after creation + period |
| `onceRemainTrue` | `period` | True on first due evaluation and stays true |
| `every` | `period` | True at each period interval |

**LLM source (`llm`):**

| Method | Args | Description |
|---|---|---|
| `athena_condition` | `query`, `period`, `speed?` | LLM-evaluated condition (natural language) |

**Scheduling period (cron / llm):** minimum `1h`. Allowed: `1h`, `2h`, `4h`, `8h`,
`12h`, `24h`, `1d`, `7d`.

**Supported operators:** `>`, `<`, `>=`, `<=`, `==`, `!=`, `crosses_above`, `crosses_below`

**Dynamic comparisons:** `value` can reference another live data source instead of a literal:

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

#### Copy-paste query templates

**1) Breakout alert (webhook):**

```json
{
  "title": "BTC breakout above 100k",
  "description": "Notify runner when BTC spot trades above the 100k level.",
  "conditions": { "AND": [{ "source": "price", "method": "current", "args": { "symbol": "BTC" }, "operator": ">", "value": 100000 }] },
  "actions": [{ "stepId": "step_1", "type": "webhook", "params": { "url": "https://your-endpoint.example/events" } }],
  "expiresIn": "24h"
}
```

**2) Downside guardrail (telegram):**

```json
{
  "title": "ETH downside guardrail (< 2500)",
  "description": "Risk-off alert: flag if ETH breaks below 2500.",
  "conditions": { "AND": [{ "source": "price", "method": "current", "args": { "symbol": "ETH" }, "operator": "<", "value": 2500 }] },
  "actions": [{ "stepId": "step_1", "type": "telegram", "params": { "message": "ETH fell below 2500" } }],
  "expiresIn": "24h"
}
```

**3) Triggered LLM analysis:**

```json
{
  "title": "BTC > 100k — LLM review",
  "description": "On BTC breakout, run LLM to decide next trading action.",
  "conditions": { "AND": [{ "source": "price", "method": "current", "args": { "symbol": "BTC" }, "operator": ">", "value": 100000 }] },
  "actions": [{ "stepId": "step_1", "type": "llm", "params": { "objective": "Analyze trigger context and return next action" } }],
  "expiresIn": "24h"
}
```

**4) Multi-symbol confirmation:**

```json
{
  "title": "BTC + ETH joint breakout",
  "description": "Confirm majors moving together before acting.",
  "conditions": {
    "AND": [
      { "source": "price", "method": "current", "args": { "symbol": "BTC" }, "operator": ">", "value": 100000 },
      { "source": "price", "method": "current", "args": { "symbol": "ETH" }, "operator": ">", "value": 3500 }
    ]
  },
  "actions": [{ "stepId": "step_1", "type": "notify", "params": { "message": "BTC and ETH confirmation fired" } }],
  "expiresIn": "24h"
}
```

**5) Dynamic comparison (price vs Bollinger Band):**

```json
{
  "title": "ETH breakout above 4h upper BBand",
  "description": "Dynamic: fire when ETH price crosses above its own 4h upper Bollinger Band.",
  "conditions": {
    "AND": [{
      "source": "price", "method": "current", "args": { "symbol": "ETH" },
      "operator": "crosses_above",
      "value": { "source": "ta", "method": "bbands_upper", "args": { "symbol": "ETH", "timeframe": "4h" } }
    }]
  },
  "actions": [{ "stepId": "step_1", "type": "webhook", "params": { "url": "https://your-endpoint.example/events" } }],
  "expiresIn": "2d"
}
```

**6) Scheduled cron check:**

```json
{
  "title": "Every 4h: portfolio sweep",
  "description": "Recurring LLM pass every 4h.",
  "conditions": { "AND": [{ "source": "cron", "method": "every", "args": { "period": "4h" }, "operator": "==", "value": true }] },
  "actions": [{ "stepId": "step_1", "type": "llm", "params": { "objective": "Summarize BTC/ETH/SOL context and flag risk shifts" } }],
  "expiresIn": "3d"
}
```

**7) LLM-evaluated narrative condition:**

```json
{
  "title": "AI narrative shift watcher",
  "description": "Fire when dominant AI-token narrative shifts based on news + X sentiment.",
  "conditions": {
    "AND": [{
      "source": "llm", "method": "athena_condition",
      "args": { "query": "Has the dominant narrative around AI-sector tokens shifted materially in the last 6 hours?", "period": "1h" },
      "operator": "==", "value": true
    }]
  },
  "actions": [{ "stepId": "step_1", "type": "telegram", "params": { "message": "AI narrative shift detected" } }],
  "expiresIn": "2d"
}
```

#### Poll response shape

`GET /v2/auto/queries/{queryId}` (and the x402 `POST` equivalent) returns:

```json
{
  "queryId": "q_123",
  "status": "active",
  "latestEvaluation": {
    "evaluatedAt": "2026-04-01T12:00:00.000Z",
    "wouldTriggerNow": false
  },
  "executions": [
    {
      "id": "exec_123",
      "queryId": "q_123",
      "type": "llm",
      "status": "failed",
      "error": {
        "code": "LLM_ACTION_UPSTREAM_ERROR",
        "message": "Failed to execute llm action"
      },
      "createdAt": "2026-04-01T12:00:01.000Z"
    }
  ]
}
```

Use polling for debugging or backfills. For production delivery, prefer webhook or SSE
notifications. Store `sessionId` values from executions so you can fetch full LLM analysis
only when needed via `GET /v2/auto/queries/:queryId/sessions/:sessionId`.

#### Notifications — delivery channels

After a query triggers, Auto delivers events via one of three channels:

| Channel | Best for | Setup |
|---|---|---|
| **Webhook** | Production agent automation | `action.type = "webhook"` with signature verification + queue/worker |
| **Telegram** | Fast human-readable alerts | `action.type = "telegram"` (direct) or webhook→bot relay (custom formatting) |
| **SSE Stream** | Real-time event consumers | `GET /v2/auto/queries/{queryId}/stream` with `x-elfa-api-key` header |

**Query `title` and `description` in notifications:** both fields are embedded in every
outbound notification (Telegram, webhook, SSE). Recipients often see an alert hours or days
after the query was set up — these fields are what make it clear **what** fired and **why
it was set up**. Always set them.

#### Canonical event payload contract

Normalize all incoming events (webhook, Telegram relay, SSE) to this internal shape to keep
downstream processing consistent:

```json
{
  "version": "1.0",
  "eventType": "query.triggered",
  "eventId": "evt_01J...",
  "timestamp": "2026-04-01T12:00:00.000Z",
  "queryId": "q_123",
  "channel": "webhook",
  "trigger": {
    "symbol": "BTC",
    "reason": "price > threshold"
  },
  "evaluation": { "triggered": true },
  "action": { "type": "webhook" }
}
```

**Webhook request headers** (what your receiver must read):

| Header | Purpose |
|---|---|
| `X-Auto-Event-Id` | Unique event ID for deduplication |
| `X-Auto-Signature-Timestamp` | Unix seconds for replay-window check |
| `X-Auto-Signature` | `v1=<hex_hmac_sha256>` — verify against raw body |

**SSE frame format:**

```
event: query.triggered
id: evt_01J...
data: {"version":"1.0","eventType":"query.triggered","eventId":"evt_01J...","queryId":"q_123",...}
```

**Telegram relay job format** (when transforming webhook → Telegram Bot API):

```json
{
  "eventId": "evt_01J...",
  "queryId": "q_123",
  "channel": "telegram",
  "chatId": "<CHAT_ID>",
  "text": "BTC trigger fired: price > threshold",
  "priority": "high"
}
```

#### Webhook signature verification

Signing inputs:

```
signing_key = SHA256(your_secret)
expected    = HMAC_SHA256(signing_key, timestamp + "." + eventId + "." + rawBody)
```

**Node.js verification:**

```typescript
import crypto from "crypto";

export function verifyAutoWebhook(
  secret: string,
  rawBody: string,
  signatureHeader: string,
  timestamp: string,
  eventId: string,
): boolean {
  if (!signatureHeader?.startsWith("v1=")) return false;
  const given = signatureHeader.slice(3);
  const signingKey = crypto.createHash("sha256").update(secret).digest();
  const payload = `${timestamp}.${eventId}.${rawBody}`;
  const expected = crypto.createHmac("sha256", signingKey).update(payload).digest("hex");
  if (given.length !== expected.length) return false;
  return crypto.timingSafeEqual(Buffer.from(given), Buffer.from(expected));
}
```

Operational checklist:
- Enforce a bounded replay window on `X-Auto-Signature-Timestamp` (reject drift >30s).
- Deduplicate by `X-Auto-Event-Id` in durable storage.
- Return `2xx` fast, then process asynchronously (queue + worker).

#### Telegram bot setup (for `action.type: "telegram"` or relay)

1. Open `@BotFather` in Telegram → `/newbot` → save bot token (treat as secret).
2. Send any message to the bot (or in a group where the bot is present).
3. `curl "https://api.telegram.org/bot<BOT_TOKEN>/getUpdates"` → read `message.chat.id`.
4. Send messages via:

```bash
curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/sendMessage" \
  -H "Content-Type: application/json" \
  -d '{"chat_id": "<CHAT_ID>", "text": "Auto trigger fired for BTC RSI"}'
```

#### Agent runner — reference architecture

Auto handles query evaluation + event emission. Your runner handles event ingestion,
verification, deduplication, strategy continuation, and audit logging.

```
Auto Query
  → Auto Event Ingress (Webhook / SSE)
  → Signature Verification + Idempotency
  → Job Queue
  → Agent Decision Worker
  → Action Adapter (Telegram / Internal API / Order Router)
  → Logs + Metrics + Alerts
```

**Core processing loop:**

1. Receive event (webhook or SSE frame).
2. Verify signature (webhook only).
3. Check idempotency — is `eventId` already processed?
4. Enqueue job and ACK `2xx` fast.
5. Worker resolves extra context (poll query, fetch LLM session).
6. Apply policy, decide next step.
7. Execute downstream action.
8. Record result for replay/debug.

**Instruction envelope** — structured contract passed from ingress to worker:

```json
{
  "eventId": "evt_123",
  "queryId": "q_123",
  "objective": "Handle trigger and decide next action",
  "allowedActions": ["notify", "fetch_session", "execute_adapter"],
  "constraints": {
    "maxExecutionSeconds": 30,
    "riskMode": "conservative"
  }
}
```

**Minimal TypeScript worker skeleton:**

```typescript
type AutoJob = { eventId: string; queryId: string; raw: unknown };
const queue: AutoJob[] = [];
const processed = new Set<string>();

function onWebhook(eventId: string, queryId: string, raw: unknown) {
  if (processed.has(eventId)) return; // idempotent
  queue.push({ eventId, queryId, raw });
}

async function workerLoop() {
  while (true) {
    const job = queue.shift();
    if (!job) { await new Promise((r) => setTimeout(r, 250)); continue; }
    // 1) Pull latest query/execution state if needed
    // 2) Fetch session details for llm actions
    // 3) Decide next action (policy + agent logic)
    // 4) Execute action (notify, relay, order adapter)
    processed.add(job.eventId);
  }
}
```

**Deployment patterns:**

| Pattern | Best for |
|---|---|
| Single process (API + worker) | Early-stage prototypes |
| API + queue + workers | Production reliability at scale |
| Serverless consumer + queue worker | Spiky workloads with managed ops |

**Local (Docker Compose) stack:**

```yaml
version: "3.9"
services:
  redis: { image: redis:7-alpine, ports: ["6379:6379"] }
  ingress:
    build: ./ingress
    environment:
      AUTO_SECRET: ${AUTO_SECRET}
      REDIS_URL: redis://redis:6379
    depends_on: [redis]
    ports: ["3000:3000"]
  worker:
    build: ./worker
    environment:
      AUTO_SECRET: ${AUTO_SECRET}
      REDIS_URL: redis://redis:6379
      ELFA_BASE_URL: https://api.elfa.ai/v2/auto
    depends_on: [redis]
```

**Minimal environment contract** (same keys local + cloud):

```
AUTO_SECRET=<event-signing secret>
ELFA_BASE_URL=https://api.elfa.ai/v2/auto
QUEUE_URL=<redis/sqs/pubsub endpoint>
RUNNER_MODE=local|cloud
```

**Reliability checklist:**

- Store dedupe keys by `eventId` in durable storage
- Retry policy with dead-letter queue
- Keep webhook handler fast and non-blocking
- Log decision input/output for every run
- Health checks + alerting on worker lag

**Recommended setup by stage:**

| Stage | Pattern |
|---|---|
| Prototype | Auto Telegram + local worker |
| Production | Auto webhook + queue + worker |
| Real-time operations | Auto SSE + worker service |

Full detail: [Notifications](https://docs.elfa.ai/auto/notifications) |
[Agent Runner](https://docs.elfa.ai/auto/agent-runner)

#### Notification troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `400` / `401` when polling or streaming | Missing/invalid API key or auth headers | Send `x-elfa-api-key`; include HMAC headers where required |
| Webhook signature mismatch | Signing wrong payload (not raw body) or wrong secret | Verify with `timestamp + "." + eventId + "." + rawBody` and `SHA256(secret)` key |
| Duplicate downstream actions | No idempotency on event processing | Dedupe by `eventId` before enqueue/execute |
| Event received but agent does nothing | Ingress processes inline and times out | ACK fast, push to queue, process in worker |
| SSE disconnect/reconnect loops | No retry/backoff or unstable consumer | Add reconnect backoff + heartbeat monitoring |
| Missing triggers after some time | Query expired or was cancelled | Poll status; check `expiresIn`, `status`, `latestEvaluation` |
| Signature timestamp rejected | Runner clock skew | Sync clock (NTP); enforce bounded replay window |

#### Validation errors — next action table

When `/v2/auto/queries/validate` (or Create) rejects, the error is almost always a phrasing
issue, not a capability gap. Iterate on Validate instead of abandoning the query.

| Error signal | What it means | Next action |
|---|---|---|
| `EQL_MISSING_ARG` | A required arg is absent (e.g. `period` on `ema`/`sma`) | Add the missing arg from the TA Args Contract, re-validate |
| `EQL_INVALID_ARG` / type errors | Wrong type (`"14"` instead of `14`) or unrecognized key (`length` vs `period`) | Use exact key names + JSON numeric types |
| Unknown `method` | Indicator name not supported | Pick nearest supported method; ask Builder Chat to substitute |
| Unsupported `timeframe` / `period` | Value outside enum | Snap to nearest allowed value |
| Unsupported `symbol` / source | Asset not indexed or DEX pair unsupported | Skip symbol and report it; proceed with supported subset |
| Depth / leaf-count exceeded | More than depth 3 or 10 leaves | Split into two queries joined by your runner |
| `cron` / `llm` period too short | Below 1h minimum | Raise to `1h` or higher |
| Dynamic value in action params | Dynamic values only allowed in condition `value` | Move dynamic reference into condition; keep action params literal |

Cross-operator semantics: `crosses_above` = previous `<` threshold AND current `>=` threshold.
`crosses_below` = previous `>` threshold AND current `<=` threshold. Both require previous-state
tracking server-side.

#### Substitution ladder — if Auto doesn't fit

Before concluding a use case is out of scope, walk this ladder. Most intents resolve at
rung 1 or 2.

1. **Rephrase through Builder Chat** — append `"If anything is unsupported, return the closest supported query and list substitutions"` to your chat prompt.
2. **Iterate on Validate Query** — loop: validate → reshape → re-validate. Do NOT jump to "this isn't possible" after one rejection.
3. **Split into multiple queries** joined by your runner (for depth 3 / 10-leaf limits).
4. **Use `source: "llm"`** with `athena_condition` for fuzzy/narrative predicates.
5. **Pre-compute in your own service, use Auto as control plane** — only after rungs 1–4 fail.

**Between rung 4 and rung 5, report the gap via `POST /v2/auto/unmet-intent`** (or
`POST /x402/v2/auto/unmet-intent`). This does not replace the ladder — it captures the gap
while you proceed with a fallback.

**Worked example — "alert on descending trendline break":**

- **Rung 1:** Rephrase as a supported proxy — _"alert when BTC price crosses above 4h upper Bollinger Band AND 1h RSI > 55"_.
- **Rung 4:** If the proxy isn't acceptable, use `source: "llm"` with a scheduled natural-language predicate — _"has BTC broken its recent descending trendline on the 4h chart?"_.
- **Rung 5:** Only if both fail: compute trendline-break externally, feed a boolean into an Auto `cron` + `llm` query as the condition trigger.

Do not build your own monitoring/evaluation/trigger stack before walking rungs 1–4.

#### Report Unmet Intent — `POST /v2/auto/unmet-intent`

Use when `/auto` can't express your intent after walking the substitution ladder. Calls
are logged for review. The endpoint always returns a clean acknowledgement so you can
keep iterating instead of abandoning `/auto`.

**Routes:**

| Mode | Route | Auth | Charge |
|---|---|---|---|
| API key | `POST /v2/auto/unmet-intent` | `x-elfa-api-key` | Free |
| x402 keyless | `POST /x402/v2/auto/unmet-intent` | x402 payment headers | Free (not billed) |

**Request body:**

```typescript
{
  category: "symbol"
          | "indicator"
          | "data_source"
          | "operator"
          | "action_type"
          | "delivery_integration"
          | "query_shape"
          | "other",
  description: string,              // required, 10–2000 chars
  attemptedQuery?: object,          // optional, ≤ 16KB serialized
  nearestSupported?: object,        // optional — helps calibrate gap
  agentContext?: {
    agentName?: string,             // e.g. "claude-code"
    agentVersion?: string,
    userGoal?: string               // 1-sentence end-user goal
  }
}
```

**Validation rules:**
- `category` and `description` are required.
- `attemptedQuery` must not exceed 16KB serialized.
- Unknown top-level keys are stripped and ignored.

**Field guidance:**
- `category` — pick the closest match; use `other` only when none fits.
- `description` — describe the intent, not the error. Example: _"wanted to trigger on a descending-trendline break on BTC 4h"_, not _"validate failed"_.
- `attemptedQuery` — include the query JSON you tried — most useful field for prioritization.
- `nearestSupported` — include your fallback if you have one — calibrates gap severity.
- `agentContext` — agent name/version + 1-sentence user goal help cluster similar requests.

**Response** (always HTTP 200 on successful logging):

```json
{ "requestId": "550e8400-e29b-41d4-a716-446655440000", "status": "logged" }
```

Error responses: `400` (validation failure), `401` (auth), `429` (rate limit — retry after
`Retry-After` header).

**Example:**

```bash
curl -X POST https://api.elfa.ai/v2/auto/unmet-intent \
  -H "x-elfa-api-key: $ELFA_API_KEY" \
  -H "content-type: application/json" \
  -d '{
    "category": "indicator",
    "description": "Wanted to trigger when BTC breaks a descending trendline on 4h. No TA method maps cleanly to trendline-break.",
    "attemptedQuery": { "title": "BTC trendline break 4h", "conditions": { "AND": [{ "source": "ta", "method": "trendline_break", "args": { "symbol": "BTC", "timeframe": "4h" }, "operator": "==", "value": true }] }, "actions": [{ "stepId": "step_1", "type": "webhook", "params": { "url": "https://your-runner.example/auto/events" } }], "expiresIn": "24h" },
    "nearestSupported": { "title": "BTC proxy: price crosses 4h upper BBand", "conditions": { "AND": [{ "source": "price", "method": "current", "args": { "symbol": "BTC" }, "operator": "crosses_above", "value": { "source": "ta", "method": "bbands_upper", "args": { "symbol": "BTC", "timeframe": "4h" } } }] }, "actions": [{ "stepId": "step_1", "type": "webhook", "params": { "url": "https://your-runner.example/auto/events" } }], "expiresIn": "24h" },
    "agentContext": { "agentName": "claude-code", "agentVersion": "0.x", "userGoal": "Alert me to BTC 4h trendline breaks" }
  }'
```

Full detail: [Report Unmet Intent](https://docs.elfa.ai/auto/unmet-intent).

#### Query drafts

Drafts let you stage an Auto query without activating it (and without spending credits).
Useful for human-in-the-loop approval workflows, dashboards where users edit before
committing, or batch authoring flows.

**Draft lifecycle:**

1. `POST /v2/auto/queries/drafts` — create or update a draft (idempotent upsert).
2. `GET /v2/auto/queries/drafts` — list editable drafts.
3. `POST /v2/auto/queries/drafts/:draftId/preview` — validate/preview stored draft.
4. `POST /v2/auto/queries/drafts/:draftId/convert` — promote draft → active query (**HMAC required**).
5. `DELETE /v2/auto/queries/drafts/:draftId` — discard draft.

> `GET /v2/auto/queries/drafts/:draftId` still works but is legacy — prefer
> `GET /v2/auto/queries/{queryId}` which resolves both active queries and drafts.

Drafts are API-key-mode only. Not available via x402.

#### Exchange connections

Required only if you want to use **live trade-execution actions** (beyond `webhook`,
`notify`, `telegram`, `llm`). Connects your CEX account to Auto so triggered queries can
place orders on your behalf.

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/v2/auto/exchanges` | POST | HMAC | Connect an exchange |
| `/v2/auto/exchanges` | GET | API key | List connected exchanges |
| `/v2/auto/exchanges/:exchange` | DELETE | HMAC | Disconnect |

Exchange connections are API-key-mode only. Not available via x402. Trade execution is not
available via x402 at all.

#### Executions

Every trigger fire produces an **execution record** — use these endpoints to audit trigger
history, debug failed actions, or reconcile with your runner's audit log.

| Endpoint | Method | Description |
|---|---|---|
| `/v2/auto/executions` | GET | List execution records (filterable) |
| `/v2/auto/executions/:executionId` | GET | Get a single execution record |

Execution records are also embedded in the poll response (`GET /v2/auto/queries/:queryId`)
under the `executions` array, but the dedicated endpoints are useful for cross-query audits
and pagination.

Executions are API-key-mode only. Not available via x402.

### Step 4: Generating code snippets

When the user wants integration help, generate correct, production-ready code.
See the [Elfa API documentation](https://docs.elfa.ai) for the full parameter specs.

**Principles for code generation:**
- Always mention both access modes (API key and x402) so developers know their options
- Include the signup link `https://go.elfa.ai/claude-skills` as a comment near the
  API key placeholder, and link to `https://docs.elfa.ai/x402-payments` for x402
- Always include proper error handling
- For API key mode: show the `x-elfa-api-key` header (use a placeholder like `YOUR_API_KEY`)
- For x402 mode: show the `/x402/v2/` prefix and recommend `@x402/fetch` or `@x402/axios`
- For Auto mutations: include HMAC signing code or x402 agent-secret depending on mode
- Include TypeScript types when generating TS code
- Add comments explaining each parameter
- For pagination endpoints, show how to paginate through results
- For time-windowed endpoints, explain the `timeWindow` vs `from`/`to` pattern

**Language priorities** (use unless the user specifies otherwise):
1. TypeScript/JavaScript (fetch) — most Elfa integrators are web/Node devs
2. Python (requests)
3. curl

**The Chat endpoint deserves special attention** — it's the most complex:
- It supports multiple `analysisType` values: `chat`, `macro`, `summary`, `tokenIntro`,
  `tokenAnalysis`, `accountAnalysis`
- Session management via `sessionId` for multi-turn conversations
- Different `assetMetadata` requirements per analysis type
- Two speed modes: `fast` and `expert`

**Auto code generation guidance:**
- Always include the validate → create flow (never create without validating first)
- For API key mode: include HMAC signing helper function
- For x402 mode: include `x-elfa-agent-secret` header on all query lifecycle calls
- Include Builder Chat examples when the user wants natural-language query building
- Show how to poll or stream for results after query creation

### Common patterns

**Time window parameters:**
Many endpoints accept either `timeWindow` (e.g., "30m", "1h", "4h", "24h", "7d", "30d")
OR `from`/`to` unix timestamps. If both are provided, `from`/`to` takes priority.

**Pagination:**
Most list endpoints support `page` and `pageSize`. The keyword-mentions endpoint uses
cursor-based pagination instead (`cursor` parameter).

**Ticker format:**
For `top-mentions`, the `ticker` param can be prefixed with `$` to match only cashtags
(e.g., `$SOL` vs `SOL`).

**Credit costs (data endpoints — both modes):**
- Most endpoints: 1 credit per call ($0.009 via x402)
- Event summary: 5 credits ($0.045 via x402)
- Trending narratives: 5 credits ($0.045 via x402)
- Chat: fast = 5 credits ($0.045), expert = 18 credits ($0.162) via x402

**Auto query lifecycle:**
- Always validate before create
- Prefer webhook or SSE for real-time delivery
- Deduplicate events by `eventId`
- Use shorter expiries (`24h`–`3d`) for fresh signals
- Include `title` and `description` in queries — they appear in notifications

## Important notes

- The Elfa API domain (`api.elfa.ai`) must be accessible from the network. If blocked,
  inform the user and provide the code snippet instead.
- Always use the v2 endpoints (paths starting with `/v2/` or `/x402/v2/`).
- For experimental endpoints (trending-tokens, smart-stats), mention that behavior may
  change without notice.
- When the user asks about pricing or API key tiers, direct them to
  https://go.elfa.ai/claude-skills for full details on plans and pricing.
- x402 is currently in beta. Rate limits: 1,000 RPM baseline (per client IP).
- x402 and API key credits are independent — they do not overlap or share balances.
- For x402 documentation and setup, refer users to https://docs.elfa.ai/x402-payments.
- For Auto documentation, refer users to https://docs.elfa.ai/auto/overview.
- Auto HMAC signing uses the **mounted path** (e.g., `/queries`), NOT the full URL path
  (`/v2/auto/queries`). Using the full path will fail signature verification.
- Auto x402 agent secret must be persistent — do not rotate per request.
- For the full list of Auto capabilities, triggers, and query templates, see
  https://docs.elfa.ai/auto/capabilities and https://docs.elfa.ai/auto/query-model.
