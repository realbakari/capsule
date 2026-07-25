"""Capsule skill health and calibration analyzer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence
from .schema import SourceRecord


@dataclass
class HealthFinding:
    check: str
    severity: str
    detail: str
    evidence: str = ""


@dataclass
class HealthReport:
    record: SourceRecord
    policy_directives: int = 0
    behavioral_directives: int = 0
    altitude: str = "right"
    findings: list[HealthFinding] = field(default_factory=list)


def reasoning_extraction_risk(body: str) -> list[HealthFinding]:
    if "explain your reasoning in your response" in body.lower():
        return [HealthFinding("reasoning-extraction", "high", "Instruction to output internal reasoning", "matches directive")]
    return []


def classifier_domain_risk(body: str) -> list[HealthFinding]:
    findings = []
    if "exploit development" in body.lower() and "must not contain" not in body.lower():
        findings.append(HealthFinding("offensive-cyber", "high", "Offensive cyber domain text"))
    if "pcr reaction" in body.lower():
        findings.append(HealthFinding("life-sciences", "medium", "Life sciences domain text"))
    return findings


def conflicting_directives(body: str) -> list[HealthFinding]:
    lower = body.lower()
    c1 = "avoid comments" if "avoid comments" in lower else ("never add comments" if "never add comments" in lower else None)
    c2 = "document the public api" if "document the public api" in lower else None
    if c1 and c2:
        pos1 = lower.find(c1)
        pos2 = lower.find(c2)
        dist = abs(pos2 - pos1)
        if dist < 500:
            return [HealthFinding("conflict", "medium", "Conflicting directives", f"{dist} chars apart")]
        else:
            return [HealthFinding("conflict", "low", "Conflicting directives (scoped)", f"{dist} chars apart")]
    return []


def progressive_disclosure(body: str, aux_files: int = 0) -> list[HealthFinding]:
    word_count = len(body.split())
    if word_count > 2000:
        if aux_files == 0:
            return [HealthFinding("progressive-disclosure", "medium", f"{word_count}w in single file without supporting files")]
        else:
            return [HealthFinding("progressive-disclosure", "low", f"{word_count}w in single file with supporting files")]
    return []


def example_density(body: str) -> list[HealthFinding]:
    fence_count = body.count("```")
    if fence_count >= 24:
        return [HealthFinding("example-density", "medium", "Excessive example density")]
    return []


def analyze(record: SourceRecord, body: str = "", aux_files: int = 0) -> HealthReport:
    policy_keywords = ["never reconstruct", "never load", "trust gate", "security policy"]
    policy_directives = sum(body.lower().count(k) for k in policy_keywords)

    behavioral_keywords = ["never use", "always start", "do not write", "must use", "always end", "must keep", "always cite"]
    behavioral_directives = sum(body.lower().count(k) for k in behavioral_keywords)

    findings = []
    findings.extend(reasoning_extraction_risk(body))
    findings.extend(classifier_domain_risk(body))
    findings.extend(conflicting_directives(body))
    findings.extend(progressive_disclosure(body, aux_files))
    findings.extend(example_density(body))

    altitude = "right"
    if behavioral_directives >= 6:
        altitude = "brittle"
    elif behavioral_directives >= 3:
        altitude = "firm"

    return HealthReport(
        record=record,
        policy_directives=policy_directives,
        behavioral_directives=behavioral_directives,
        altitude=altitude,
        findings=findings,
    )


def summarize(reports: Sequence[HealthReport]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0, "clean": 0}
    for r in reports:
        if not r.findings:
            counts["clean"] += 1
        else:
            severities = {f.severity for f in r.findings}
            if "high" in severities:
                counts["high"] += 1
            elif "medium" in severities:
                counts["medium"] += 1
            else:
                counts["low"] += 1
    return counts
