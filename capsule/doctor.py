"""Model Calibration Doctor (`capsule doctor`).

Inverts the legacy instinct that longer, more explicit guidance is always safer.
Analyzes skill bodies for:
  - Body length & word budget (<500 lines / <3000 words)
  - Progressive disclosure (checks if complex topics use references/)
  - Prescriptive altitude (ratio of prescriptive MUST/NEVER to explanation)
  - Example density (ratio of code block lines to prose)
  - Crucially: Security invariants are excluded from prescription penalties!
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from .schema import RunContext, SourceRecord


@dataclass
class Diagnostic:
    severity: str  # "high", "medium", "info"
    kind: str
    message: str


@dataclass
class SkillAudit:
    name: str
    word_count: int
    line_count: int
    prescriptive_words: int
    explanatory_words: int
    prescription_ratio: float
    altitude: str  # "optimal", "verbose", "brittle"
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def report(self) -> str:
        lines = [
            f"{self.name:<24} {self.word_count:>5}w  lines={self.line_count:<4} presc_ratio={self.prescription_ratio:.2f}  altitude={self.altitude}"
        ]
        for diag in self.diagnostics:
            lines.append(f"  [{diag.severity}] {diag.kind}: {diag.message}")
        return "\n".join(lines)


# Prescriptive imperative keywords (excluding security invariants)
_PRESCRIPTIVE_WORDS = {
    "must", "always", "never", "required", "do not", "don't",
    "forbidden", "mandatory", "shall",
}

_SECURITY_PATTERNS = [
    r"license", r"auth", r"secret", r"api_key", r"password",
    r"sandbox", r"reconstructable",
]


def audit_skill(skill_dir_path: str | Path, name: str = "") -> SkillAudit:
    path = Path(skill_dir_path)
    skill_md = path / "SKILL.md" if path.is_dir() else path
    if not name and path.is_dir():
        name = path.name

    if not skill_md.exists():
        return SkillAudit(
            name=name, word_count=0, line_count=0,
            prescriptive_words=0, explanatory_words=0,
            prescription_ratio=0.0, altitude="unknown",
            diagnostics=[Diagnostic("high", "missing", "SKILL.md file not found")],
        )

    content = skill_md.read_text(errors="replace")
    lines = content.splitlines()
    words = re.findall(r"\b\w+\b", content.lower())
    total_words = len(words)
    total_lines = len(lines)

    # Filter out security-related lines before counting prescriptive penalties
    prescriptive_count = 0
    explanatory_count = 0

    for line in lines:
        line_lower = line.lower()
        # Skip security invariant lines from penalty
        if any(re.search(pat, line_lower) for pat in _SECURITY_PATTERNS):
            continue
        line_words = re.findall(r"\b\w+\b", line_lower)
        if any(pw in line_words for pw in _PRESCRIPTIVE_WORDS):
            prescriptive_count += len(line_words)
        else:
            explanatory_count += len(line_words)

    ratio = prescriptive_count / max(1, total_words)

    altitude = "optimal"
    if total_lines > 500 or total_words > 3000:
        altitude = "brittle"
    elif ratio > 0.4:
        altitude = "brittle"
    elif total_words > 1500:
        altitude = "verbose"

    diagnostics = []
    if total_lines > 500:
        refs_dir = path / "references" if path.is_dir() else path.parent / "references"
        if not refs_dir.is_dir():
            diagnostics.append(Diagnostic(
                "medium", "progressive-disclosure",
                f"{total_lines} lines in single SKILL.md without supporting references/ directory",
            ))

    if ratio > 0.4:
        diagnostics.append(Diagnostic(
            "high", "prescriptive-altitude",
            f"Prescription ratio {ratio:.2f} is high (>0.4); model may experience instruction friction",
        ))

    # Check for unproductive verification loops (Opus 5 System Card Section 2.2.6 & Section 6.2.1)
    loop_keywords = [
        "keep retrying until", "repeat until all pass", "endless verification",
        "retry until 100%", "continue debugging until all pass", "never stop until",
    ]
    content_lower = content.lower()
    for lkw in loop_keywords:
        if lkw in content_lower:
            diagnostics.append(Diagnostic(
                "medium", "unproductive-verification-loop",
                f"Skill contains over-verification trigger '{lkw}'; bound turn budgets instead",
            ))

    return SkillAudit(
        name=name,
        word_count=total_words,
        line_count=total_lines,
        prescriptive_words=prescriptive_count,
        explanatory_words=explanatory_count,
        prescription_ratio=ratio,
        altitude=altitude,
        diagnostics=diagnostics,
    )


def audit_context(context: RunContext) -> list[SkillAudit]:
    audits = []
    for record in context.of_type("skill"):
        audits.append(audit_skill(record.source_path, record.name))
    return audits
