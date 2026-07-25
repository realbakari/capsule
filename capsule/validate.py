"""Capsule pack validation."""

from __future__ import annotations

import re
from pathlib import Path


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

        allowed_keys = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
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

    return (len(problems) == 0, problems)
