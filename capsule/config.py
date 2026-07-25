"""Configuration and corpus-level diagnostics.

`capsule.toml` is the single customisation surface. Everything Capsule does that
an organisation might reasonably want different lives here: roots, thresholds,
overrides, precedence, and custom rules.

This module also implements two whole-corpus checks that no per-skill validator
can perform, because they are properties of the *set* rather than any member.
Both come straight out of the field reports on why skills silently stop working.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .rules import RuleSet, default_ruleset
from .schema import RunContext, SourceRecord

# Combined skill descriptions are concatenated into the agent's context to drive
# selection. Past a budget, the tail is truncated and those skills simply never
# fire -- with no error surfaced anywhere. The exact ceiling is host-dependent,
# so this is a configurable early-warning line, not a spec constant.
DEFAULT_DESCRIPTION_BUDGET = 12000


@dataclass
class Precedence:
    """Declares that one skill is the specialist and should win a near-tie.

    The open skills ecosystem has no mechanism for expressing that skill B is a
    specialisation of skill A, so overlapping skills from unrelated authors
    compete for the same triggers and the winner is effectively arbitrary. This
    is the local fix: state the relationship, apply it at rerank time.
    """

    prefer: str
    over: str
    when: str = ""
    reason: str = ""
    # Score bonus applied to the preferred skill. The default is deliberately
    # advisory: it settles near-ties without overriding a decisively better
    # match. Raise it to make the relationship authoritative.
    weight: float = 2.0

    def applies(self, task: str) -> bool:
        return not self.when or self.when.lower() in task.lower()


@dataclass
class CapsuleConfig:
    writable_roots: list[str] = field(default_factory=lambda: ["/home/claude", "/mnt/user-data/outputs"])
    readonly_roots: list[str] = field(default_factory=lambda: ["/mnt/skills", "/mnt/user-data/uploads", "/mnt/transcripts"])
    discover_roots: list[str] = field(default_factory=lambda: ["/mnt/skills/public", "/mnt/skills/examples"])
    allow_restricted_reconstruction: bool = False
    allow_unaudited_registry_skills: bool = False
    min_route_score: float = 1.5
    shortlist_size: int = 4
    description_budget: int = DEFAULT_DESCRIPTION_BUDGET
    use_default_rules: bool = True
    ruleset: RuleSet = field(default_factory=default_ruleset)
    precedence: list[Precedence] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None) -> "CapsuleConfig":
        if path is None or not Path(path).exists():
            return cls()
        data = tomllib.loads(Path(path).read_text())
        policy = data.get("policy", {})
        routing = data.get("routing", {})

        use_defaults = policy.get("use_default_rules", True)
        ruleset = default_ruleset() if use_defaults else RuleSet()
        for rule in RuleSet.from_dict(data).rules:
            ruleset.add(rule)

        return cls(
            writable_roots=policy.get("writable_roots", cls().writable_roots),
            readonly_roots=policy.get("readonly_roots", cls().readonly_roots),
            discover_roots=data.get("discover", {}).get("roots", cls().discover_roots),
            allow_restricted_reconstruction=policy.get("allow_restricted_reconstruction", False),
            allow_unaudited_registry_skills=policy.get("allow_unaudited_registry_skills", False),
            min_route_score=routing.get("min_score", 1.5),
            shortlist_size=routing.get("shortlist_size", 4),
            description_budget=routing.get("description_budget", DEFAULT_DESCRIPTION_BUDGET),
            use_default_rules=use_defaults,
            ruleset=ruleset,
            precedence=[Precedence(**p) for p in routing.get("precedence", [])],
        )


# -- corpus diagnostics --------------------------------------------------------


@dataclass
class BudgetReport:
    total_chars: int
    budget: int
    skill_count: int
    at_risk: list[str] = field(default_factory=list)

    @property
    def over_budget(self) -> bool:
        return self.total_chars > self.budget

    def line(self) -> str:
        state = "OVER" if self.over_budget else "ok"
        return (
            f"description budget: {self.total_chars}/{self.budget} chars "
            f"across {self.skill_count} skills [{state}]"
        )


def description_budget(context: RunContext, budget: int = DEFAULT_DESCRIPTION_BUDGET) -> BudgetReport:
    """Detect silent truncation risk across the whole skill set.

    When combined descriptions exceed the host's budget, the overflow is dropped
    and those skills stop triggering with no visible error. Capsule cannot read
    the host's real ceiling, but it can measure the corpus and name which skills
    sit past the configured line.
    """
    skills = [r for r in context.records if r.source_type in ("skill", "registry")]
    running, at_risk = 0, []
    for record in skills:
        # Approximates what a host concatenates: name plus description text.
        entry = len(record.name) + len(record.purpose) + 8
        running += entry
        if running > budget:
            at_risk.append(record.name)
    return BudgetReport(running, budget, len(skills), at_risk)


@dataclass
class OverlapReport:
    pairs: list[tuple[str, str, float]] = field(default_factory=list)

    def worst(self, limit: int = 5) -> list[tuple[str, str, float]]:
        return sorted(self.pairs, key=lambda p: -p[2])[:limit]


def trigger_overlap(context: RunContext, threshold: float = 0.35) -> OverlapReport:
    """Find skill pairs competing for the same trigger vocabulary.

    Routing ambiguity is the most-cited cause of skill misfires: when several
    skills share trigger vocabulary, which one fires is effectively a coin flip,
    and nothing in the format catches it. This surfaces the collisions before
    they cost a production run.

    Compares *tokens*, not whole phrases. An earlier version did set-overlap on
    the phrase strings and reported zero collisions across a corpus that visibly
    has them, because every skill's phrase list contains its own name and file
    extensions -- trivially distinct strings that drive Jaccard to zero while the
    underlying vocabulary overlaps heavily. Each skill's own name tokens are
    excluded for the same reason.
    """
    import re

    skills = [r for r in context.records if r.source_type == "skill"]
    stop = {"use", "when", "this", "skill", "user", "the", "and", "for", "with", "any", "file", "files"}

    def vocab(record: SourceRecord) -> set[str]:
        own = set(re.findall(r"[a-z0-9]+", record.name.lower()))
        text = " ".join(record.trigger_phrases) + " " + record.purpose
        tokens = set(re.findall(r"[a-z][a-z0-9]{2,}", text.lower()))
        return tokens - own - stop

    pairs = []
    for i, a in enumerate(skills):
        set_a = vocab(a)
        if len(set_a) < 3:
            continue
        for b in skills[i + 1:]:
            set_b = vocab(b)
            if len(set_b) < 3:
                continue
            jaccard = len(set_a & set_b) / len(set_a | set_b)
            if jaccard >= threshold:
                pairs.append((a.name, b.name, round(jaccard, 2)))
    return OverlapReport(pairs)


# -- lethal trifecta -----------------------------------------------------------

_PRIVATE_DATA = r"(\.ssh/|\.aws/|\.env\b|credential|api[_-]?key|password|token|wallet)"
_UNTRUSTED_INPUT = r"(web_?fetch|requests\.get|urlopen|curl |wget |scrape|fetch\()"
_EGRESS = r"(requests\.post|urlopen|curl -X POST|webhook|https?://[^\s)\"']+)"


@dataclass
class TrifectaReport:
    name: str
    private_data: bool
    untrusted_input: bool
    egress: bool

    @property
    def complete(self) -> bool:
        return self.private_data and self.untrusted_input and self.egress

    def line(self) -> str:
        legs = [
            "private-data" if self.private_data else "",
            "untrusted-input" if self.untrusted_input else "",
            "egress" if self.egress else "",
        ]
        present = ", ".join(l for l in legs if l) or "none"
        mark = "TRIFECTA" if self.complete else f"{sum([self.private_data, self.untrusted_input, self.egress])}/3"
        return f"{self.name}: {mark} ({present})"


def lethal_trifecta(record: SourceRecord, body: str) -> TrifectaReport:
    """Flag the co-occurrence of private data, untrusted content, and egress.

    Any one leg is ordinary. All three in one skill is the configuration under
    which a prompt injection becomes an exfiltration, which is why it is worth
    naming as a combination rather than three separate findings.
    """
    import re

    return TrifectaReport(
        name=record.name,
        private_data=bool(re.search(_PRIVATE_DATA, body, re.IGNORECASE)),
        untrusted_input=bool(re.search(_UNTRUSTED_INPUT, body, re.IGNORECASE)),
        egress=bool(re.search(_EGRESS, body, re.IGNORECASE)),
    )
