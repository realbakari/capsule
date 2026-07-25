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

        A `live` tier names the mitigations rather than only the risk. The
        published guidance is explicit that the defenses which work are
        structural -- delivering third-party content inside `tool_result`
        blocks, JSON-encoding it so an attacker cannot close a quote and break
        out into an instruction context, and scoping permissions so a
        successful injection can do little. Signature matching is
        defence-in-depth behind those, never a substitute: the attacks that
        matter are natural-language and carry no signature at all.
        """
        detail = f" ({self.evidence})" if self.evidence else ""
        line = f"untrusted-input tier: {self.tier}{detail}"
        if self.tier == FETCH_LIVE:
            line += (
                "; deliver fetched content in tool_result blocks, JSON-encode "
                "it, and scope this skill's tools narrowly"
            )
        return line


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
    """Claude Code permission rules.

    `target` is recorded because this shape is host-specific and does not
    travel. Claude Code evaluates deny -> ask -> allow over command patterns;
    Managed Agents has no deny primitive at all. Emitting this format without
    saying which host it is for produces a file that silently does nothing
    somewhere else.
    """
    commands, _ = split_obligations(contract)
    deny_patterns = [f"Bash({ob.token} *)" for ob in commands]
    return {
        "permissions": {
            "deny": deny_patterns
        },
        "_capsule": {
            "target": "claude-code",
            "coverage_note": f"enforces {len(commands)} command-shaped obligations"
        }
    }


# Managed Agents offers `always_allow` and `always_ask`. There is no deny: a
# tool is either governed by a policy or removed from the agent entirely.
POLICY_ALLOW = "always_allow"
POLICY_ASK = "always_ask"

# Command-shaped prohibitions all land on one tool, because that is the only
# granularity the policy model has.
_AGENT_SHELL_TOOL = "bash"


def managed_agent_policy(contract: Contract) -> dict:
    """Translate a contract into a Managed Agents tool configuration.

    The translation is lossy in one direction that matters, and the emitted
    file says so rather than leaving it to be discovered.

    Claude Code can deny a *command pattern*: `Bash(npm install *)` blocks that
    invocation and nothing else. Managed Agents policies attach to a **tool**,
    and the strongest available policy is `always_ask`. So a contract that
    prohibits `npm install` becomes "pause before any bash command" -- broader
    than intended and weaker than a denial, because a human can approve it.

    That is still worth emitting: an approval prompt in front of every shell
    command is a real control, and it is the strongest one this host has. But
    it is not the deny rule, and calling it one would misrepresent the gate.
    """
    commands, contents = split_obligations(contract)

    configs = []
    if commands:
        configs.append({
            "name": _AGENT_SHELL_TOOL,
            "permission_policy": {"type": POLICY_ASK},
        })

    policy = {
        "tools": [
            {
                "type": "agent_toolset_20260401",
                "default_config": {"permission_policy": {"type": POLICY_ALLOW}},
                **({"configs": configs} if configs else {}),
            }
        ],
        "_capsule": {
            "target": "managed-agents",
            "coverage_note": (
                f"{len(commands)} command-shaped obligation(s) mapped to "
                f"'{POLICY_ASK}' on '{_AGENT_SHELL_TOOL}'"
            ),
            "fidelity_loss": (
                "Managed Agents has no deny primitive and policies attach to a "
                "tool rather than a command pattern. A prohibition on a single "
                "command becomes an approval prompt before every command that "
                "tool can run, and a human can approve it. To remove a "
                "capability outright, disable the tool instead of setting a "
                "policy on it."
            ),
            "unenforced": [
                ob.token for ob in contents
            ],
            "unenforced_note": (
                "Content-shaped prohibitions have no equivalent here. Claude "
                "Code blocks them with a PreToolUse hook; this host exposes no "
                "such interception point, so they remain verify-only."
            ),
        },
    }
    return policy


def merge_settings(existing: dict, generated: dict) -> dict:
    """Fold generated deny rules into a settings file the user already owns.

    `.claude/settings.json` is *their* file. Overwriting it destroyed
    everything Capsule does not generate -- allow rules, env, and, worst,
    their own deny rules. A tool that removes a `Bash(rm -rf *)` denial while
    installing a security control is not a security control.

    So: union the deny lists, keep every other key untouched, and never write
    an `allow`. Denials are additive and order-independent, which is what makes
    this merge safe to run repeatedly.
    """
    merged = json.loads(json.dumps(existing)) if existing else {}

    permissions = merged.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        # Malformed input: keep it, nest ours alongside rather than destroying.
        merged["permissions"] = permissions = {}

    theirs = permissions.get("deny")
    theirs = list(theirs) if isinstance(theirs, list) else []
    ours = generated.get("permissions", {}).get("deny", [])

    combined = list(theirs)
    for rule in ours:
        if rule not in combined:
            combined.append(rule)
    if combined:
        permissions["deny"] = combined

    # Provenance, so a reader can tell which rules arrived from a contract and
    # re-running is visibly idempotent rather than mysteriously additive.
    merged["_capsule"] = generated.get("_capsule", {})
    merged["_capsule"]["managed_deny"] = list(ours)
    return merged


def hook_config(contract: Contract, route_prompts: bool = False) -> dict:
    hooks: dict = {
        "PreToolUse": [
            {
                "matcher": "Write|Edit",
                "hooks": ["capsule/capsule-hook.py"]
            }
        ]
    }
    if route_prompts:
        # Fires before the model starts, which is the only point where Capsule
        # can influence which pack gets read.
        hooks["UserPromptSubmit"] = [{"hooks": ["capsule/capsule-router.py"]}]
    return {"hooks": hooks}


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


def prompt_router_hook(index_path: str, min_score: float = 1.5) -> str:
    """A `UserPromptSubmit` hook that routes the prompt and injects the brief.

    This is the piece that makes selection automatic rather than something a
    human pastes. `PreToolUse` fires only once the agent has already decided to
    write, which is too late to influence *which* skill it read; `verify` runs
    after the diff exists. `UserPromptSubmit` is the only point at which
    Capsule can put the right pack in front of the model before it starts.

    Three deliberate constraints:

    - **Fails open.** Any error, any timeout, any low-confidence route injects
      nothing and exits 0. A hook that blocks the conversation when routing is
      uncertain gets deleted, and then it routes nothing at all.
    - **Silent below threshold.** Injecting a marginal pack is worse than
      injecting none: it spends context and argues for the wrong approach.
    - **Advisory wording.** The block says a pack looks relevant and names the
      evidence. It does not claim the model must obey, because the contract --
      not the prompt -- is what actually enforces anything.
    """
    return f'''#!/usr/bin/env python3
"""Route each prompt to a skill and inject an activation brief. Fails open."""
import json
import subprocess
import sys

INDEX = {index_path!r}
MIN_SCORE = {min_score!r}
TIMEOUT_SECONDS = 10


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except Exception:
        return 0

    prompt = (payload.get("prompt") or payload.get("user_prompt") or "").strip()
    # Very short prompts ("yes", "go on") carry no routing signal, and routing
    # them produces confident nonsense.
    if len(prompt) < 12:
        return 0

    try:
        result = subprocess.run(
            [sys.executable, "-m", "capsule.cli", "brief",
             "--index", INDEX, "--task", prompt],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False,
        )
    except Exception:
        return 0

    # Exit 2 is "no candidate cleared the threshold" -- the correct outcome for
    # a prompt no skill covers, and not something to report as an error.
    if result.returncode != 0 or not result.stdout.strip():
        return 0

    print("<capsule-activation>")
    print(result.stdout.strip())
    print(
        "This pack was selected deterministically from the workspace index. "
        "Read the SKILL.md above before proceeding. The listed obligations are "
        "checked against the resulting diff."
    )
    print("</capsule-activation>")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never break the conversation
        print(f"capsule router hook skipped: {{exc}}", file=sys.stderr)
        sys.exit(0)
'''


def plugin_manifest(contract: Contract) -> dict:
    return {
        "name": contract.skill_name,
        "hooks": "./hooks/hooks.json"
    }


@dataclass
class ArtifactEntry:
    path: str
    content: str


def emit_all(contract: Contract, dest_dir: str | Path,
             index_path: str | None = None,
             target: str = "claude-code") -> list[ArtifactEntry]:
    """Harness artifacts for a contract.

    `index_path` opts into the prompt router. It is separate because routing
    needs a workspace index, which is a different concern from a contract, and
    because a hook that runs on every prompt should be asked for explicitly.
    """
    if target == "managed-agents":
        # A single JSON document: this host configures policy on the agent
        # itself rather than through files a harness reads off disk.
        return [ArtifactEntry(
            "managed-agent-policy.json",
            json.dumps(managed_agent_policy(contract), indent=2),
        )]

    route_prompts = bool(index_path)

    # Everything Capsule owns sits under one directory, so a reader can see at
    # a glance what is generated and delete it in one action. `settings.json`
    # is the single exception: it is the user's file, and it is merged rather
    # than written. See `merge_settings`.
    entries = [
        ArtifactEntry("settings.json", json.dumps(permission_rules(contract), indent=2)),
        ArtifactEntry("capsule/hooks.json",
                      json.dumps(hook_config(contract, route_prompts), indent=2)),
        ArtifactEntry("capsule/capsule-hook.py", hook_script(contract)),
        ArtifactEntry("capsule/README.md", _footprint_readme(contract, route_prompts)),
    ]
    if route_prompts:
        entries.append(
            ArtifactEntry("capsule/capsule-router.py", prompt_router_hook(index_path))
        )
    return entries


# Files Capsule owns outright and may overwrite. Anything not listed is the
# user's and must be merged, never replaced.
GENERATED_PREFIX = "capsule/"


def _footprint_readme(contract: Contract, route_prompts: bool) -> str:
    """A note left beside the generated files explaining what they are.

    Generated configuration that cannot be explained by the person who finds it
    gets deleted or, worse, cargo-culted. Four files with no README is clutter;
    four files with one is a component.
    """
    commands, contents = split_obligations(contract)
    router = (
        "\n- `capsule-router.py` - runs on every prompt, routes it to a skill and\n"
        "  injects an activation brief. Fails open and stays silent when unsure.\n"
        if route_prompts else ""
    )
    return f"""# Generated by Capsule

Everything in this directory is generated from the `{contract.skill_name}` skill
contract. It is safe to delete: regenerate with

    capsule harness --skill {contract.skill_name} --dest ./.claude

## What is here

- `hooks.json` - registers the hooks below with the harness.
- `capsule-hook.py` - blocks writes containing {len(contents)} prohibited token(s)
  the skill names. Fails open with a printed reason if it cannot parse a payload.{router}
- `README.md` - this file.

## What is not here

`../settings.json` is **your** file. Capsule merges {len(commands)} deny rule(s)
into it and leaves everything else alone; the rules it manages are listed under
`_capsule.managed_deny`. It never writes an `allow`.

## Committing

Commit this directory: the rules are part of how the project is governed, and
they should be reviewed like any other config. `capsule-index.json` is derived
and should be gitignored.
"""


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
