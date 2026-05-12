#!/usr/bin/env bash
# Surface pending registry alerts so an agent can relay them in chat. The
# receiver writes every alert (trigger fired, order placed, error, warning)
# to the SQLite registry; this script prints any unacked alerts as plain
# text. This is the in-chat counterpart to Telegram and works whether
# Telegram is configured or not.
#
# Agents should run this on every session start (AGENTS.md instructs this)
# or whenever the user asks about strategy status. Wire it into your
# agent's session-start / pre-prompt hook if your CLI supports it.
#
# Behavior:
#   - exits 0 silently if there's nothing to report (no alerts, no DB yet,
#     missing env, registry_cli not importable)
#   - never blocks the user prompt (stderr swallowed, errors non-fatal)
#   - auto-acks alerts after printing so the same item isn't repeated

set -u

PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$PROJECT_ROOT" || exit 0

# Pull REGISTRY_DB_PATH and friends from .env if present. Stay quiet on
# failure: the hook must never break the user's prompt flow.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env 2>/dev/null || true
  set +a
fi

if [[ -z "${REGISTRY_DB_PATH:-}" ]]; then
  exit 0
fi
if [[ ! -f "$REGISTRY_DB_PATH" ]]; then
  exit 0
fi

# Pick the project venv python if it exists; otherwise fall back to
# whatever python3 is on PATH. Some agent CLIs spawn shells that don't
# inherit an active venv, so we look it up explicitly.
PY="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$( command -v python3 || command -v python || true )"
fi
if [[ -z "$PY" ]]; then
  exit 0
fi

OUTPUT="$( PYTHONPATH="$PROJECT_ROOT/src" "$PY" -m registry_cli alerts --pending 2>/dev/null || true )"
if [[ -z "$OUTPUT" || "$OUTPUT" == "no alerts" ]]; then
  exit 0
fi

echo "=== elfa-grvt-bot: pending alerts (auto-surfaced from registry) ==="
echo "$OUTPUT"
echo "=== end pending alerts ==="
echo
echo "Relay any new triggers / orders / errors above to the user in this turn,"
echo "then run: python -m registry_cli ack all   (to clear the queue)"

exit 0
