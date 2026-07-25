"""Skill calibration for current-generation models.

Every other check in Capsule looks for too *little* governance. This one looks
for too *much* instruction, which is the failure mode that arrived with the
Claude 5 generation: skills written defensively for earlier models are often
over-prescriptive for current ones and measurably degrade output.

The load-bearing distinction is between **policy** and **behavioral** directives.
A governance skill is supposed to say "never load what the audits will not
clear"; counting that as over-prescription tells you to weaken the gate that is
working. So security invariants are detected and excluded from the
prescriptiveness score, and reported on their own axis.

Findings are advisory. A high behavioral count is a prompt to review, never a
verdict, and `analyze` never rewrites anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from .schema import SourceRecord

SEVERITIES = ("high", "medium", "low", "info")
_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

# Bodies shorter than this are scored on absolute directive count rather than
# density: three rules in a fifty-word note is a note, not a brittle skill.
_DENSITY_FLOOR_DIRECTIVES = 2

# Behavioral directives per 100 words. Calibrated against the reference corpus,
# where `setup-writing-style` reads 1.3 at 91 behavioral directives over 7,024
# words -- the most prescriptive artifact measured, and the intended top of the
# scale rather than an arbitrary ceiling.
_BRITTLE_DENSITY = 1.0
_FIRM_DENSITY = 0.4

_MONOLITHIC_WORDS = 2000
_EXAMPLE_FENCE_LIMIT = 24
_CONFLICT_PROXIMITY = 500


@dataclass
class HealthFinding:
    check: str
    severity: str
    detail: str
    evidence: str = ""

    def line(self) -> str:
        tail = f" [{self.evidence}]" if self.evidence else ""
        return f"[{self.severity}] {self.check}: {self.detail}{tail}"


# `__init__` and downstream callers refer to this as `Finding`.
Finding = HealthFinding


@dataclass
class HealthReport:
    record: SourceRecord
    word_count: int = 0
    policy_directives: int = 0
    behavioral_directives: int = 0
    prescriptiveness: float = 0.0
    altitude: str = "right"
    findings: list[HealthFinding] = field(default_factory=list)

    @property
    def worst_severity(self) -> str:
        if not self.findings:
            return "info"
        return min((f.severity for f in self.findings), key=lambda s: _SEVERITY_RANK.get(s, 3))

    def line(self) -> str:
        return (
            f"{self.record.name:<22}{self.word_count:>6}w  "
            f"behav={self.behavioral_directives:<4} policy={self.policy_directives:<4} "
            f"presc={self.prescriptiveness:.1f}   altitude={self.altitude}"
        )


# -- text preparation ---------------------------------------------------------

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def _prose(body: str) -> str:
    """Body with frontmatter and fenced code removed.

    Directives inside a code fence are illustrations, not instructions. Mining
    them produces findings about example code the skill is telling you not to
    write.
    """
    return _FENCE.sub(" ", _FRONTMATTER.sub("", body.replace("\r\n", "\n")))


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n(?=\s*[-*#>]|\n)", text)
    return [p.strip() for p in parts if p.strip()]


# -- reasoning extraction -----------------------------------------------------

# Instructions telling the model to emit its internal reasoning as response text
# can trip the `reasoning_extraction` refusal category, which fails by silently
# falling back to a weaker model rather than by erroring. That makes this the
# highest-value check here: the cost is concrete and the symptom is invisible.
_REASONING = re.compile(
    r"(explain|show|include|output|describe|share)\s+(your|the)\s+"
    r"(reasoning|thinking|thought process|chain of thought)"
    r"[^.\n]{0,60}?"
    r"\b(in|to|within|as)\s+(your|the|a)?\s*(response|answer|output|reply|message)",
    re.IGNORECASE,
)

# A rule forbidding the behavior is not the behavior. Checked against the text
# leading up to the match rather than the whole sentence, so "never X" is
# suppressed while "do X, and never Y" is not.
_PROHIBITED = re.compile(
    r"\b(never|not|don't|do not|avoid|refrain from|must not|should not|without)\b",
    re.IGNORECASE,
)


def reasoning_extraction_risk(body: str) -> list[HealthFinding]:
    findings: list[HealthFinding] = []
    text = _prose(body)
    for match in _REASONING.finditer(text):
        lead = text[max(0, match.start() - 60):match.start()]
        if _PROHIBITED.search(lead):
            continue
        findings.append(
            HealthFinding(
                "reasoning-extraction",
                "high",
                "instructs the model to emit internal reasoning as response text; "
                "risks a refusal and a silent fallback to a weaker model",
                match.group(0)[:80].replace("\n", " "),
            )
        )
    return findings


# -- safety classifier domains ------------------------------------------------

# Content in these domains can attract classifier attention regardless of intent.
# Naming the domain is informational: it tells an author why an otherwise
# reasonable skill might behave inconsistently, not that the skill is wrong.
_DOMAINS: list[tuple[str, str, tuple[str, ...]]] = [
    ("offensive-cyber", "high", (
        "exploit development", "exploit code", "malware", "ransomware",
        "privilege escalation", "reverse shell", "payload delivery",
        "credential harvesting", "keylogger", "botnet",
    )),
    ("life-sciences", "medium", (
        "pcr reaction", "pcr amplification", "gene synthesis", "plasmid",
        "pathogen", "select agent", "viral vector", "toxin synthesis",
    )),
    ("chemical", "medium", (
        "precursor synthesis", "energetic material", "detonation", "nerve agent",
    )),
]

# A skill that forbids a topic is not a skill about that topic. This is the
# single largest false-positive source in domain matching: security guidance
# necessarily names what it prohibits.
_PROHIBITIVE_FRAME = re.compile(
    r"\b(must not|may not|never|do not|don't|cannot|prohibit\w*|forbidd?\w*|"
    r"disallow\w*|reject\w*|block\w*|refuse\w*)\b[^.\n]{0,120}$",
    re.IGNORECASE,
)


def classifier_domain_risk(body: str) -> list[HealthFinding]:
    findings: list[HealthFinding] = []
    text = _prose(body)
    lowered = text.lower()

    for domain, severity, needles in _DOMAINS:
        for needle in needles:
            start = lowered.find(needle)
            if start < 0:
                continue
            # Look back to the start of the clause for prohibitive framing.
            clause_start = max(lowered.rfind(".", 0, start), lowered.rfind("\n", 0, start)) + 1
            if _PROHIBITIVE_FRAME.search(text[clause_start:start]):
                continue
            findings.append(
                HealthFinding(
                    domain,
                    severity,
                    f"references a {domain.replace('-', ' ')} topic that may attract "
                    "safety-classifier attention regardless of intent",
                    needle,
                )
            )
            break

    return findings


# -- conflicting directives ---------------------------------------------------

# Curated opposition pairs. This is deliberately a small, named list rather than
# a general contradiction detector: the general problem needs semantic
# understanding, and a heuristic that guesses at it produces noise an author
# learns to ignore. Each entry is (topic, prohibition, requirement).
_OPPOSITIONS: list[tuple[str, str, str]] = [
    ("comments",
     r"\b(never|avoid|do not|don't|no)\s+(add\s+|write\s+|include\s+|use\s+)?comments?\b",
     r"\bdocument\s+(the\s+)?(public\s+)?(api|code|function|method|interface)"),
    ("brevity",
     r"\b(be\s+)?(brief|concise|terse|short)\b",
     r"\b(be\s+)?(exhaustive|comprehensive|thorough|detailed)\b"),
    ("examples",
     r"\b(never|avoid|do not|don't)\s+(add\s+|include\s+|give\s+)?examples?\b",
     r"\b(always\s+)?(include|provide|give)\s+(an?\s+)?examples?\b"),
    ("questions",
     r"\b(never|do not|don't)\s+ask\b",
     r"\b(always\s+)?ask\s+(the\s+user|for\s+clarification|before)\b"),
]


def conflicting_directives(body: str) -> list[HealthFinding]:
    """Flag directives that pull in opposite directions.

    Severity is weighted by distance. Two contradictory rules in adjacent
    sentences are a defect; the same pair a thousand characters apart is
    usually a scoped exception, and calling that a contradiction trains authors
    to ignore the check.
    """
    findings: list[HealthFinding] = []
    text = _prose(body)

    for topic, negative, positive in _OPPOSITIONS:
        neg = re.search(negative, text, re.IGNORECASE)
        pos = re.search(positive, text, re.IGNORECASE)
        if not (neg and pos):
            continue

        distance = abs(pos.start() - neg.start())
        near = distance < _CONFLICT_PROXIMITY
        findings.append(
            HealthFinding(
                "conflicting-directives",
                "medium" if near else "low",
                f"conflicting guidance about {topic}"
                + ("" if near else "; far apart, so probably a scoped exception"),
                f"{distance} chars apart: {neg.group(0)[:40]!r} vs {pos.group(0)[:40]!r}",
            )
        )

    return findings


# -- progressive disclosure and examples --------------------------------------


def progressive_disclosure(body: str, aux_files: int = 0) -> list[HealthFinding]:
    """Flag a body large enough that it should have been split.

    Supporting files downgrade rather than clear the finding: a long SKILL.md
    beside a `references/` directory is usually a body that failed to delegate,
    not a body that had nothing to delegate to.
    """
    words = len(body.split())
    if words <= _MONOLITHIC_WORDS:
        return []

    if aux_files == 0:
        return [HealthFinding(
            "progressive-disclosure", "medium",
            f"{words}w in a single file with no supporting files; "
            "move detail into references/ so it loads only when needed",
            f"{words} words, 0 supporting files",
        )]

    return [HealthFinding(
        "progressive-disclosure", "low",
        f"{words}w in a single file with {aux_files} supporting file(s); "
        "consider moving more detail out of SKILL.md",
        f"{words} words, {aux_files} supporting files",
    )]


# -- description quality ------------------------------------------------------

# The description is the only resident part of a skill and the sole input to
# triggering, so these are the highest-leverage authoring defects available.
# Both come straight out of the first-party authoring guidance: descriptions are
# written in third person because the text is injected into a system prompt and
# mixed point-of-view degrades discovery, and they must carry a "when to use"
# clause because the observed failure is *under*-triggering, not over.
_FIRST_OR_SECOND_PERSON = re.compile(
    r"\b(I can|I will|I help|you can use this|use this to help you|"
    r"let me|I'll|we can)\b",
    re.IGNORECASE,
)

# Deliberately generous. A description that states its trigger in an unusual
# phrasing and gets flagged anyway teaches authors the check is noise, and a
# check people route around is worse than no check. Over-passing a weak
# description costs far less than false-flagging a good one.
_TRIGGER_CLAUSE = re.compile(
    r"\b(?:use|used|using|apply|invoke|trigger|triggers|reach for|call)\b"
    r"[^.]{0,40}?\bwhen(?:ever)?\b"
    r"|\bwhen(?:ever)?\s+(?:the\s+)?(?:user|you|someone|a\s+user|working|asked)\b"
    r"|\btriggers?\s+(?:on|include|when)\b"
    r"|\bshould be used\b"
    r"|\buse\s+(?:this\s+)?(?:skill\s+)?(?:for|if)\b",
    re.IGNORECASE,
)

# Wide enough that the tail risks truncation in the skill listing before the
# trigger clause is ever read.
_DESCRIPTION_SOFT_LIMIT = 500


def description_quality(description: str) -> list[HealthFinding]:
    """Check the one field that decides whether a skill ever runs."""
    findings: list[HealthFinding] = []
    text = (description or "").strip()

    if not text:
        return [HealthFinding(
            "description-missing", "high",
            "no description; the skill can only be invoked by name and will "
            "never trigger automatically",
        )]

    person = _FIRST_OR_SECOND_PERSON.search(text)
    if person:
        findings.append(HealthFinding(
            "description-person", "medium",
            "description is not in third person; the text is injected into a "
            "system prompt and mixed point-of-view degrades discovery",
            person.group(0),
        ))

    if not _TRIGGER_CLAUSE.search(text):
        findings.append(HealthFinding(
            "description-no-trigger", "medium",
            "description says what the skill does but not when to use it; "
            "under-triggering is the common failure, so name the phrases a "
            "user would actually type",
        ))

    if len(text) > _DESCRIPTION_SOFT_LIMIT:
        findings.append(HealthFinding(
            "description-length", "low",
            f"{len(text)} chars; long descriptions risk losing their tail to "
            "listing truncation, so front-load the decisive use case",
            f"{len(text)} chars",
        ))

    return findings


def example_density(body: str) -> list[HealthFinding]:
    """Flag bodies that have become a catalogue of examples.

    Past a point, examples stop teaching a pattern and start enumerating cases,
    which is the same over-specification problem as a long rule list.
    """
    fences = body.count("```")
    if fences < _EXAMPLE_FENCE_LIMIT:
        return []
    return [HealthFinding(
        "example-density", "medium",
        f"{fences // 2} code blocks; past roughly a dozen, examples enumerate "
        "cases rather than teach a pattern",
        f"{fences // 2} fenced blocks",
    )]


# -- directive classification -------------------------------------------------

_DIRECTIVE = re.compile(
    r"\b(never|always|must not|must|do not|don't|shall not|shall|"
    r"required to|forbidden|mandatory|under no circumstances)\b",
    re.IGNORECASE,
)

# A directive touching any of these is a security invariant. Absolutes belong
# here -- they are the gate, and measuring them as prescription would recommend
# weakening it.
_POLICY_MARKERS = re.compile(
    r"\b(licen[cs]\w*|audit\w*|trust|permission\w*|privilege\w*|secur\w*|policy|"
    r"credential\w*|secret\w*|token\w*|api[_-]?key|password|sandbox|egress|"
    r"exfiltrat\w*|deny|denied|denial|gate|authori[sz]\w*|reconstruct\w*|"
    r"provenance|signature|untrusted|malicious)\b",
    re.IGNORECASE,
)


def _classify_directives(text: str) -> tuple[int, int]:
    """Split directive sentences into (policy, behavioral) counts."""
    policy = behavioral = 0
    for sentence in _sentences(text):
        hits = len(_DIRECTIVE.findall(sentence))
        if not hits:
            continue
        if _POLICY_MARKERS.search(sentence):
            policy += hits
        else:
            behavioral += hits
    return policy, behavioral


def _altitude(behavioral: int, density: float) -> str:
    """Map behavioral prescription onto an advisory altitude band.

    Density alone misreads short bodies -- three rules in a fifty-word note is
    a density of six and a perfectly reasonable note -- so an absolute floor
    applies first.
    """
    if behavioral <= _DENSITY_FLOOR_DIRECTIVES:
        return "right"
    if density >= _BRITTLE_DENSITY:
        return "brittle"
    if density >= _FIRM_DENSITY:
        return "firm"
    return "right"


def analyze(record: SourceRecord, body: str = "", aux_files: int = 0) -> HealthReport:
    """Assess one skill's calibration. Advisory only; nothing is rewritten."""
    text = _prose(body)
    words = len(text.split())
    policy, behavioral = _classify_directives(text)

    # Per 100 words, so the number is comparable across skills of any size.
    density = round((behavioral / words * 100) if words else 0.0, 2)

    findings: list[HealthFinding] = []
    findings.extend(reasoning_extraction_risk(body))
    findings.extend(classifier_domain_risk(body))
    findings.extend(conflicting_directives(body))
    findings.extend(progressive_disclosure(body, aux_files))
    findings.extend(example_density(body))

    return HealthReport(
        record=record,
        word_count=words,
        policy_directives=policy,
        behavioral_directives=behavioral,
        prescriptiveness=density,
        altitude=_altitude(behavioral, density),
        findings=findings,
    )


def summarize(reports: Sequence[HealthReport]) -> dict[str, int]:
    """Count reports by their worst finding. Clean means no findings at all."""
    counts = {"high": 0, "medium": 0, "low": 0, "clean": 0}
    for report in reports:
        if not report.findings:
            counts["clean"] += 1
        else:
            counts[report.worst_severity] = counts.get(report.worst_severity, 0) + 1
    return counts
