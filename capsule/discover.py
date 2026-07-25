"""Discovery: walk the mounted workspace and condense what is found.

Order is fixed by the Capsule contract:
  1. root instruction files
  2. nested scoped instruction files
  3. docs and architecture notes
  4. skill folders and SKILL.md files
  5. trusted external skill references
  6. tooling shortcuts and workflow notes

Discovery never writes and never fails hard on an unreadable file; it records
lower confidence instead.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .policy import Policy
from .schema import LIFECYCLES, TOOLS_INHERIT_ALL, RunContext, SourceRecord

ROOT_INSTRUCTION_NAMES = (
    "CLAUDE.md",
    "AGENTS.md",
    "AGENT.md",
    "CODEX.md",
    "codex.md",
    ".cursorrules",
    "CONTRIBUTING.md",
)

MANIFEST_NAMES = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pnpm-workspace.yaml",
    "requirements.txt",
)

DOC_DIR_NAMES = ("docs", "doc", "documentation", "adr", "rfcs")

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)

# Category inference. First match wins, so order matters.
_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("document-io", ("docx", "pdf", "pptx", "xlsx", "spreadsheet", "word document", "slide")),
    ("file-routing", ("uploaded", "read from disk", "router", "extract content")),
    ("skill-infrastructure", ("skill", "mcp server", "eval", "benchmark")),
    ("visual-design", ("design", "art", "theme", "poster", "gif", "watercolor", "brand", "artifact")),
    ("writing", ("writing", "documentation", "proposal", "comms", "voice profile", "draft")),
    ("commerce", ("order", "grocery", "delivery", "refund", "return", "subscription", "cart")),
    ("admin-tasks", ("expense", "reimburs", "form", "prescription", "appointment", "booking")),
    ("analysis", ("calculation", "financial", "scenario", "projection")),
    ("product-knowledge", ("anthropic's products", "claude code", "pricing")),
]

# Shortcut patterns: slash commands and CLI invocations mentioned in a body.
_SLASH = re.compile(r"(?<![\w/])/([a-z][a-z0-9-]{2,30})\b")
_SCRIPT = re.compile(r"\b(?:python3?|node|bash)\s+([\w./-]+\.(?:py|js|sh))")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def parse_frontmatter(text: str) -> dict:
    match = _FRONTMATTER.match(text.replace("\r\n", "\n"))
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


# Directory names that group skills without describing them. A skill under
# `skills/public/docx` is not in the "public" category, so these fall through to
# keyword inference instead.
_GENERIC_DIRS = {
    "skills", "skill", "public", "examples", "example", "packs", "pack",
    "src", "lib", ".claude", "plugins",
}

# Directory names that declare maturity rather than subject.
_LIFECYCLE_DIRS = {
    "deprecated": "deprecated",
    "archive": "deprecated",
    "archived": "deprecated",
    "in-progress": "in-progress",
    "wip": "in-progress",
    "draft": "in-progress",
    "experimental": "in-progress",
}


def _infer_category(name: str, description: str) -> str:
    haystack = f"{name} {description}".lower()
    for category, needles in _CATEGORY_RULES:
        if any(n in haystack for n in needles):
            return category
    return "general"


def _category_from_nesting(skill_dir: Path) -> str | None:
    """Use the parent directory as the category when it names a subject.

    A repo that files skills as `skills/engineering/tdd/` has already declared
    the taxonomy in its layout; re-deriving it from keywords would ignore the
    author. Generic grouping directories are skipped so `skills/public/docx`
    still falls through to keyword inference.
    """
    parent = skill_dir.parent.name.lower()
    if not parent or parent in _GENERIC_DIRS:
        return None
    return parent


def _infer_lifecycle(skill_dir: Path) -> str:
    """Read maturity off the directory path. Frontmatter overrides this."""
    for part in (p.lower() for p in skill_dir.parts):
        if part in _LIFECYCLE_DIRS:
            return _LIFECYCLE_DIRS[part]
    return "stable"


def _trigger_phrases(description: str, name: str, limit: int = 12) -> list[str]:
    """Pull the phrases a router should match on out of a description.

    Descriptions in this corpus are written as trigger documentation already
    ("Use when...", "Triggers include: ..."), so quoted fragments, extension
    tokens and post-'when' clauses carry most of the signal.
    """
    phrases: list[str] = [name, name.replace("-", " ")]
    lowered = description.lower()

    for quoted in re.findall(r"['\"]([^'\"]{3,40})['\"]", description):
        phrases.append(quoted.strip().lower())
    for ext in re.findall(r"\.\w{2,5}\b", lowered):
        phrases.append(ext)
    for clause in re.findall(r"(?:use (?:this skill )?(?:when|for)|triggers include)[: ]([^.]{5,120})", lowered):
        for part in re.split(r",| or | and ", clause):
            part = part.strip(" :;")
            if 3 <= len(part) <= 48:
                phrases.append(part)

    seen: list[str] = []
    for phrase in phrases:
        phrase = phrase.strip()
        if phrase and phrase not in seen:
            seen.append(phrase)
    return seen[:limit]


def _shortcuts(body: str, limit: int = 8) -> list[str]:
    found: list[str] = []
    for match in _SLASH.findall(body):
        token = f"/{match}"
        if token not in found:
            found.append(token)
    for match in _SCRIPT.findall(body):
        if match not in found:
            found.append(match)
    return found[:limit]


def _purpose(body: str, description: str) -> str:
    """First substantive prose line after the frontmatter, else the description."""
    stripped = _FRONTMATTER.sub("", body.replace("\r\n", "\n"), count=1).strip()
    for line in stripped.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "|", ">", "-", "*", "`")):
            return line[:280]
    return description[:280] or "no purpose statement found"


def discover_skill(skill_dir: Path, policy: Policy, scope: str) -> SourceRecord | None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None
    try:
        body = skill_md.read_text(errors="replace")
    except OSError:
        return None

    front = parse_frontmatter(body)
    name = str(front.get("name") or skill_dir.name).strip()
    description = str(front.get("description") or "").strip()

    license_class = Policy.classify_license(skill_dir)
    aux = sorted(
        p.name
        for p in skill_dir.iterdir()
        if p.name not in ("SKILL.md", "LICENSE.txt") and not p.name.startswith(".")
    )

    # Confidence reflects how much of the record was read rather than guessed.
    confidence = 0.55
    if front:
        confidence += 0.2
    if description:
        confidence += 0.15
    if license_class != "unknown":
        confidence += 0.1
    confidence = round(min(confidence, 1.0), 2)

    constraints = [f"license:{license_class}"]
    if str(skill_dir).startswith("/mnt/skills"):
        constraints.append("source-tree:read-only")
    if license_class != "apache-2.0":
        constraints.append("reconstruction:denied-by-default")

    # Layout is an authored signal; keywords are a guess. Prefer the author.
    category = _category_from_nesting(skill_dir) or _infer_category(name, description)

    # Frontmatter is more specific than layout, so a declared lifecycle wins
    # over the directory a skill happens to sit in.
    lifecycle = str(front.get("lifecycle") or "").strip() or _infer_lifecycle(skill_dir)
    if lifecycle not in LIFECYCLES:
        lifecycle = "stable"

    record = SourceRecord(
        source_type="skill",
        # Absolute, so the index stays meaningful when a later command runs
        # from a different working directory. A relative path that fails to
        # resolve yields an empty contract, and an empty contract passes
        # everything -- a gate that silently stops gating.
        source_path=str(skill_dir.resolve()),
        name=name,
        category=category,
        lifecycle=lifecycle,
        purpose=_purpose(body, description),
        trigger_phrases=_trigger_phrases(description, name),
        shortcuts=_shortcuts(body),
        scope=scope,
        policy_constraints=constraints,
        reload_rules="on-selection: read SKILL.md in full before use",
        confidence=confidence,
        license_class=license_class,
        reconstructable=(license_class == "apache-2.0"),
        body_words=len(body.split()),
        aux_dirs=aux,
        content_hash=_hash(body),
    )
    return record


def _parse_tools(raw) -> list[str]:
    """Normalise a `tools:` value.

    Both spellings occur in the wild and mean the same thing:
        tools: ["Read", "Grep"]
        tools: Glob, Grep, LS, Read
    """
    if raw is None:
        return [TOOLS_INHERIT_ALL]
    if isinstance(raw, str):
        parsed = [t.strip() for t in raw.split(",") if t.strip()]
    elif isinstance(raw, (list, tuple)):
        parsed = [str(t).strip() for t in raw if str(t).strip()]
    else:
        return [TOOLS_INHERIT_ALL]
    return parsed or [TOOLS_INHERIT_ALL]


def discover_agent(path: Path, scope: str) -> SourceRecord | None:
    """Condense one agent definition.

    Agents are a governed surface in their own right: they carry a description
    that decides when they fire, and a `tools:` list that is an explicit
    permission grant. A file under `agents/` with no parsable frontmatter is
    not an agent definition at all, so it is skipped rather than recorded as a
    zero-permission agent -- which would read as the safest entry in the index.
    """
    try:
        body = path.read_text(errors="replace")
    except OSError:
        return None

    front = parse_frontmatter(body)
    if not front or not front.get("name"):
        return None

    name = str(front["name"]).strip()
    description = str(front.get("description") or "").strip()
    tools = _parse_tools(front.get("tools"))

    constraints = ["source-type:agent"]
    constraints.append(
        "tools:inherits-all" if tools == [TOOLS_INHERIT_ALL] else f"tools:scoped({len(tools)})"
    )

    confidence = 0.6 + (0.2 if description else 0.0) + (0.2 if front.get("tools") else 0.0)

    return SourceRecord(
        source_type="agent",
        source_path=str(path.resolve()),
        name=name,
        category="agent-definition",
        # An agent body is a system prompt, not documentation, so its first
        # line is "You are a code reviewer..." rather than a summary. The
        # frontmatter description is the meaningful one, and it is also the
        # text that decides when the agent gets delegated to.
        purpose=description or _purpose(body, description),
        trigger_phrases=_trigger_phrases(description, name),
        shortcuts=[],
        scope=scope,
        policy_constraints=constraints,
        reload_rules="on-selection: read the definition in full before delegating",
        confidence=round(min(confidence, 1.0), 2),
        license_class=Policy.classify_license(path.parent),
        reconstructable=False,
        body_words=len(body.split()),
        content_hash=_hash(body),
        tool_grants=tools,
        model=str(front.get("model") or ""),
    )


def _simple_record(path: Path, source_type: str, category: str, scope: str) -> SourceRecord:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        text = ""
    first = next(
        (l.strip("# ").strip() for l in text.splitlines() if l.strip() and not l.startswith("---")),
        "",
    )
    return SourceRecord(
        source_type=source_type,
        source_path=str(path),
        name=path.name,
        category=category,
        purpose=first[:280] or f"{category} file",
        trigger_phrases=[path.stem.lower()],
        shortcuts=_shortcuts(text),
        scope=scope,
        policy_constraints=["authoritative" if source_type == "instruction" else "informational"],
        reload_rules="on-repo-change" if source_type != "instruction" else "always",
        confidence=0.9 if source_type == "instruction" else 0.7,
        body_words=len(text.split()),
        content_hash=_hash(text),
    )


def discover(roots: list[str], policy: Policy, max_depth: int = 6) -> RunContext:
    """Walk roots in contract order and build the condensed run context."""
    context = RunContext(
        roots=[str(Path(r)) for r in roots],
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    seen_skills: set[str] = set()

    for root_str in roots:
        root = Path(root_str)
        decision = policy.can_read(root)
        if not decision.allowed:
            continue

        scope = "external-trusted" if str(root).startswith("/mnt/skills") else "repo"

        for path in sorted(root.rglob("*")):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                depth = len(path.relative_to(root).parts)
            except ValueError:
                continue
            if depth > max_depth:
                continue

            if path.is_dir():
                continue

            # 4/5. skills (dedup by directory)
            if path.name == "SKILL.md":
                skill_dir = path.parent
                if str(skill_dir) in seen_skills:
                    continue
                seen_skills.add(str(skill_dir))
                record = discover_skill(skill_dir, policy, scope)
                if record:
                    context.records.append(record)
                continue

            # agent definitions: any .md directly under an `agents/` directory
            if path.suffix.lower() == ".md" and path.parent.name == "agents":
                record = discover_agent(path, scope)
                if record:
                    context.records.append(record)
                continue

            # 1/2. instruction files, root or nested
            if path.name in ROOT_INSTRUCTION_NAMES:
                context.records.append(
                    _simple_record(path, "instruction", "agent-instructions", "root" if depth == 1 else "scoped")
                )
                continue

            # manifests
            if path.name in MANIFEST_NAMES:
                context.records.append(_simple_record(path, "manifest", "build-config", scope))
                continue

            # 3. docs
            if path.suffix.lower() == ".md" and any(
                d in {p.lower() for p in path.parts} for d in DOC_DIR_NAMES
            ):
                context.records.append(_simple_record(path, "doc", "documentation", scope))
                continue

    return context
