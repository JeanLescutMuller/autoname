#!/bin/bash
# stop-hook.sh - Async Stop hook for session auto-titling.
#
# Fires after each assistant turn. Generates a title when user message count
# since the last auto-title crosses the configured threshold.
#
# Config: settings.yaml at plugin root
# No temp files. All state derived from the JSONL itself.

set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
SCRIPT="$PLUGIN_ROOT/scripts/session-autoname.py"
CONFIG="$PLUGIN_ROOT/settings.yaml"

# Read hook JSON from stdin
INPUT=$(cat) || exit 0
TP=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null) || exit 0

[ -z "$TP" ] || [ ! -f "$TP" ] && exit 0
[ ! -f "$SCRIPT" ] && exit 0

# -- Fast check: should we generate a title? (python_forward, ~5-90ms) ----

DECISION=$(python3 - "$TP" "$CONFIG" << 'PYEOF'
import json, sys

tp, config_path = sys.argv[1], sys.argv[2]

# Read config
min_prompts = 2
refresh_every = 10
hook_enabled = True

try:
    with open(config_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            if "#" in val:
                val = val[:val.index("#")].strip()
            if key == "hook_enabled":
                hook_enabled = val.lower() in ("true", "yes", "1")
            elif key == "min_prompts":
                min_prompts = int(val)
            elif key == "refresh_every":
                refresh_every = int(val)
except Exception:
    pass

if not hook_enabled:
    print("skip")
    sys.exit(0)

# Forward scan: find last custom-title and count user messages after it
last_title = None
has_title = False
user_count_after = 0
total_user_count = 0

try:
    with open(tp, encoding="utf-8", errors="replace") as f:
        for raw in f:
            if '"custom-title"' in raw:
                try:
                    e = json.loads(raw)
                    if e.get("type") == "custom-title":
                        has_title = True
                        last_title = e.get("customTitle", "")
                        user_count_after = 0
                        continue
                except Exception:
                    pass
            if '"type":"user"' in raw or '"type": "user"' in raw:
                total_user_count += 1
                user_count_after += 1
except Exception:
    print("skip")
    sys.exit(0)

# Decision logic
is_auto = last_title is not None and last_title.endswith("(auto-generated)")

if not has_title:
    # No title yet: generate if enough messages
    if total_user_count >= min_prompts:
        print("generate")
    else:
        print("skip")
elif not is_auto:
    # Manual title (doesn't end with "(auto-generated)"): never overwrite
    print("skip")
elif refresh_every <= 0:
    # refresh disabled: one auto-title is enough
    print("skip")
elif user_count_after >= refresh_every:
    print("generate")
else:
    print("skip")
PYEOF
) || exit 0

[ "$DECISION" != "generate" ] && exit 0

# -- Generate title --------------------------------------------------------

TITLE=$(python3 "$SCRIPT" --jsonl "$TP" --skip-if manual --append-to-jsonl --jsonl-tag hook 2>/dev/null) || exit 0
[ -z "$TITLE" ] && exit 0

# -- Rename tmux window ---------------------------------------------------

TMUX_RENAME="true"
if [ -f "$CONFIG" ]; then
    val=$(grep "^tmux_rename:" "$CONFIG" 2>/dev/null | head -1 | cut -d: -f2 | tr -d ' ')
    [ -n "$val" ] && TMUX_RENAME="$val"
fi

if [ "$TMUX_RENAME" = "true" ] && [ -n "${TMUX_PANE:-}" ]; then
    SHORT=$(printf '%s' "$TITLE" | cut -c1-30 | tr '[:upper:]' '[:lower:]')
    tmux rename-window "$SHORT" 2>/dev/null || true
fi
