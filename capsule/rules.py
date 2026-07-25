"""Capsule rule engine and default rulesets (OWASP AST10)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence
from .schema import SourceRecord

ACTION_DENY = "deny"
ACTION_APPROVAL = "approval"
ACTION_FLAG = "flag"


@dataclass
class RuleHit:
    rule_id: str
    action: str
    reason: str
    evidence: str = ""


@dataclass
class Rule:
    id: str
    action: str
    reason: str
    applies_to: Optional[str] = None
    name_regex: Optional[str] = None
    body_regex: Optional[str] = None

    def __post_init__(self):
        if self.action not in (ACTION_DENY, ACTION_APPROVAL, ACTION_FLAG):
            raise ValueError(f"unknown action: {self.action}")
        if not self.reason:
            raise ValueError("rule reason required")
        if self.body_regex:
            try:
                re.compile(self.body_regex)
            except re.error as e:
                raise ValueError(f"invalid regex: {e}")
        if self.name_regex:
            try:
                re.compile(self.name_regex)
            except re.error as e:
                raise ValueError(f"invalid regex: {e}")

    def matches(self, record: SourceRecord, body: str = "") -> tuple[bool, str]:
        if not self.name_regex and not self.body_regex and not self.applies_to:
            return False, ""

        if self.applies_to:
            if self.applies_to == "registry" and record.source_type != "registry":
                return False, ""
            if self.applies_to == "skill" and record.source_type != "skill":
                return False, ""

        if self.name_regex:
            if not re.search(self.name_regex, record.name):
                return False, ""

        if self.body_regex:
            match = re.search(self.body_regex, body)
            if not match:
                return False, ""
            return True, f"body match: '{match.group(0)}'"

        return True, "rule matched"


class RuleSet:
    def __init__(self, rules: Optional[Sequence[Rule]] = None):
        self.rules = list(rules) if rules is not None else []

    def evaluate(self, record: SourceRecord, body: str = "") -> list[RuleHit]:
        hits = []
        for r in self.rules:
            matched, ev = r.matches(record, body)
            if matched:
                hits.append(RuleHit(r.id, r.action, r.reason, ev))
        return hits

    def verdict(self, hits: list[RuleHit]) -> Optional[str]:
        if not hits:
            return None
        actions = {h.action for h in hits}
        if ACTION_DENY in actions:
            return ACTION_DENY
        if ACTION_APPROVAL in actions:
            return ACTION_APPROVAL
        if ACTION_FLAG in actions:
            return ACTION_FLAG
        return None


def default_ruleset() -> RuleSet:
    return RuleSet([
        Rule(
            id="ast02-remote-fetch-execute",
            action=ACTION_DENY,
            reason="Remote script fetch and execution (curl | bash)",
            body_regex=r"curl[^\n|]*\|\s*bash",
        ),
        Rule(
            id="ast03-credential-paths",
            action=ACTION_DENY,
            reason="Accessing sensitive credential path",
            body_regex=r"~/\.(ssh|aws)/",
        ),
        Rule(
            id="ast03-identity-file-write",
            action=ACTION_DENY,
            reason="Appending to memory/identity configuration",
            body_regex=r">>\s*MEMORY\.md",
        ),
        Rule(
            id="ast05-unsafe-yaml-load",
            action=ACTION_DENY,
            reason="Unsafe YAML loading without SafeLoader",
            body_regex=r"yaml\.load\((?![^)]*Loader=yaml\.SafeLoader)",
        ),
        Rule(
            id="ast09-destructive-shell",
            action=ACTION_DENY,
            reason="Destructive recursive deletion",
            body_regex=r"rm\s+-rf\s+/(var|etc|usr|home|tmp)",
        ),
    ])
