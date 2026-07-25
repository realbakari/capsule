"""Harness integration: pushing enforcement earlier than the diff."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .contract import FORBID, Contract, Obligation

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

    def line(self) -> str:
        """One-line form for `capsule lint`.

        The tier measures how a skill ingests external content, not whether it
        is trustworthy: a scraper is legitimate and still lands in `live`.
        """
        detail = f" ({self.evidence})" if self.evidence else ""
        return f"untrusted-input tier: {self.tier}{detail}"


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

    # Inbound prompt injection attack signatures (from Opus 5 System Card Section 5.2)
    injection_signatures = [
        "IGNORE PREVIOUS INSTRUCTIONS",
        "IGNORE ALL PRIOR",
        "DISREGARD ALL PREVIOUS",
        "SYSTEM OVERRIDE:",
        "NEW SYSTEM PROMPT:",
        "BYPASS SECURITY:",
    ]
    injection_json = json.dumps(injection_signatures)

    return f"""#!/usr/bin/env python3
import sys, json, re

BANNED = {tokens_json}
INJECTION_SIGNATURES = {injection_json}

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

    tool_result = data.get("tool_result", {{}})
    result_text = str(tool_result.get("content") or tool_result.get("output") or data.get("result") or "")

    if not content and not result_text:
        print("no content field recognised", file=sys.stderr)
        sys.exit(0)

    if content:
        for tok in BANNED:
            if tok and tok in content:
                print(f"Contract violation: token {{tok}} is prohibited (this is not a permission problem)", file=sys.stderr)
                sys.exit(1)

    if result_text:
        for sig in INJECTION_SIGNATURES:
            if sig in result_text.upper():
                print(f"Prompt injection detected in tool result: {{sig}}", file=sys.stderr)
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


# -- Multi-host plugin manifest generation ------------------------------------
# Following the patterns from resend/resend-skills (4 hosts) and
# supabase/agent-skills (Claude plugin + marketplace).


@dataclass
class SkillMeta:
    """Minimal metadata about a skill for plugin manifest generation."""
    name: str
    description: str
    category: str = "general"
    lifecycle: str = "stable"


def claude_plugin_manifest(
    skills: list[SkillMeta], repo_slug: str
) -> dict:
    """Generate .claude-plugin/plugin.json and marketplace.json contents."""
    return {
        "plugin.json": {
            "name": repo_slug.split("/")[-1],
            "version": "1.0.0",
            "description": f"Agent skills from {repo_slug}",
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "path": f"skills/{s.name}",
                }
                for s in skills
                if s.lifecycle != "deprecated"
            ],
        },
        "marketplace.json": {
            "name": repo_slug.split("/")[-1],
            "display_name": repo_slug,
            "repository": f"https://github.com/{repo_slug}",
            "skills": [s.name for s in skills if s.lifecycle != "deprecated"],
        },
    }


def codex_plugin_manifest(
    skills: list[SkillMeta], repo_slug: str
) -> dict:
    """Generate .codex-plugin/plugin.json contents."""
    return {
        "plugin.json": {
            "name": repo_slug.split("/")[-1],
            "version": "1.0.0",
            "description": f"Agent skills from {repo_slug}",
            "agents": [
                {
                    "name": s.name,
                    "instructions": f"skills/{s.name}/SKILL.md",
                }
                for s in skills
                if s.lifecycle != "deprecated"
            ],
        },
    }


def cursor_plugin_manifest(
    skills: list[SkillMeta], repo_slug: str
) -> dict:
    """Generate .cursor-plugin/plugin.json and marketplace.json contents."""
    return {
        "plugin.json": {
            "name": repo_slug.split("/")[-1],
            "version": "1.0.0",
            "description": f"Agent skills from {repo_slug}",
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "path": f"skills/{s.name}",
                }
                for s in skills
                if s.lifecycle != "deprecated"
            ],
        },
        "marketplace.json": {
            "name": repo_slug.split("/")[-1],
            "repository": f"https://github.com/{repo_slug}",
        },
    }


def grok_plugin_manifest(
    skills: list[SkillMeta], repo_slug: str
) -> dict:
    """Generate .grok-plugin/plugin.json contents."""
    return {
        "plugin.json": {
            "name": repo_slug.split("/")[-1],
            "version": "1.0.0",
            "description": f"Agent skills from {repo_slug}",
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "instructions_file": f"skills/{s.name}/SKILL.md",
                }
                for s in skills
                if s.lifecycle != "deprecated"
            ],
        },
    }


def emit_all_plugins(
    skills: list[SkillMeta],
    repo_slug: str,
    output_dir: str | Path,
) -> list[ArtifactEntry]:
    """Write all plugin manifest directories to output_dir.

    Creates:
      .claude-plugin/plugin.json
      .claude-plugin/marketplace.json
      .codex-plugin/plugin.json
      .cursor-plugin/plugin.json
      .cursor-plugin/marketplace.json
      .grok-plugin/plugin.json
    """
    dest = Path(output_dir)
    entries: list[ArtifactEntry] = []

    generators = {
        ".claude-plugin": claude_plugin_manifest,
        ".codex-plugin": codex_plugin_manifest,
        ".cursor-plugin": cursor_plugin_manifest,
        ".grok-plugin": grok_plugin_manifest,
    }

    for dir_name, gen_fn in generators.items():
        manifest = gen_fn(skills, repo_slug)
        for filename, content in manifest.items():
            rel_path = f"{dir_name}/{filename}"
            entries.append(ArtifactEntry(rel_path, json.dumps(content, indent=2)))

    return entries
