#!/usr/bin/env bash
# start_receiver.sh: source .env and launch the receiver.
# Run from the project working directory (where .venv and .env live).
set -euo pipefail

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found in $(pwd). Copy .env.example and fill it in first." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "ERROR: .venv not found in $(pwd). Run install.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
set -a
# shellcheck disable=SC1091
source .env
set +a

echo "Starting receiver. Press Ctrl-C to stop."
exec python -m elfa_grvt_bot
