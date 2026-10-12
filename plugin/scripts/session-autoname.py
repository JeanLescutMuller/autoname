#!/usr/bin/env python3
"""session-autoname.py - Generate session titles via LLM and write them to JSONL or index JSON.

Modes:
    session-autoname.py [OPTIONS]                   Auto-discover JSONL from session env vars
    session-autoname.py --jsonl <path> [OPTIONS]    Parse JSONL, generate title, write custom-title
    session-autoname.py --index <path> [OPTIONS]    Parse index JSON, generate title, update index

Options:
    --skip-if <v>        Skip policy: any (default), manual, none
    --append-to-jsonl    Write the custom-title entry to the JSONL (default: print only)
    --fallback <v>       When LLM fails: none (default), first-prompt, placeholder
    --model <model>      Override model from config
    --jsonl-tag <tag>    Metadata label stored in the source field (e.g., "hook", "command")

Exit codes:
    0  Success (title printed to stdout)
    1  Error (no prompts, LLM failure, invalid input)
    2  Skipped (title exists, use --skip-if none to override)
"""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logging.basicConfig(format="%(levelname)s: %(message)s")
log = logging.getLogger("autoname")

# -- Defaults --------------------------------------------------------------

DEFAULTS = {
    "model": "sonnet",
    "effort": "low",
    "max_prompt_chars": 2000,
    "max_title_length": 60,
}

REFUSAL_PREFIXES = ("I ", "I'", "Please ", "Could you", "Failed", "Error", "API", "Unauthorized", "{", "```")

SYSTEM_PROMPT = (
    "Summarize the following work session into a single 5-8 word title.\n"
    "Respond with ONLY the title. No quotes, no punctuation at the end, no explanation.\n"
    "\nRules:\n"
    "- Output EXACTLY ONE LINE: the title. Nothing else.\n"
    "- Summarize the overall topic across all prompts, not just the first one.\n"
    "- Do NOT ask questions, refuse, or add commentary.\n"
    "- Do NOT use quotes around the title.\n"
    "- Infer the topic from whatever text is provided.\n"
)

HOME = str(Path.home())
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(os.path.dirname(SCRIPT_DIR), "settings.yaml")
_CLAUDE_ENV = {**os.environ, "PATH": f"/usr/local/bin:{os.environ.get('PATH', '/usr/bin:/bin')}"}


# -- Config ----------------------------------------------------------------

def _parse_config_line(line: str) -> Optional[Tuple[str, str]]:
    """Parse a single config line into (key, value) or None."""
    line = line.strip()
    if not line or line.startswith("#") or ":" not in line:
        return None
    key, _, val = line.partition(":")
    key, val = key.strip(), val.strip()
    if "#" in val:
        val = val[:val.index("#")].strip()
    if key not in DEFAULTS:
        return None
    return key, val


def _coerce_config_value(key: str, val: str) -> object:
    """Coerce a string value to the type matching DEFAULTS[key]."""
    default_type = type(DEFAULTS[key])
    if default_type == bool:
        return val.lower() in ("true", "yes", "1")
    if default_type == int:
        try:
            return int(val)
        except ValueError:
            return DEFAULTS[key]
    return val


def load_config(config_path: str = DEFAULT_CONFIG) -> Dict:
    """Load config from YAML file, falling back to defaults for missing keys.
    Uses simple line parsing to avoid PyYAML dependency."""
    config = dict(DEFAULTS)
    if not os.path.isfile(config_path):
        return config
    try:
        with open(config_path, encoding="utf-8") as f:
            for line in f:
                parsed = _parse_config_line(line)
                if parsed is None:
                    continue
                key, val = parsed
                config[key] = _coerce_config_value(key, val)
    except OSError:
        pass
    return config


# -- JSONL Parsing ---------------------------------------------------------

