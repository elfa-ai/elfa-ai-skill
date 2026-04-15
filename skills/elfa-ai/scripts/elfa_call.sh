#!/usr/bin/env bash
# elfa_call.sh — Make authenticated Elfa API calls from the command line.
#
# Supports two auth modes:
#   API key:  Set ELFA_API_KEY in the environment (default).
#   x402:     Pass --x402 with a pre-signed payment header via --payment.
#
# Supports Auto endpoints with HMAC signing:
#   --hmac-secret  HMAC secret for mutation endpoints under /v2/auto/.
#   --agent-secret Agent identity secret for x402 Auto requests.
#
# Usage:
#   ./elfa_call.sh <endpoint> [options]
#
# Examples:
#   ./elfa_call.sh /v2/ping
#   ./elfa_call.sh /v2/aggregations/trending-tokens -q 'timeWindow=24h&pageSize=10'
#   ./elfa_call.sh /v2/data/top-mentions -q 'ticker=$SOL&timeWindow=24h'
#   ./elfa_call.sh /v2/chat -d '{"message":"What is trending?","analysisType":"chat"}'
#   ./elfa_call.sh /v2/aggregations/trending-tokens --x402 --payment '<base64-payload>'

set -euo pipefail

BASE_URL="https://api.elfa.ai"

usage() {
  cat <<'EOF'
elfa_call.sh — Make authenticated Elfa API calls from the command line.

Supports two auth modes:
  API key:  Set ELFA_API_KEY in the environment (default).
  x402:     Pass --x402 with a pre-signed payment header via --payment.

Usage:
  ./elfa_call.sh <endpoint> [options]

Options:
  -q, --query <params>        Query string (e.g. 'timeWindow=24h&pageSize=10')
  -X, --method <METHOD>       HTTP method (default: GET, auto-set to POST with -d)
  -d, --data <json>           Request body (JSON). Implies -X POST.
  --x402                      Use x402 keyless mode (rewrites /v2/ to /x402/v2/).
  --payment <payload>         Pre-signed x402 payment header (base64). Requires --x402.
  --hmac-secret <secret>      HMAC secret for Auto mutation endpoints (POST/DELETE on
                              /v2/auto/). Can also be set via ELFA_HMAC_SECRET env var.
  --agent-secret <secret>     Agent identity secret added as x-elfa-agent-secret header
                              when using --x402 with Auto endpoints. Can also be set via
                              ELFA_AGENT_SECRET env var.
  -h, --help                  Show this help

Auto endpoint HMAC signing:
  Mutation endpoints under /v2/auto/ require an HMAC-SHA256 signature. When
  --hmac-secret is provided (or ELFA_HMAC_SECRET is set) and the request is a
  POST or DELETE to an /auto/ path, the following headers are added automatically:

    x-elfa-timestamp: <unix_seconds>
    x-elfa-signature: <hex_hmac_sha256>

  The signature payload is: timestamp + METHOD + mounted_path + body
  where mounted_path is the portion of the path AFTER /v2/auto.
  Example: /v2/auto/queries  →  mounted_path = /queries
           /v2/auto/chat     →  mounted_path = /chat
           /v2/auto/queries/q_123  →  mounted_path = /queries/q_123

Examples:
  # API key mode (reads ELFA_API_KEY from env)
  ./elfa_call.sh /v2/ping
  ./elfa_call.sh /v2/aggregations/trending-tokens -q 'timeWindow=24h&pageSize=10'
  ./elfa_call.sh /v2/chat -d '{"message":"What is trending?","analysisType":"chat"}'

  # x402 mode (pay-per-request with USDC on Base)
  ./elfa_call.sh /v2/aggregations/trending-tokens --x402 --payment '<base64-payload>'

  # Auto: validate a query (read-only, no HMAC needed)
  ./elfa_call.sh /v2/auto/queries/validate -d '{"query":{...}}'

  # Auto: create a query (mutation, HMAC auto-applied)
  ./elfa_call.sh /v2/auto/queries -d '{"query":{...}}' --hmac-secret "$ELFA_HMAC_SECRET"

  # Auto: cancel a query (mutation, HMAC auto-applied)
  ./elfa_call.sh /v2/auto/queries/q_123 -X DELETE --hmac-secret "$ELFA_HMAC_SECRET"

  # Auto x402: create query with agent secret
  ./elfa_call.sh /v2/auto/queries --x402 --payment '<payload>' --agent-secret "$ELFA_AGENT_SECRET"
EOF
  exit 0
}

die() { echo "error: $1" >&2; exit 1; }

# Format JSON — prefer jq, fall back to python3
fmt_json() {
  if command -v jq &>/dev/null; then
    jq .
  elif command -v python3 &>/dev/null; then
    python3 -m json.tool
  else
    cat
  fi
}

# Parse arguments
METHOD="GET"
QUERY=""
BODY=""
X402=false
PAYMENT=""
HMAC_SECRET="${ELFA_HMAC_SECRET:-}"
AGENT_SECRET="${ELFA_AGENT_SECRET:-}"

