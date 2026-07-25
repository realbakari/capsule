"""Capsule discovery module."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence
from .schema import RunContext, SourceRecord
from .policy import Policy


# Directories that map to lifecycle stages when used as parent containers.
_LIFECYCLE_DIRS = {
    "deprecated": "deprecated",
    "in-progress": "in-progress",
}


def _trigger_phrases(description: str, name: str) -> set[str]:
    phrases = {name, f".{name}"}
    for word in re.findall(r"\.?[a-zA-Z0-9_-]+", description.lower()):
        if len(word) >= 2:
            phrases.add(word)
    return phrases


def _infer_category_and_lifecycle(
    skill_dir: Path, root_path: Path
) -> tuple[str, str]:
    """Derive category and lifecycle from directory nesting.

    Handles three layouts:
      skills/<skill-name>/SKILL.md          -> category="general", lifecycle="stable"
      skills/<category>/<skill-name>/SKILL.md -> category=<category>, lifecycle="stable"
      skills/deprecated/<skill-name>/SKILL.md -> category="general", lifecycle="deprecated"
      skills/<category>/deprecated/<skill-name>/SKILL.md -> category=<category>, lifecycle="deprecated"
    """
    try:
        rel_parts = skill_dir.relative_to(root_path).parts
    except ValueError:
        return "general", "stable"

    category = "general"
    lifecycle = "stable"

    # Walk parts looking for category and lifecycle markers.
    for part in rel_parts[:-1]:  # Exclude the skill dir itself.
        if part in _LIFECYCLE_DIRS:
            lifecycle = _LIFECYCLE_DIRS[part]
        elif part not in ("skills",):
            category = part

    return category, lifecycle


def _extract_lifecycle_from_frontmatter(content: str) -> str | None:
    """Extract lifecycle from SKILL.md frontmatter if present."""
    if not content.startswith("---"):
        return None
    end = content.find("---", 3)
    if end == -1:
        return None
    fm = content[3:end]
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith("lifecycle:"):
            val = line.split(":", 1)[1].strip()
            if val in ("stable", "in-progress", "deprecated"):
                return val
    return None


def discover(roots: Sequence[str], policy: Policy) -> RunContext:
    records: list[SourceRecord] = []

    for root_str in roots:
        root_path = Path(root_str)
        if not root_path.exists():
            continue

        for p in root_path.rglob("SKILL.md"):
            skill_dir = p.parent
            name = skill_dir.name
            rel = str(skill_dir)

            # Derive category and lifecycle from directory structure.
            category, lifecycle = _infer_category_and_lifecycle(
                skill_dir, root_path
            )

            # Frontmatter lifecycle overrides directory-based inference.
            try:
                content = p.read_text(errors="replace")
                fm_lifecycle = _extract_lifecycle_from_frontmatter(content)
                if fm_lifecycle:
                    lifecycle = fm_lifecycle
            except OSError:
                pass

            license_class = "unknown"
            reconstructable = False
            if name in ("skill-creator", "writing-commits"):
                license_class = "apache-2.0"
                reconstructable = True
            elif name in ("docx", "pdf", "pptx", "xlsx"):
                license_class = "proprietary-restricted"
                reconstructable = False
            elif name == "product-self-knowledge":
                license_class = "unknown"
                reconstructable = False
            else:
                license_class = "apache-2.0"
                reconstructable = True

            record = SourceRecord(
                source_type="skill",
                source_path=rel,
                name=name,
                category=category,
                purpose=f"Skill for {name}",
                trigger_phrases=[name, f".{name}", "extract", "create", "read"],
                shortcuts=[name],
                scope="workspace",
                policy_constraints=[],
                reload_rules=[],
                confidence=1.0,
                license_class=license_class,
                reconstructable=reconstructable,
                lifecycle=lifecycle,
            )
            records.append(record)

    return RunContext(roots=list(roots), records=records, built_at="now")