def _extract_user_text(entry: Dict) -> Optional[str]:
    """Extract clean text from a user message entry."""
    content = entry.get("message", {}).get("content", "")

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    return None
        text = " ".join(parts)
    elif isinstance(content, str):
        text = content
    else:
        return None

    # Strip noise
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL)
    text = re.sub(r"<command-name>.*?</command-name>", "", text, flags=re.DOTALL)
    text = re.sub(r"<command-message>.*?</command-message>", "", text, flags=re.DOTALL)
    text = re.sub(r"<command-args>.*?</command-args>", "", text, flags=re.DOTALL)
    text = re.sub(r"<local-command-stdout>.*?</local-command-stdout>", "", text, flags=re.DOTALL)
    text = re.sub(r"<role>.*?</role>", "", text, flags=re.DOTALL)
    text = re.sub(r"<source_space>.*?</source_space>", "", text, flags=re.DOTALL)
    text = re.sub(r"<instructions>.*?</instructions>", "", text, flags=re.DOTALL)
    text = re.sub(r"<task-notification\b[^>]*>.*?</task-notification>", "", text, flags=re.DOTALL)
    text = re.sub(r"<message\b[^>]*>.*?</message>", "", text, flags=re.DOTALL)
    text = text.strip()

    if not text or text.startswith("/"):
        return None

    if text.startswith("Base directory for this skill"):
        return None
    if "<available_skills>" in text or "<available-deferred-tools>" in text:
        return None
    if "<local-command-caveat>" in text:
        return None

    return text


def parse_jsonl(jsonl_path: str) -> Tuple[Optional[str], Optional[str], Optional[str], List[str]]:
    """Parse a Claude Code JSONL transcript.
    Returns (session_id, custom_title, custom_title_source, user_messages)."""
    session_id = None
    custom_title = None
    custom_title_source = None
    messages: List[str] = []

    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                etype = entry.get("type", "")

                if etype == "custom-title":
                    custom_title = entry.get("customTitle") or custom_title
                    custom_title_source = entry.get("source")
                    session_id = entry.get("sessionId") or session_id
                    continue

                if not session_id:
                    session_id = entry.get("sessionId")

                if etype == "user":
                    text = _extract_user_text(entry)
                    if text:
                        messages.append(text)

    except OSError as e:
        log.error("could not read %s: %s", jsonl_path, e)

    return session_id, custom_title, custom_title_source, messages


# -- Prompts + LLM ---------------------------------------------------------

def build_prompts_text(messages: List[str], max_chars: int) -> str:
    """Build concatenated prompts text from user messages, capped at max_chars."""
    parts: List[str] = []
    total = 0
    for text in messages:
        if total + len(text) > max_chars:
            remaining = max_chars - total
            if remaining > 50:
                parts.append(text[:remaining] + "...")
            break
        parts.append(text)
        total += len(text) + 1
    return "\n---\n".join(parts)


