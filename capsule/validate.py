"""Validation of a portable skill pack.

Mirrors the frontmatter contract enforced at upload time, so a pack Capsule
produces cannot pass here and fail there. Rules encoded:
  - exactly one SKILL.md, at <pack>/SKILL.md
  - YAML frontmatter with name + description
  - only allowed frontmatter keys
  - kebab-case name, <= 64 chars
  - description <= 1024 chars, no angle brackets
  - every relative link resolves, and every reference file is reachable
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .schema import LIFECYCLES

# Fields defined by the Agent Skills specification.
SPEC_KEYS = {
    "name", "description", "license", "allowed-tools", "metadata", "compatibility",
}

# Host extensions. Unknown keys are ignored by hosts that do not implement them,
# so rejecting these would fail packs that are valid everywhere they run --
# including first-party ones: `version` appears in 13 of the 28 skills shipped
# in the official plugin marketplace. A validator that fails correct work gets
# deleted, and then it validates nothing.
HOST_KEYS = {
    "version", "lifecycle", "when_to_use", "argument-hint", "arguments",
    "user-invocable", "disable-model-invocation", "disallowed-tools", "tools",
    "model", "effort", "context", "agent", "background", "hooks", "paths", "shell",
}

ALLOWED_KEYS = SPEC_KEYS | HOST_KEYS
EXCLUDED_DIR_PARTS = {"__pycache__", "node_modules"}
ROOT_EXCLUDED_DIR_PARTS = {"evals"}

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_KEBAB = re.compile(r"^[a-z0-9-]+$")


def _packaged(rel_path: Path) -> bool:
    dir_parts = rel_path.parts[:-1]
    if any(p in EXCLUDED_DIR_PARTS for p in dir_parts):
        return False
    if dir_parts and dir_parts[0] in ROOT_EXCLUDED_DIR_PARTS:
        return False
    return True


# Markdown links, plus backticked relative paths -- skills reference scripts as
# `scripts/extract.py` at least as often as they link them.
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
_CODE_PATH = re.compile(r"`([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,5})`")

# Fenced blocks are illustrations. A doc teaching skill authoring shows
# `[FORMS.md](references/FORMS.md)` as an example; resolving that against the
# doc's own directory reports a broken link that was never a link.
_FENCED = re.compile(r"^(`{3,}|~{3,}).*?^\1", re.DOTALL | re.MULTILINE)


def _outside_fences(text: str) -> str:
    return _FENCED.sub("\n", text.replace("\r\n", "\n"))

# A leading underscore marks a file included on purpose but not linked --
# partials, fragments, notes. Treating those as orphans trains authors to
# ignore the check.
_INTERNAL_PREFIX = "_"


def _link_targets(pack: Path) -> set[Path]:
    """Every in-pack path referenced from any packaged markdown file."""
    targets: set[Path] = set()
    for md in sorted(pack.rglob("*.md")):
        if not md.is_file() or not _packaged(md.relative_to(pack)):
            continue
        try:
            text = _outside_fences(md.read_text(errors="replace"))
        except OSError:
            continue
        for raw in _MD_LINK.findall(text) + _CODE_PATH.findall(text):
            target = raw.split("#")[0].strip()
            if not target or target.startswith(("/", "#")) or "://" in target:
                continue
            targets.add((md.parent / target).resolve())
    return targets


def validate_references(pack_dir: str | Path) -> list[str]:
    """Check that bundled reference material is actually reachable.

    Two failures, both silent at runtime. A **broken reference** sends the agent
    after a file that is not there, and it improvises instead of erroring. An
    **orphaned reference** is bundled material nothing points at, so it never
    loads -- the skill carries the weight without getting the benefit.
    """
    pack = Path(pack_dir)
    problems: list[str] = []
    targets = _link_targets(pack)

    for md in sorted(pack.rglob("*.md")):
        if not md.is_file() or not _packaged(md.relative_to(pack)):
            continue
        try:
            text = _outside_fences(md.read_text(errors="replace"))
        except OSError:
            continue
        for raw in _MD_LINK.findall(text):
            target = raw.split("#")[0].strip()
            if not target or target.startswith(("/", "#")) or "://" in target:
                continue
            if not (md.parent / target).exists():
                problems.append(
                    f"broken reference: {target!r} linked from "
                    f"{md.relative_to(pack)} does not exist"
                )

    refs_dir = pack / "references"
    if refs_dir.is_dir():
        for ref in sorted(refs_dir.rglob("*")):
            if not ref.is_file() or not _packaged(ref.relative_to(pack)):
                continue
            if ref.name.startswith(_INTERNAL_PREFIX):
                continue
            if ref.resolve() not in targets:
                problems.append(
                    f"orphaned reference: {ref.relative_to(pack)} is not linked "
                    "from any packaged markdown file, so it will never load"
                )

    return problems


def validate_pack(pack_dir: str | Path) -> tuple[bool, list[str]]:
    """Return (ok, problems). An empty problem list means the pack is valid."""
    pack = Path(pack_dir)
    problems: list[str] = []

    skill_md = pack / "SKILL.md"
    if not skill_md.exists():
        return False, [f"{pack}: SKILL.md not found"]

    found = [p for p in pack.rglob("SKILL.md") if _packaged(p.relative_to(pack))]
    if len(found) > 1:
        extras = sorted(str(p.relative_to(pack)) for p in found if p != skill_md)
        problems.append(f"expected exactly one packaged SKILL.md, found {len(found)}: {extras}")

    text = skill_md.read_text(errors="replace").replace("\r\n", "\n")
    match = _FRONTMATTER.match(text)
    if not match:
        return False, problems + ["SKILL.md has no parsable YAML frontmatter"]

    try:
        front = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return False, problems + [f"invalid YAML frontmatter: {exc}"]
    if not isinstance(front, dict):
        return False, problems + ["frontmatter must be a YAML mapping"]

    unexpected = set(front) - ALLOWED_KEYS
    if unexpected:
        problems.append(f"unexpected frontmatter key(s): {sorted(unexpected)}")

    name = front.get("name")
    if not name:
        problems.append("missing 'name' in frontmatter")
    elif not isinstance(name, str):
        problems.append("'name' must be a string")
    else:
        name = name.strip()
        if not _KEBAB.match(name):
            problems.append(f"name {name!r} must be kebab-case")
        if name.startswith("-") or name.endswith("-") or "--" in name:
            problems.append(f"name {name!r} has leading/trailing/double hyphens")
        if len(name) > 64:
            problems.append(f"name is {len(name)} chars; max 64")

    # Optional, matching hosts that fall back to the first body paragraph. A
    # pack without one still loads; it just triggers far less reliably, which
    # is an authoring problem rather than a validity one.
    description = front.get("description")
    if description is not None:
        if not isinstance(description, str):
            problems.append("'description' must be a string")
        else:
            if "<" in description or ">" in description:
                problems.append("description cannot contain angle brackets")
            if len(description) > 1024:
                problems.append(f"description is {len(description)} chars; max 1024")

    lifecycle = front.get("lifecycle")
    if lifecycle is not None and lifecycle not in LIFECYCLES:
        problems.append(
            f"lifecycle {lifecycle!r} must be one of {sorted(LIFECYCLES)}"
        )

    problems.extend(validate_references(pack))

    return (not problems), problems
