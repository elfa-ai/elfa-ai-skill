#!/usr/bin/env bash
# Build the .skill ZIP package for Claude Desktop.
#
# Users can attach this single file to a Claude Desktop conversation
# instead of copying individual files.
#
# Usage:
#   ./scripts/build-skill.sh
#
# Output:
#   dist/elfa-api.skill

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$REPO_ROOT/dist"
SKILL_NAME="elfa-api"
OUTPUT="$DIST_DIR/${SKILL_NAME}.skill"

rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

# Stage files under elfa-api/ directory and zip
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT

mkdir -p "$STAGING/$SKILL_NAME/references" "$STAGING/$SKILL_NAME/scripts"
cp "$REPO_ROOT/SKILL.md" "$STAGING/$SKILL_NAME/"
cp "$REPO_ROOT/references/swagger.json" "$STAGING/$SKILL_NAME/references/"
cp "$REPO_ROOT/scripts/elfa_call.sh" "$STAGING/$SKILL_NAME/scripts/"

cd "$STAGING"
zip -r "$OUTPUT" "$SKILL_NAME/"

echo "Built: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