def generate_title(prompts_text: str, config: Dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Call Claude CLI to generate a title.
    Returns (title, error, raw_output)."""
    if not prompts_text:
        return None, "no user prompts", None

    model = config.get("model", DEFAULTS["model"])
    effort = config.get("effort", DEFAULTS["effort"])
    max_title_length = config.get("max_title_length", DEFAULTS["max_title_length"])

    try:
        result = subprocess.run(
            [
                "claude", "-p", prompts_text,
                "--model", model,
                "--effort", effort,
                "--strict-mcp-config",
                "--tools", "",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--no-chrome",
                "--setting-sources", "",
                "--system-prompt", SYSTEM_PROMPT,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
            env=_CLAUDE_ENV,
            cwd=HOME,
        )
        if result.returncode != 0:
            return None, f"exit {result.returncode}: {result.stderr[:200].strip()}", None

        raw = result.stdout.strip()
        cleaned = raw
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```\w*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
        title = cleaned.strip("\"'").split("\n")[0].strip()

        if not title:
            return None, f"invalid title: {raw[:100]}", raw
        if len(title) > max_title_length:
            title = title[: max_title_length - 3] + "..."
        if any(title.startswith(p) for p in REFUSAL_PREFIXES):
            return None, f"LLM refusal: {title[:80]}", raw

        return title, None, raw
    except subprocess.TimeoutExpired:
        return None, "timeout (120s)", None
    except Exception as e:
        return None, str(e), None


# -- Skip check -----------------------------------------------------------

def _should_skip(title: str, auto_suffix: str, skip_if: str) -> bool:
    """Return True if the existing title should be preserved."""
    if not title:
        return False
    is_auto = title.endswith(auto_suffix)
    if skip_if == "none":
        return False
    if skip_if == "manual" and is_auto:
        return False
    return True


# -- Fallback -------------------------------------------------------------

def _apply_fallback(fallback: str, l_messages: List[str], max_title_length: int) -> Optional[str]:
    """Generate a fallback title when LLM fails. Returns None if fallback is 'none'."""
    if fallback == "placeholder":
        return "Untitled session"
    if fallback == "first-prompt" and l_messages:
        first = l_messages[0].strip()
        if len(first) > max_title_length:
            first = first[: max_title_length - 3] + "..."
        return first
    return None


# -- Mode: --jsonl ---------------------------------------------------------

def cmd_jsonl(jsonl_path: str, config: Dict, skip_if: str = "any",
              append_to_jsonl: bool = False, jsonl_tag: str = "hook",
              fallback: str = "none") -> int:
    """Generate title from JSONL transcript."""
    session_id, title, _, l_messages = parse_jsonl(jsonl_path)

    if _should_skip(title, "(auto-generated)", skip_if):
        print(title)
        return 2

    if not l_messages:
        log.error("no user messages found")
        return 1

    max_chars = config.get("max_prompt_chars", DEFAULTS["max_prompt_chars"])
    max_title_length = config.get("max_title_length", DEFAULTS["max_title_length"])
    prompts_text = build_prompts_text(l_messages, max_chars)
    new_title, err, raw = generate_title(prompts_text, config)

    if not new_title:
        fb = _apply_fallback(fallback, l_messages, max_title_length)
        if fb:
            new_title = fb
        else:
            log.error("%s", err)
            return 1

    new_title = f"{new_title} (auto-generated)"

    if append_to_jsonl:
        if not session_id:
            log.warning("no session_id found, title not persisted")
        else:
            entry = {
                "type": "custom-title",
                "customTitle": new_title,
                "sessionId": session_id,
                "source": jsonl_tag,
            }
            try:
                with open(jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError as e:
                log.warning("could not write to %s: %s", jsonl_path, e)

    print(new_title)
    return 0


# -- Mode: --index ---------------------------------------------------------

def _read_index(idx_path: str) -> Optional[Dict]:
    try:
        with open(idx_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("could not read %s: %s", idx_path, e)
        return None


def _write_index(idx_path: str, doc: Dict) -> None:
    tmp_path = idx_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, idx_path)
    except OSError as e:
        log.warning("could not write to %s: %s", idx_path, e)


def cmd_index(idx_path: str, config: Dict, skip_if: str = "any",
              write_to_disk: bool = False, fallback: str = "none") -> int:
    """Generate title from asm index JSON."""
    doc = _read_index(idx_path)
    if doc is None:
        return 1

    title = doc.get("custom_title", "")

    if not title.endswith("[prompt]") and _should_skip(title, "[LLM]", skip_if):
        print(title)
        return 2

    l_messages = [
        m.get("text", "").strip()
        for m in doc.get("conversation", [])
        if m.get("role") == "user" and m.get("text", "").strip()
    ]

    if not l_messages:
        log.error("no user messages in index")
        return 1

    max_chars = config.get("max_prompt_chars", DEFAULTS["max_prompt_chars"])
    max_title_length = config.get("max_title_length", DEFAULTS["max_title_length"])
    prompts_text = build_prompts_text(l_messages, max_chars)
    new_title, err, raw = generate_title(prompts_text, config)

    if not new_title:
        fb = _apply_fallback(fallback, l_messages, max_title_length)
        if fb:
            new_title = fb
            raw = None
        else:
            log.error("%s", err)
            return 1

    if write_to_disk:
        doc["custom_title"] = new_title + " [LLM]"
        doc["llm_generated_title_raw"] = raw
        doc["llm_title_error"] = None
        _write_index(idx_path, doc)

    print(new_title)
    return 0


# -- Main ------------------------------------------------------------------

_ARGS_WITH_VALUES = {"--jsonl", "--index", "--model", "--jsonl-tag", "--skip-if", "--fallback",
                     "--source", "--overwrite"}
_ARGS_FLAGS = {"--append-to-jsonl", "--dry-run"}
_SKIP_IF_VALUES = {"any", "manual", "none"}
_FALLBACK_VALUES = {"none", "first-prompt", "placeholder"}
_OVERWRITE_TO_SKIP_IF = {"nothing": "any", "llm_only": "manual", "all": "none"}


def _parse_args(args: List[str]) -> Dict:
    """Parse CLI arguments into a dict."""
    parsed: Dict = {"skip_if": "any", "append_to_jsonl": False,
                     "model_override": None, "jsonl_tag": "hook",
                     "fallback": "none", "mode": None, "path": None}
    parsed["append_to_jsonl"] = "--append-to-jsonl" in args

    i = 0
    while i < len(args):
        a = args[i]
        if a in _ARGS_WITH_VALUES and i + 1 < len(args):
            val = args[i + 1]
            if a == "--jsonl":
                parsed["mode"], parsed["path"] = "jsonl", val
            elif a == "--index":
                parsed["mode"], parsed["path"] = "index", val
            elif a == "--model":
                parsed["model_override"] = val
            elif a == "--jsonl-tag" or a == "--source":
                parsed["jsonl_tag"] = val
            elif a == "--skip-if":
                if val not in _SKIP_IF_VALUES:
                    log.error("--skip-if must be any, manual, or none (got: %s)", val)
                    sys.exit(1)
                parsed["skip_if"] = val
            elif a == "--overwrite":
                mapped = _OVERWRITE_TO_SKIP_IF.get(val)
                if not mapped:
                    log.error("--overwrite must be nothing, llm_only, or all (got: %s)", val)
                    sys.exit(1)
                parsed["skip_if"] = mapped
            elif a == "--fallback":
                if val not in _FALLBACK_VALUES:
                    log.error("--fallback must be none, first-prompt, or placeholder (got: %s)", val)
                    sys.exit(1)
                parsed["fallback"] = val
            i += 2
        elif a in _ARGS_FLAGS:
            i += 1
        else:
            log.error("Unknown argument: %s", a)
            sys.exit(1)
    return parsed


def main():
    args = sys.argv[1:]

    if "-h" in args or "--help" in args:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    parsed = _parse_args(args)

    if not parsed["mode"] or not parsed["path"]:
        sid = os.environ.get("CLAUDE_CODE_CURRENT_SESSION_ID")
        if not sid:
            log.error("not inside a Claude Code session. Provide --jsonl or --index.")
            sys.exit(1)
        cwd = os.getcwd()
        project_key = cwd.replace("/", "-").replace("_", "-").replace(" ", "-")
        jsonl_path = os.path.join(HOME, ".claude", "projects", project_key, f"{sid}.jsonl")
        if not os.path.isfile(jsonl_path):
            log.error("derived JSONL path does not exist: %s", jsonl_path)
            sys.exit(1)
        parsed["mode"] = "jsonl"
        parsed["path"] = jsonl_path

    config = load_config()
    if parsed["model_override"]:
        config["model"] = parsed["model_override"]

    if parsed["mode"] == "jsonl":
        sys.exit(cmd_jsonl(parsed["path"], config, parsed["skip_if"],
                           parsed["append_to_jsonl"], parsed["jsonl_tag"],
                           parsed["fallback"]))
    elif parsed["mode"] == "index":
        sys.exit(cmd_index(parsed["path"], config, parsed["skip_if"],
                           parsed["append_to_jsonl"], parsed["fallback"]))


if __name__ == "__main__":
    main()
