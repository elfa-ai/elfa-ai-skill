#!/usr/bin/env bash
# install.sh: bootstrap a new elfa-grvt-bot working copy from this skill bundle.
#
# Usage:
#   bash <skill-path>/scripts/install.sh [target-dir]
#
# Default target-dir is ~/elfa_grvt_bot. The script copies the bundled source,
# creates a venv, installs deps, and runs the test suite.
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SKILL_ROOT="$( dirname "$SCRIPT_DIR" )"
SOURCE_DIR="$SKILL_ROOT/assets/source"
TARGET="${1:-$HOME/elfa_grvt_bot}"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "ERROR: bundled source not found at $SOURCE_DIR" >&2
  exit 1
fi

echo "Installing elfa-grvt-bot to $TARGET"
mkdir -p "$TARGET"

if [[ -n "$(ls -A "$TARGET" 2>/dev/null)" ]]; then
  echo "WARNING: $TARGET is not empty. Files may be overwritten."
  read -r -p "Continue? [y/N] " ans
  case "$ans" in
    [yY]*) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

cp -R "$SOURCE_DIR"/. "$TARGET/"
cd "$TARGET"

# Python environment.
if [[ ! -d .venv ]]; then
  echo "Creating venv at .venv"
  python3.11 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e ".[dev]"

echo
echo "Running test suite..."
pytest -q || {
  echo "Tests failed. Investigate before proceeding."
  exit 1
}

echo
echo "Install complete."
echo
echo "Next steps:"
echo "  1. cd $TARGET"
echo "  2. cp .env.example .env"
echo "  3. Edit .env with your API credentials (see references/setup.md)."
echo "  4. source .venv/bin/activate && set -a && source .env && set +a"
echo "  5. python -m elfa_grvt_bot   (in one terminal)"
echo "  6. cloudflared tunnel --url http://localhost:8000   (in another)"
echo "  7. Set RECEIVER_PUBLIC_URL in .env to the trycloudflare URL."
echo "  8. Re-source .env and start authoring strategies in your preferred agent."
