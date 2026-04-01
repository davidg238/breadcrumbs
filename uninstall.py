#!/usr/bin/env python3
"""Uninstall Breadcrumbs hooks from Claude Code settings."""

import json
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOOKS_DIR = CLAUDE_DIR / "hooks"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"
SCRIPT_NAME = "session_recorder.py"
DEST = HOOKS_DIR / SCRIPT_NAME


def main():
    # Remove script
    if DEST.exists():
        DEST.unlink()
        print(f"Removed {DEST}")
    else:
        print(f"{DEST} not found, skipping")

    # Remove hooks from settings
    if SETTINGS_PATH.exists():
        settings = json.loads(SETTINGS_PATH.read_text())
        hooks = settings.get("hooks", {})
        changed = False

        for event in list(hooks.keys()):
            original = hooks[event]
            filtered = [
                entry for entry in original
                if not any(
                    SCRIPT_NAME in h.get("command", "")
                    for h in entry.get("hooks", [])
                )
            ]
            if len(filtered) != len(original):
                hooks[event] = filtered
                changed = True
                print(f"Removed {event} hook")
            # Clean up empty event lists
            if not hooks[event]:
                del hooks[event]

        if changed:
            settings["hooks"] = hooks
            SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")
            print(f"Updated {SETTINGS_PATH}")
        else:
            print("No breadcrumbs hooks found in settings")
    else:
        print("No settings.json found")

    print("\nBreadcrumbs uninstalled.")
    print(f"Database preserved at: {CLAUDE_DIR / 'breadcrumbs.db'}")
    print("Delete it manually if you want to remove all recorded data.")


if __name__ == "__main__":
    main()
