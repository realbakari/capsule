"""Capsule pack validation."""

from __future__ import annotations

import re
from pathlib import Path


def validate_references(pack_path: str | Path) -> list[str]:
    """Validate the references/ directory for a skill pack.

    Checks:
      - All files in references/ are .md (warns on others)
      - Internal links from SKILL.md to references/*.md resolve to real files
      - Orphaned references (files not linked from SKILL.md) are reported
    """
    path = Path(pack_path)
    problems = []
    refs_dir = path / "references"
    skill_md = path / "SKILL.md"

    if not refs_dir.is_dir():
        return []

    # Check for non-markdown files in references/.
    ref_files: set[str] = set()
    for f in refs_dir.rglob("*"):
        if f.is_file():
            ref_files.add(str(f.relative_to(path)))
            if f.suffix not in (".md", ".mjs", ".js", ".sh", ".py"):
                problems.append(f"unexpected file type in references/: {f.name}")

    # Parse SKILL.md for links to references/.
    if not skill_md.exists():
        return problems

    content = skill_md.read_text(errors="replace")
    # Match markdown links: [text](references/something.md)
    link_pattern = re.compile(r'\[.*?\]\((references/[^)]+)\)')
    linked_refs: set[str] = set()
    for match in link_pattern.finditer(content):
        ref_target = match.group(1)
        linked_refs.add(ref_target)
        target_path = path / ref_target
        if not target_path.exists():
            problems.append(f"broken reference link: {ref_target}")

    # Check for orphaned references (in refs but not linked from SKILL.md).
    for ref_file in sorted(ref_files):
        if ref_file.startswith("references/") and ref_file not in linked_refs:
            # Underscored files are conventionally internal, not linked.
            base = Path(ref_file).name
            if not base.startswith("_"):
                problems.append(f"orphaned reference: {ref_file}")

    return problems


def validate_pack(pack_path: str | Path) -> tuple[bool, list[str]]:
    path = Path(pack_path)
    problems = []
    if not path.exists() or not path.is_dir():
        return False, ["path does not exist or is not a directory"]
    skill_md = path / "SKILL.md"
    if not skill_md.exists():
        return False, ["SKILL.md not found"]

    content = skill_md.read_text()
    if not content.startswith("---"):
        return False, ["SKILL.md must start with --- frontmatter"]

    dir_name = path.name
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", dir_name):
        problems.append("dir name must be kebab-case format")

    fm_end = content.find("---", 3)
    if fm_end != -1:
        fm = content[3:fm_end]
        if "<" in fm or ">" in fm:
            problems.append("angle brackets in description")

        allowed_keys = {
            "name", "description", "license", "compatibility",
            "metadata", "allowed-tools", "lifecycle",
        }
        for line in fm.splitlines():
            line = line.strip()
            if line and ":" in line and not line.startswith("#"):
                key = line.split(":", 1)[0].strip()
                val = line.split(":", 1)[1].strip()
                if key and key not in allowed_keys:
                    problems.append(f"unexpected key '{key}' in frontmatter")
                if key == "name" and not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", val):
                    problems.append("name field must be kebab-case format")

    nested = []
    for child in path.rglob("SKILL.md"):
        if child != skill_md and "evals" not in child.parts:
            nested.append(str(child))
    if nested:
        problems.append("exactly one SKILL.md allowed at root")

    # Validate references/ directory if present.
    ref_problems = validate_references(path)
    problems.extend(ref_problems)

    return (len(problems) == 0, problems)

