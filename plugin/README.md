# autoname

Auto-generate session titles via LLM at progressive message intervals.

## What it does

- **Stop hook** fires after each assistant turn. Counts user messages since the last auto-generated title. If the count crosses a threshold, generates a new title via LLM and appends it to the JSONL transcript.
- **`/autoname` command** lets you manually trigger title generation.
- Respects manual titles: if you used `/rename`, auto-titling stops for that session.
- Optionally renames the tmux window.

## CLI usage

```
session-autoname.py --jsonl <path> [OPTIONS]
session-autoname.py --index <path> [OPTIONS]
```

| Flag | Values | Default | What it does |
|------|--------|---------|-------------|
| `--jsonl <path>` | file path | | Parse a JSONL transcript |
| `--index <path>` | file path | | Parse a CSM index JSON |
| `--skip-if` | `any`, `manual`, `none` | `any` | When to skip: `any` = title exists, `manual` = only manual title, `none` = always regenerate |
| `--append-to-jsonl` | flag | off | Write the `custom-title` entry to the JSONL. Without this, just prints to stdout |
| `--fallback` | `none`, `first-prompt`, `placeholder` | `none` | What to use when LLM fails |
| `--jsonl-tag` | e.g. `hook`, `command` | `hook` | Label stored in the `source` field of the appended entry |
| `--model` | e.g. `sonnet`, `haiku` | from config | Override model |

Exit codes: 0 = success, 1 = error, 2 = skipped (title exists).

Backward-compatible aliases: `--overwrite`, `--source`, `--dry-run` are accepted silently.

## Configuration

Create `~/.claude/session-autoname.yaml` to override defaults:

```yaml
model: sonnet          # LLM model for title generation
effort: low            # Claude --effort flag
max_prompt_chars: 2000 # Max chars of user prompts sent to LLM
max_title_length: 60   # Truncate titles beyond this

hook_enabled: true     # Enable/disable the Stop hook
min_prompts: 2         # First title after N user messages
refresh_every: 10      # Re-title every N messages after that (0 = never refresh)
tmux_rename: true      # Rename tmux window on title change
```

If the file doesn't exist, bundled defaults are used.

## Limitations

Writing `custom-title` to the JSONL updates `claude --list` and session search, but does NOT update the live TUI header (top-right of the prompt box). Only `/rename` does that. The `/autoname` command generates a title and suggests the `/rename` command.
