#!/usr/bin/env bash
# install.sh — wire the accounting-skill repo into pi.
# Idempotent: safe to re-run after edits. Restart pi afterwards for skills to load.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="$HOME/.pi/agent/settings.json"

SKILL_DIRS=(
  "$REPO_DIR/skills/corp-accounting"
)

echo "==> Merging skill paths into $SETTINGS"
python3 - "$SETTINGS" "${SKILL_DIRS[@]}" << 'PY'
import json, sys, os

settings_path, *skill_dirs = sys.argv[1:]
settings = {}
if os.path.exists(settings_path):
    with open(settings_path, encoding="utf-8") as fh:
        settings = json.load(fh)

existing = settings.get("skills", [])
added = []
for d in skill_dirs:
    d = os.path.abspath(d)
    if d not in existing:
        existing.append(d)
        added.append(d)
settings["skills"] = existing

with open(settings_path, "w", encoding="utf-8") as fh:
    json.dump(settings, fh, indent=2, ensure_ascii=False)
    fh.write("\n")

if added:
    print("  added:", ", ".join(added))
else:
    print("  no changes (already present)")
PY

echo "==> Registering corp-accountant agent (repo is source of truth)"
AGENT_SRC="$REPO_DIR/agents/corp-accountant.md"
AGENT_DST="$HOME/.pi/agent/agents/corp-accountant.md"
mkdir -p "$(dirname "$AGENT_DST")"
cp "$AGENT_SRC" "$AGENT_DST"
echo "  installed to $AGENT_DST"

echo
echo "Done. Restart pi for the skill and agent to be discovered."
echo "Test: launch pi in the Finance vault and ask: 'process my corp accounting inbox'"
