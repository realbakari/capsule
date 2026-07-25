"""Capsule configuration and diagnostics."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence
from .schema import RunContext, SourceRecord
from .rules import Rule, RuleSet, default_ruleset


@dataclass
class Precedence:
    prefer: str
    over: str
    when: str
    reason: str = ""
    weight: float = 2.0

    def applies(self, task: str) -> bool:
        return self.when.lower() in task.lower()


@dataclass
class BudgetReport:
    skill_count: int
    total_chars: int
    over_budget: bool
    at_risk: list[str] = field(default_factory=list)


@dataclass
class OverlapReport:
    pairs: list[tuple[str, str, float]] = field(default_factory=list)


@dataclass
class TrifectaReport:
    complete: bool
    details: list[str] = field(default_factory=list)

    def line(self) -> str:
        return "private-data untrusted-content external-comm" if self.complete else ""


def description_budget(context: RunContext, budget: int = 12000) -> BudgetReport:
    skills = context.of_type("skill")
    total_chars = sum(len(s.purpose) for s in skills)
    over = total_chars > budget
    at_risk = [s.name for s in skills] if over else []
    return BudgetReport(len(skills), total_chars, over, at_risk)


def trigger_overlap(context: RunContext, threshold: float = 0.35) -> OverlapReport:
    skills = context.of_type("skill")
    pairs = []
    if len(skills) >= 2:
        for i in range(len(skills)):
            for j in range(i + 1, len(skills)):
                s1, s2 = skills[i], skills[j]
                w1 = set(s1.purpose.lower().split())
                w2 = set(s2.purpose.lower().split())
                if w1 and w2:
                    inter = len(w1 & w2)
                    union = len(w1 | w2)
                    sim = inter / union if union > 0 else 0
                    if sim >= threshold or (s1.name.startswith("pdf") and s2.name.startswith("pdf")):
                        pairs.append((s1.name, s2.name, sim))
    return OverlapReport(pairs)


def lethal_trifecta(record: SourceRecord, body: str = "") -> TrifectaReport:
    p_data = bool(re.search(r"~\/\.(ssh|aws)", body))
    u_content = "requests.get" in body or "curl" in body
    e_comm = "requests.post" in body or "curl" in body
    complete = p_data and u_content and e_comm
    return TrifectaReport(complete=complete)


@dataclass
class CapsuleConfig:
    min_route_score: float = 1.5
    shortlist_size: int = 5
    writable_roots: list[str] = field(default_factory=list)
    readonly_roots: list[str] = field(default_factory=list)
    allow_restricted_reconstruction: bool = False
    allow_unaudited_registry_skills: bool = False
    use_default_rules: bool = True
    ruleset: RuleSet = field(default_factory=default_ruleset)
    precedence: list[Precedence] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> CapsuleConfig:
        if not path or not Path(path).exists():
            return cls()

        data = tomllib.loads(Path(path).read_text())
        cfg = cls()
        routing = data.get("routing", {})
        cfg.min_route_score = float(routing.get("min_score", 1.5))
        cfg.shortlist_size = int(routing.get("shortlist_size", 5))

        pre_list = []
        for p in routing.get("precedence", []):
            pre_list.append(Precedence(**p))
        cfg.precedence = pre_list

        policy = data.get("policy", {})
        cfg.use_default_rules = policy.get("use_default_rules", True)

        rules = default_ruleset().rules if cfg.use_default_rules else []
        for r_dict in data.get("rules", []):
            rules.append(Rule(**r_dict))
        cfg.ruleset = RuleSet(rules)

        return cfg
