#!/usr/bin/env python3
"""Install Breadcrumbs hooks into Claude Code settings."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOOKS_DIR = CLAUDE_DIR / "hooks"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"
SCRIPT_NAME = "session_recorder.py"
SOURCE = Path(__file__).parent / SCRIPT_NAME
DEST = HOOKS_DIR / SCRIPT_NAME

HOOK_COMMAND = f"python3 {DEST}"

HOOKS_CONFIG = {
    "UserPromptSubmit": [
        {
            "matcher": "",
            "hooks": [{"type": "command", "command": f"{HOOK_COMMAND} prompt"}],
        }
    ],
    "Stop": [
        {
            "matcher": "",
            "hooks": [{"type": "command", "command": f"{HOOK_COMMAND} sync"}],
        }
    ],
}


def main():
    # Copy script
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, DEST)
    DEST.chmod(0o755)
    print(f"Copied {SCRIPT_NAME} -> {DEST}")

    # Read existing settings
    if SETTINGS_PATH.exists():
        settings = json.loads(SETTINGS_PATH.read_text())
    else:
        settings = {}

    # Merge hooks (preserve existing ones)
    existing_hooks = settings.get("hooks", {})
    for event, hook_entries in HOOKS_CONFIG.items():
        if event not in existing_hooks:
            existing_hooks[event] = []
        # Check if breadcrumbs hook already installed
        already = any(
            SCRIPT_NAME in h.get("command", "")
            for entry in existing_hooks[event]
            for h in entry.get("hooks", [])
        )
        if not already:
            existing_hooks[event].extend(hook_entries)
            print(f"Added {event} hook")
        else:
            print(f"{event} hook already installed, skipping")

    settings["hooks"] = existing_hooks

    # Write settings
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"Updated {SETTINGS_PATH}")
    # Bulk import all existing sessions
    projects_dir = CLAUDE_DIR / "projects"
    if projects_dir.exists():
        print("\nImporting existing sessions...")
        total = 0
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            slug = project_dir.name
            # Derive cwd from slug: -home-david-foo -> /home/david/foo
            cwd = slug.replace("-", "/", 1) if slug.startswith("-") else slug
            # Fix: only first char was a -, rest need selective replacement
            # Slug format is cwd.replace("/", "-"), so reverse it
            cwd = slug.replace("-", "/")
            if not cwd.startswith("/"):
                cwd = "/" + cwd
            count = 0
            for jsonl in project_dir.glob("*.jsonl"):
                session_id = jsonl.stem
                input_json = json.dumps({"session_id": session_id, "cwd": cwd})
                subprocess.run(
                    [sys.executable, str(DEST), "sync"],
                    input=input_json, capture_output=True, text=True,
                )
                count += 1
            if count:
                print(f"  {slug}: {count} sessions")
                total += count
        print(f"Imported {total} sessions total.")
    else:
        print("\nNo existing sessions found to import.")

    print("\nBreadcrumbs installed. Restart Claude Code for hooks to take effect.")
    print(f"Database: {CLAUDE_DIR / 'breadcrumbs.db'}")


if __name__ == "__main__":
    main()
