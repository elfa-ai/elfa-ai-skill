#!/usr/bin/env bash
# start_tunnel.sh: launch cloudflared and print the public URL.
# Run in a second terminal after the receiver is up.
set -euo pipefail

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "ERROR: cloudflared not installed. Install with:" >&2
  echo "  macOS:  brew install cloudflared" >&2
  echo "  Linux:  https://github.com/cloudflare/cloudflared/releases" >&2
  exit 1
fi

LOCAL_URL="${1:-http://localhost:8000}"

echo "Starting tunnel to $LOCAL_URL"
echo "Watch for the trycloudflare URL printed below; copy it into .env as RECEIVER_PUBLIC_URL."
echo
exec cloudflared tunnel --url "$LOCAL_URL"
