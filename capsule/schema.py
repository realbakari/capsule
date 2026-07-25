"""Capsule run-context data model.

Every discovered source condenses to exactly one SourceRecord. The field set is
fixed by the Capsule contract and must not be trimmed: downstream routing and
policy both read fields that look optional but are not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


# Reconstruction eligibility, derived from license, never from the caller.
LICENSE_APACHE = "apache-2.0"
LICENSE_RESTRICTED = "proprietary-restricted"
LICENSE_UNKNOWN = "unknown"

SOURCE_TYPES = {
    "skill",
    "agent",
    "instruction",
    "doc",
    "manifest",
    "config",
    "tooling",
    "registry",
}

# An agent definition that names no tools inherits the host's full tool set.
# Recorded explicitly rather than as an empty list, because "granted everything"
# and "granted nothing" are opposite facts and must not share a representation.
TOOLS_INHERIT_ALL = "*"

# Maturity, declared in frontmatter or inferred from directory layout. Kept to
# three values on purpose: a taxonomy an author has to look up is one they will
# get wrong, and routing only needs to know "ready", "not yet", "not anymore".
LIFECYCLES = {"stable", "in-progress", "deprecated"}


@dataclass
class SourceRecord:
    """One condensed source in the run context."""

    source_type: str
    source_path: str
    name: str
    category: str
    purpose: str
    trigger_phrases: list[str] = field(default_factory=list)
    shortcuts: list[str] = field(default_factory=list)
    scope: str = "repo"
    policy_constraints: list[str] = field(default_factory=list)
    reload_rules: str = "on-task-change"
    confidence: float = 0.0

    # Derived metadata. Not part of the required field set, but required for
    # policy decisions to be reproducible without re-reading the filesystem.
    license_class: str = LICENSE_UNKNOWN
    reconstructable: bool = False
    body_words: int = 0
    aux_dirs: list[str] = field(default_factory=list)
    content_hash: str = ""
    lifecycle: str = "stable"

    # Tools an agent definition grants. `[TOOLS_INHERIT_ALL]` means the
    # definition named none and therefore inherits everything the host allows.
    # Empty means "not applicable" -- skills carry their grants in frontmatter
    # the host reads directly.
    tool_grants: list[str] = field(default_factory=list)
    model: str = ""

    # Registry provenance and trust. Populated for source_type "registry"; left
    # empty for local sources, which are governed by the license gate instead.
    registry_id: str = ""
    installs: int = 0
    is_duplicate: bool = False
    trust_verdict: str = ""
    trust_status: str = ""
    trust_risk: str = ""
    trust_reason: str = ""

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(
                f"unknown source_type {self.source_type!r}; "
                f"expected one of {sorted(SOURCE_TYPES)}"
            )
        if self.lifecycle not in LIFECYCLES:
            raise ValueError(
                f"unknown lifecycle {self.lifecycle!r}; "
                f"expected one of {sorted(LIFECYCLES)}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")
        if not self.name:
            raise ValueError("name is required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRecord":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def digest(self) -> str:
        """One-line form used in the condensed index printout."""
        if self.source_type == "registry":
            dup = " dup" if self.is_duplicate else ""
            return (
                f"[registry] {self.name} <{self.registry_id}> "
                f"installs={self.installs} trust={self.trust_verdict}"
                f"/{self.trust_risk or 'n-a'}{dup}"
            )
        gate = "OK" if self.reconstructable else "DENY"
        return (
            f"[{self.source_type}] {self.name} <{self.category}> "
            f"conf={self.confidence:.2f} lic={self.license_class} recon={gate}"
        )


@dataclass
class RunContext:
    """The condensed index plus the provenance needed to rebuild it."""

    roots: list[str] = field(default_factory=list)
    records: list[SourceRecord] = field(default_factory=list)
    built_at: str = ""
    capsule_version: str = "0.1.0"

    def by_name(self, name: str) -> SourceRecord | None:
        for record in self.records:
            if record.name == name:
                return record
        return None

    def of_type(self, source_type: str) -> list[SourceRecord]:
        return [r for r in self.records if r.source_type == source_type]

    def of_category(self, category: str) -> list[SourceRecord]:
        return [r for r in self.records if r.category == category]

    def of_lifecycle(self, lifecycle: str) -> list[SourceRecord]:
        return [r for r in self.records if r.lifecycle == lifecycle]

    def of_category(self, category: str) -> list[SourceRecord]:
        return [r for r in self.records if r.category == category]

    def of_lifecycle(self, lifecycle: str) -> list[SourceRecord]:
        return [r for r in self.records if r.lifecycle == lifecycle]

    def reconstructable(self) -> list[SourceRecord]:
        return [r for r in self.records if r.reconstructable]

    def loadable(self) -> list[SourceRecord]:
        """Records no gate blocks: licensed locals, plus trust-cleared remotes."""
        out = []
        for record in self.records:
            if record.source_type == "registry":
                if record.trust_verdict == "allow":
                    out.append(record)
            else:
                out.append(record)
        return out

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(
            {
                "capsule_version": self.capsule_version,
                "built_at": self.built_at,
                "roots": self.roots,
                "record_count": len(self.records),
                "records": [r.to_dict() for r in self.records],
            },
            indent=indent,
            sort_keys=False,
        )

    @classmethod
    def from_json(cls, text: str) -> "RunContext":
        data = json.loads(text)
        return cls(
            roots=data.get("roots", []),
            records=[SourceRecord.from_dict(r) for r in data.get("records", [])],
            built_at=data.get("built_at", ""),
            capsule_version=data.get("capsule_version", "0.1.0"),
        )
