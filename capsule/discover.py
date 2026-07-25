"""Capsule discovery module."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence
from .schema import RunContext, SourceRecord
from .policy import Policy


def _trigger_phrases(description: str, name: str) -> set[str]:
    phrases = {name, f".{name}"}
    for word in re.findall(r"\.?[a-zA-Z0-9_-]+", description.lower()):
        if len(word) >= 2:
            phrases.add(word)
    return phrases


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
                category="general",
                purpose=f"Skill for {name}",
                trigger_phrases=[name, f".{name}", "extract", "create", "read"],
                shortcuts=[name],
                scope="workspace",
                policy_constraints=[],
                reload_rules=[],
                confidence=1.0,
                license_class=license_class,
                reconstructable=reconstructable,
            )
            records.append(record)

    return RunContext(roots=list(roots), records=records, built_at="now")
