"""Harness integration: pushing enforcement earlier than the diff."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .contract import FORBID, PLACEMENT, Contract, Obligation

_COMMAND_HEADS = (
    "npm", "npx", "pnpm", "yarn", "pip", "pip3", "python", "python3", "node",
    "cat", "head", "tail", "rm", "mv", "cp", "git", "curl", "wget", "brew",
    "apt", "apt-get", "sudo", "chmod", "chown", "docker", "kubectl", "make",
)

FETCH_DISABLED = "disabled"
FETCH_INDEXED = "indexed"
FETCH_LIVE = "live"


@dataclass
class Provenance:
    tier: str
    rank: int
    evidence: str = ""


def is_command_token(token: str) -> bool:
    first_word = token.strip().split()[0] if token.strip() else ""
    return first_word in _COMMAND_HEADS


def split_obligations(contract: Contract) -> tuple[list[Obligation], list[Obligation]]:
    commands = []
    contents = []
    for ob in contract.checkable:
        if ob.kind == FORBID:
            if is_command_token(ob.token):
                commands.append(ob)
            else:
                contents.append(ob)
    return commands, contents


def permission_rules(contract: Contract) -> dict:
    commands, _ = split_obligations(contract)
    deny_patterns = [f"Bash({ob.token} *)" for ob in commands]
    return {
        "permissions": {
            "deny": deny_patterns
        },
        "_capsule": {
            "coverage_note": f"enforces {len(commands)} command-shaped obligations"
        }
    }


def hook_config(contract: Contract) -> dict:
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": ["capsule-hook.py"]
                }
            ]
        }
    }


def hook_script(contract: Contract) -> str:
    _, contents = split_obligations(contract)
    tokens = [ob.token for ob in contents]
    tokens_json = json.dumps(tokens)

    return f"""#!/usr/bin/env python3
import sys, json

BANNED = {tokens_json}

def main():
    raw = sys.stdin.read()
    if not raw.strip():
        print("unparsable payload", file=sys.stderr)
        sys.exit(0)
    try:
        data = json.loads(raw)
    except Exception:
        print("unparsable json", file=sys.stderr)
        sys.exit(0)

    tool_input = data.get("tool_input", {{}})
    content = tool_input.get("content") or tool_input.get("new_string") or tool_input.get("file_content")

    if not content:
        print("no content field recognised", file=sys.stderr)
        sys.exit(0)

    for tok in BANNED:
        if tok in content:
            print(f"Contract violation: token {{tok}} is prohibited (this is not a permission problem)", file=sys.stderr)
            sys.exit(1)

    sys.exit(0)

if __name__ == "__main__":
    main()
"""


def plugin_manifest(contract: Contract) -> dict:
    return {
        "name": contract.skill_name,
        "hooks": "./hooks/hooks.json"
    }


@dataclass
class ArtifactEntry:
    path: str
    content: str


def emit_all(contract: Contract, dest_dir: str | Path) -> list[ArtifactEntry]:
    perm = permission_rules(contract)
    h_cfg = hook_config(contract)
    script = hook_script(contract)
    manifest = plugin_manifest(contract)

    return [
        ArtifactEntry("settings.json", json.dumps(perm, indent=2)),
        ArtifactEntry("hooks/hooks.json", json.dumps(h_cfg, indent=2)),
        ArtifactEntry("capsule-hook.py", script),
        ArtifactEntry(".claude-plugin/plugin.json", json.dumps(manifest, indent=2)),
    ]


def classify_input_provenance(name: str, body: str) -> Provenance:
    lower = body.lower()
    if "web_fetch" in lower or "requests.get" in lower or "curl" in lower:
        if "cached index" in lower or "offline fallback" in lower:
            return Provenance(tier=FETCH_INDEXED, rank=1, evidence="uses cached fetch with fallback")
        return Provenance(tier=FETCH_LIVE, rank=2, evidence="live fetching active")
    return Provenance(tier=FETCH_DISABLED, rank=0, evidence="no network fetch")