[[ $# -eq 0 ]] && usage
[[ "$1" == "-h" || "$1" == "--help" ]] && usage

ENDPOINT="$1"; shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    -X|--method)        [[ $# -lt 2 ]] && die "-X requires a value"; METHOD="$2"; shift 2 ;;
    -q|--query)         [[ $# -lt 2 ]] && die "-q requires a value"; QUERY="$2"; shift 2 ;;
    -d|--data)          [[ $# -lt 2 ]] && die "-d requires a value"; BODY="$2"; METHOD="POST"; shift 2 ;;
    --x402)             X402=true; shift ;;
    --payment)          [[ $# -lt 2 ]] && die "--payment requires a value"; PAYMENT="$2"; shift 2 ;;
    --hmac-secret)      [[ $# -lt 2 ]] && die "--hmac-secret requires a value"; HMAC_SECRET="$2"; shift 2 ;;
    --agent-secret)     [[ $# -lt 2 ]] && die "--agent-secret requires a value"; AGENT_SECRET="$2"; shift 2 ;;
    -h|--help)          usage ;;
    *)                  die "unknown option: $1" ;;
  esac
done

# Normalize method to uppercase (use tr for bash 3.x / macOS compat)
METHOD=$(printf '%s' "$METHOD" | tr '[:lower:]' '[:upper:]')

# Validate
[[ "$ENDPOINT" == /* ]] || die "endpoint must start with / (e.g. /v2/ping)"

if [[ "$X402" == true ]]; then
  # Rewrite /v2/ → /x402/v2/ if not already prefixed
  [[ "$ENDPOINT" == /v2/* ]] && ENDPOINT="/x402${ENDPOINT}"
  [[ -n "$PAYMENT" ]] || die "--x402 requires --payment <payload>. See https://docs.elfa.ai/x402-payments"
else
  [[ -z "${ELFA_API_KEY:-}" ]] && die "ELFA_API_KEY is not set. Get a free key at https://go.elfa.ai/claude-skills"
fi

# Determine whether this is an Auto mutation that requires HMAC signing.
# We check the *original* endpoint (before x402 rewrite) by examining whether
# the path contains /auto/ and the method is POST or DELETE.
IS_AUTO_MUTATION=false
IS_AUTO_ENDPOINT=false
# Use the original endpoint for detection (strip /x402 prefix if present)
ORIGINAL_ENDPOINT="${ENDPOINT#/x402}"
if [[ "$ORIGINAL_ENDPOINT" == /v2/auto/* ]]; then
  IS_AUTO_ENDPOINT=true
  # Mutations need HMAC signing — POST or DELETE, but NOT /queries/validate (read-only POST)
  if [[ "$METHOD" == "POST" || "$METHOD" == "DELETE" ]]; then
    # Exclude validate endpoint — it's a POST but doesn't require HMAC
    MOUNTED_CHECK="${ORIGINAL_ENDPOINT#/v2/auto}"
    if [[ "$MOUNTED_CHECK" != "/queries/validate" ]]; then
      IS_AUTO_MUTATION=true
    fi
  fi
fi

# Build URL
URL="${BASE_URL}${ENDPOINT}"
[[ -n "$QUERY" ]] && URL="${URL}?${QUERY}"

# Build curl args
CURL_ARGS=(-s --fail-with-body --max-time 30 -X "$METHOD")

if [[ "$X402" == true ]]; then
  CURL_ARGS+=(-H "X-PAYMENT: ${PAYMENT}")
  # Add agent-secret header only for x402 Auto requests when provided
  if [[ -n "$AGENT_SECRET" ]] && [[ "$IS_AUTO_ENDPOINT" == true ]]; then
    CURL_ARGS+=(-H "x-elfa-agent-secret: ${AGENT_SECRET}")
  fi
else
  CURL_ARGS+=(-H "x-elfa-api-key: ${ELFA_API_KEY}")
fi

if [[ -n "$BODY" ]]; then
  CURL_ARGS+=(-H "Content-Type: application/json" -d "$BODY")
fi

# HMAC signing for Auto mutation endpoints
if [[ "$IS_AUTO_MUTATION" == true ]]; then
  if [[ -n "$HMAC_SECRET" ]]; then
    # Extract mounted_path: strip /v2/auto from the original endpoint
    MOUNTED_PATH="${ORIGINAL_ENDPOINT#/v2/auto}"
    # Ensure mounted_path starts with /
    [[ "$MOUNTED_PATH" == /* ]] || MOUNTED_PATH="/${MOUNTED_PATH}"

    TIMESTAMP=$(date +%s)
    SIGN_PAYLOAD="${TIMESTAMP}${METHOD}${MOUNTED_PATH}${BODY}"
    SIGNATURE=$(printf '%s' "$SIGN_PAYLOAD" | openssl dgst -sha256 -hmac "$HMAC_SECRET" | sed 's/^.* //')

    CURL_ARGS+=(-H "x-elfa-timestamp: ${TIMESTAMP}")
    CURL_ARGS+=(-H "x-elfa-signature: ${SIGNATURE}")
  else
    die "Auto mutation endpoint (${METHOD} ${ORIGINAL_ENDPOINT}) requires an HMAC secret. Set ELFA_HMAC_SECRET in your environment or pass --hmac-secret <secret>."
  fi
fi

# Execute
HTTP_BODY=$(curl "${CURL_ARGS[@]}" -w '\n%{http_code}' "$URL") || true

HTTP_CODE=$(echo "$HTTP_BODY" | tail -n1)
RESPONSE=$(echo "$HTTP_BODY" | sed '$d')

if [[ "$HTTP_CODE" -ge 200 && "$HTTP_CODE" -lt 300 ]] 2>/dev/null; then
  echo "$RESPONSE" | fmt_json
else
  echo "HTTP $HTTP_CODE" >&2
  echo "$RESPONSE" | fmt_json >&2
  exit 1
fi
