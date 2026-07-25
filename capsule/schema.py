"""Capsule schema definitions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class SourceRecord:
    source_type: str
    source_path: str
    name: str
    category: str
    purpose: str
    trigger_phrases: list[str] = field(default_factory=list)
    shortcuts: list[str] = field(default_factory=list)
    scope: str = "workspace"
    policy_constraints: list[str] = field(default_factory=list)
    reload_rules: list[str] = field(default_factory=list)
    confidence: float = 1.0
    license_class: str = "unknown"
    reconstructable: bool = False
    installs: int = 0
    trust_verdict: str = "allow"

    def __post_init__(self):
        valid_types = {"skill", "doc", "instruction", "registry"}
        if self.source_type not in valid_types:
            raise ValueError(f"unknown source_type: {self.source_type}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence out of range: {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def digest(self) -> str:
        return f"{self.name} ({self.source_type}) [{self.category}]: {self.purpose}"


@dataclass
class RunContext:
    roots: list[str] = field(default_factory=list)
    records: list[SourceRecord] = field(default_factory=list)
    built_at: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "roots": self.roots,
            "records": [r.to_dict() for r in self.records],
            "built_at": self.built_at,
        })

    @classmethod
    def from_json(cls, data_str: str) -> RunContext:
        data = json.loads(data_str)
        records = [SourceRecord(**r) for r in data.get("records", [])]
        return cls(
            roots=data.get("roots", []),
            records=records,
            built_at=data.get("built_at", ""),
        )

    def of_type(self, source_type: str) -> list[SourceRecord]:
        return [r for r in self.records if r.source_type == source_type]

    def by_name(self, name: str) -> Optional[SourceRecord]:
        for r in self.records:
            if r.name == name:
                return r
        return None
