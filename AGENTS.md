# autoname

Claude-templates plugin that auto-generates session titles via LLM.

## How it works

- **Stop hook** fires after each assistant turn. Counts user messages in the JSONL since the last auto-generated title. If the count crosses a threshold, calls `session-autoname.py` to generate a new title via LLM.
- **`/autoname` command** lets the user manually trigger title generation.
- **`session-autoname.py`** is the core script. Two modes: `--jsonl` (live sessions) and `--index` (asm orphan backfill).

## CLI options

| Flag | Values | Default | What it does |
|------|--------|---------|-------------|
| `--jsonl <path>` | file path | | Parse a JSONL transcript |
| `--index <path>` | file path | | Parse an asm index JSON |
| `--skip-if` | `any`, `manual`, `none` | `any` | When to skip: `any` = title exists, `manual` = only manual title, `none` = always regenerate |
| `--append-to-jsonl` | flag | off | Write the `custom-title` entry to the JSONL. Without this, just prints to stdout |
| `--fallback` | `none`, `first-prompt`, `placeholder` | `none` | What to use when LLM fails |
| `--jsonl-tag` | e.g. `hook`, `command` | `hook` | Label stored in the `source` field of the appended entry |
| `--model` | e.g. `sonnet`, `haiku` | from config | Override model |

Exit codes: 0 = success, 1 = error, 2 = skipped (title exists).

Backward-compatible aliases accepted: `--overwrite` (maps to `--skip-if`), `--source` (maps to `--jsonl-tag`), `--dry-run` (no-op, print-only is already the default).

## Config

User config at `~/.claude/session-autoname.yaml`. Falls back to bundled defaults in `settings.yaml`.

```yaml
model: sonnet          # LLM model
effort: low            # Claude --effort flag
max_prompt_chars: 2000 # Max chars sent to LLM
max_title_length: 60   # Truncate titles beyond this

hook_enabled: true     # Enable/disable Stop hook
min_prompts: 2         # First title after N user messages
refresh_every: 10      # Re-title every N messages after that (0 = never refresh)
tmux_rename: true      # Rename tmux window on title change
```

## File structure

```
autoname/                              # project root (gdrive)
├-- CLAUDE.md                          # project docs (local only)
├-- release.conf                       # release script config
├-- landscape.md                       # competitive landscape survey
├-- tests/
│   └-- test_autoname.py               # 66 unit tests
└-- plugin/                            # everything here syncs to fbsource
    ├-- .claude-plugin/
    │   └-- plugin.json                # plugin definition, hook registration
    ├-- scripts/
    │   ├-- session-autoname.py        # core title generator
    │   └-- stop-hook.sh               # async Stop hook
    ├-- commands/
    │   └-- autoname.md                # /autoname command
    ├-- settings.yaml                  # bundled default config
    └-- README.md                      # marketplace description
```

## Deploy

Sync command: `rsync -av --delete ./plugin/ ~/fbsource/fbcode/claude-templates/components/plugins/autoname/`

Use `release` script: `! release ~/gdrive/secondbrain/03_tools/autoname/`

## Stop hook logic

No temp files. All state derived from the JSONL:

1. Forward-scan JSONL (~5-90ms depending on file size)
2. Find last `custom-title` entry, note its `source` field
3. Count user messages after it
4. Decision:
   - No title yet + messages >= `min_prompts` → generate
   - Last title is manual (no `(auto-generated)` suffix) → skip
   - Last title is auto + `refresh_every` <= 0 → skip
   - Last title is auto + messages since >= `refresh_every` → generate
   - Otherwise → skip

## TUI header limitation

Writing `custom-title` to JSONL updates `claude --list` and asm index, but does NOT update the live TUI header. Only `/rename` (typed by the user) does that.

## To do

- [ ] Validate with `claude-templates plugin autoname install --dev`, test in a new session
- [ ] Test: send 3 messages, verify auto-title appears in JSONL
- [ ] Test: `/autoname` command works, suggests `/rename`
- [ ] Test: manual `/rename` prevents auto-overwrite
- [ ] Test: tmux window rename (if tmux active)
- [ ] Test: `refresh_every` triggers re-titling after N messages past last auto-title
- [ ] Add `homepage` to `plugin.json` after publishing (e.g., Workplace post or wiki URL)
- [ ] Clean up old artifacts (`~/.claude/hooks/auto-rename-session.py`, old hook entries in `settings.json`)
