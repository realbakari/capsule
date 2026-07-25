"""User-defined policy rules.

Capsule's built-in gates (license, trust, path) cover what is universal. Anything
organisation-specific belongs here: rules are declared in `capsule.toml`, or
registered programmatically, and evaluated alongside the built-ins.

Two extension paths, deliberately different in power:

  - **Declarative rules** (TOML) are data. They match on record fields and skill
    body text via regex. Safe to accept from a repo you are already trusting.
  - **Programmatic rules** (`policy.add_rule`) are Python callables. They can do
    anything, so Capsule never auto-imports rule files from disk — that would be
    exactly the repository-controlled-config execution path that CVE-2025-59536
    exploited. The caller must import and register them explicitly.

Rule actions escalate but never de-escalate a built-in denial: a rule can turn an
allow into a deny, never a deny into an allow. Loosening is what override flags
are for, and those are audited individually.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .schema import SourceRecord

ACTION_DENY = "deny"
ACTION_APPROVAL = "approval"
ACTION_FLAG = "flag"
ACTIONS = {ACTION_DENY, ACTION_APPROVAL, ACTION_FLAG}


@dataclass
class RuleHit:
    rule_id: str
    action: str
    reason: str
    evidence: str = ""

    def line(self) -> str:
        tail = f" [{self.evidence}]" if self.evidence else ""
        return f"{self.action}:{self.rule_id} -- {self.reason}{tail}"


@dataclass
class Rule:
    """A declarative match-and-act rule. All specified matchers must match."""

    id: str
    action: str
    reason: str
    description: str = ""
    applies_to: str = "*"
    body_regex: str | None = None
    name_regex: str | None = None
    path_prefix: str | None = None
    license_class: list[str] = field(default_factory=list)
    trust_verdict: list[str] = field(default_factory=list)
    category: list[str] = field(default_factory=list)
    max_installs: int | None = None
    min_installs: int | None = None

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"rule {self.id}: action must be one of {sorted(ACTIONS)}")
        if not self.reason:
            raise ValueError(f"rule {self.id}: a reason is required so audits are readable")
        for pattern in (self.body_regex, self.name_regex):
            if pattern:
                try:
                    re.compile(pattern, re.IGNORECASE)
                except re.error as exc:
                    raise ValueError(f"rule {self.id}: bad regex {pattern!r}: {exc}") from exc

    def matches(self, record: SourceRecord, body: str = "") -> tuple[bool, str]:
        if self.applies_to != "*" and record.source_type != self.applies_to:
            return False, ""
        if self.license_class and record.license_class not in self.license_class:
            return False, ""
        if self.trust_verdict and record.trust_verdict not in self.trust_verdict:
            return False, ""
        if self.category and record.category not in self.category:
            return False, ""
        if self.path_prefix and not record.source_path.startswith(self.path_prefix):
            return False, ""
        if self.min_installs is not None and record.installs < self.min_installs:
            return False, ""
        if self.max_installs is not None and record.installs > self.max_installs:
            return False, ""

        evidence = ""
        if self.name_regex:
            if not re.search(self.name_regex, record.name, re.IGNORECASE):
                return False, ""
            evidence = f"name~{self.name_regex}"
        if self.body_regex:
            found = re.search(self.body_regex, body, re.IGNORECASE)
            if not found:
                return False, ""
            snippet = found.group(0)[:60].replace("\n", " ")
            evidence = f"body match: {snippet!r}"

        # A rule with no matchers at all would fire on everything; treat that as
        # an authoring error rather than silently gating the whole corpus.
        if not any([self.name_regex, self.body_regex, self.license_class,
                    self.trust_verdict, self.category, self.path_prefix,
                    self.min_installs is not None, self.max_installs is not None]):
            return False, ""
        return True, evidence


ProgrammaticRule = Callable[[SourceRecord, str], RuleHit | None]


class RuleSet:
    """Declarative rules plus explicitly registered programmatic ones."""

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self.rules: list[Rule] = list(rules or [])
        self.programmatic: list[ProgrammaticRule] = []

    def add(self, rule: Rule) -> None:
        self.rules.append(rule)

    def add_programmatic(self, fn: ProgrammaticRule) -> None:
        """Register a Python rule. Must be imported by the caller, never by path."""
        self.programmatic.append(fn)

    def evaluate(self, record: SourceRecord, body: str = "") -> list[RuleHit]:
        hits: list[RuleHit] = []
        for rule in self.rules:
            matched, evidence = rule.matches(record, body)
            if matched:
                hits.append(RuleHit(rule.id, rule.action, rule.reason, evidence))
        for fn in self.programmatic:
            try:
                hit = fn(record, body)
            except Exception as exc:  # a broken rule must not open the gate
                hits.append(RuleHit(
                    getattr(fn, "__name__", "programmatic"), ACTION_DENY,
                    f"rule raised {type(exc).__name__}; failing closed", str(exc)[:80],
                ))
                continue
            if hit:
                hits.append(hit)
        return hits

    def verdict(self, hits: list[RuleHit]) -> str | None:
        """Strongest action among hits: deny beats approval beats flag."""
        if any(h.action == ACTION_DENY for h in hits):
            return ACTION_DENY
        if any(h.action == ACTION_APPROVAL for h in hits):
            return ACTION_APPROVAL
        if hits:
            return ACTION_FLAG
        return None

    @classmethod
    def from_toml(cls, path: str | Path) -> "RuleSet":
        data = tomllib.loads(Path(path).read_text())
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "RuleSet":
        rules = []
        for entry in data.get("rules", []):
            known = {f for f in Rule.__dataclass_fields__}
            rules.append(Rule(**{k: v for k, v in entry.items() if k in known}))
        return cls(rules)


# Starter ruleset mapped to OWASP Agentic Skills Top 10 (AST10, v1.0-2026).
#
# These are *triage signals*, not verdicts. Snyk's own follow-up to ToxicSkills
# ("Why Your Skill Scanner Is Just False Security") shows pattern matching misses
# the majority of critical threats, which are natural-language instructions with
# no code signature at all. Treat a clean lint as "nothing obvious", never "safe".
DEFAULT_RULES = [
    Rule(
        id="ast02-remote-fetch-execute",
        action=ACTION_DENY,
        reason="fetches remote content and pipes it to a shell; supply-chain execution path",
        description="AST02 Supply Chain Compromise",
        body_regex=r"(curl|wget)[^\n|]{0,120}\|\s*(bash|sh|zsh|source|python3?)",
    ),
    Rule(
        id="ast03-credential-paths",
        action=ACTION_APPROVAL,
        reason="references credential or key material paths; confirm least privilege",
        description="AST03 Over-Privileged Skills",
        body_regex=r"(\.aws/credentials|\.ssh/id_[a-z0-9]+|\.env\b|AWS_SECRET|PRIVATE_KEY)",
    ),
    Rule(
        id="ast03-identity-file-write",
        action=ACTION_DENY,
        reason="writes to agent identity or memory files; session-persistent backdoor vector",
        description="AST03/AST01, per the ClawHavoc pattern",
        body_regex=r">>?\s*(SOUL|MEMORY|AGENTS|CLAUDE)\.md",
    ),
    Rule(
        id="ast04-hidden-html-directives",
        action=ACTION_APPROVAL,
        reason="contains HTML comments; a documented channel for instructions invisible on render",
        description="AST04 Insecure Metadata",
        body_regex=r"<!--(?:(?!-->)[\s\S]){40,}-->",
    ),
    Rule(
        id="ast05-unsafe-yaml-load",
        action=ACTION_DENY,
        reason="uses an unsafe YAML loader; arbitrary object construction",
        description="AST05 Unsafe Deserialization",
        body_regex=r"yaml\.(unsafe_)?load\s*\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)",
    ),
    Rule(
        id="ast07-unpinned-dependency",
        action=ACTION_FLAG,
        reason="installs dependencies without a pinned version; update-drift exposure",
        description="AST07 Update Drift",
        body_regex=r"(pip install|npm install|npx)\s+(?!-)[\w@/.-]+(?![=@]\d)",
    ),
    Rule(
        id="fable-reasoning-extraction",
        action=ACTION_APPROVAL,
        reason="tells the model to emit its reasoning as response text; risks a "
               "refusal and silent fallback to a weaker model",
        description="Claude 5 generation: reasoning_extraction refusal category",
        body_regex=r"(explain|show|include|output)\s+(your|the)\s+"
                   r"(reasoning|thinking|thought process)[^.\n]{0,60}"
                   r"\b(in|to|within|as)\s+(your|the|a)?\s*(response|answer|output|reply)",
    ),
    Rule(
        id="ast09-destructive-shell",
        action=ACTION_DENY,
        reason="contains an unguarded destructive filesystem command",
        description="AST09 No Governance",
        body_regex=r"rm\s+-[rf]{1,2}\s+[/~$]",
    ),
]


def default_ruleset() -> RuleSet:
    return RuleSet(list(DEFAULT_RULES))
