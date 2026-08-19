---
description: Generate or refresh an AI-powered session title. Pass --skip-if none to force regeneration, --skip-if manual to refresh only auto-generated titles.
argument-hint: "[--skip-if any|manual|none] [--fallback none|first-prompt|placeholder]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/session-autoname.py *)
---

Run this command exactly, passing any user arguments. Print stdout verbatim. Do not modify, reformat, or interpret the output.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/session-autoname.py" --jsonl-tag command $ARGUMENTS
```

If the exit code is 2 (title already exists), print the output, then suggest:
- `/rename <the title>` to update the CLI header
- `/autoname --skip-if none` to force regeneration

If the exit code is 0 (title generated), print the output, then suggest:
- `/rename <the title>` to update the CLI header
