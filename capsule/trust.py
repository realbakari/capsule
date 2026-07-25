"""Capsule trust aggregation module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

VERDICT_ALLOW = "allow"
VERDICT_APPROVAL = "approval-required"
VERDICT_DENY = "deny"


@dataclass
class ProviderAudit:
    provider: str
    status: str = "pass"       # pass | fail | warn | pending
    severity: str = "LOW"      # CRITICAL | HIGH | MEDIUM | LOW | ""
    details: str = ""

    def __init__(self, provider: str, status: str = "pass", severity: str = "LOW", details: str = ""):
        self.provider = provider
        self.status = status
        self.severity = severity
        self.details = details


@dataclass
class TrustVerdict:
    verdict: str
    reasons: list[str] = field(default_factory=list)
    dissenting: bool = False

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)

    @property
    def allowed(self) -> bool:
        return self.verdict == VERDICT_ALLOW


def aggregate(audits: Sequence[ProviderAudit | dict[str, Any]]) -> TrustVerdict:
    if not audits:
        return TrustVerdict(verdict=VERDICT_DENY, reasons=["unknown is not safe — no audits available"])

    parsed: list[ProviderAudit] = []
    for a in audits:
        if isinstance(a, dict):
            parsed.append(ProviderAudit(
                provider=a.get("provider", "unknown"),
                status=a.get("status", "fail"),
                severity=a.get("severity", "LOW"),
                details=a.get("details", "")
            ))
        else:
            parsed.append(a)

    statuses = [p.status for p in parsed]
    severities = [p.severity for p in parsed]

    dissenting = len(set(statuses)) > 1 or len(set(severities)) > 1

    if any(p.status == "pending" for p in parsed):
        return TrustVerdict(verdict=VERDICT_DENY, reasons=["pending is not a pass"], dissenting=dissenting)

    if any(p.severity == "CRITICAL" for p in parsed) or any(p.status == "fail" for p in parsed):
        dissent_note = " — not a majority vote" if dissenting else ""
        bad_providers = [p.provider for p in parsed if p.severity in ("CRITICAL", "HIGH") or p.status == "fail"]
        prov_str = ", ".join(bad_providers)
        return TrustVerdict(verdict=VERDICT_DENY, reasons=[f"critical finding in {prov_str}{dissent_note}"], dissenting=dissenting)

    if any(p.severity == "HIGH" for p in parsed):
        return TrustVerdict(verdict=VERDICT_DENY, reasons=["high risk finding"], dissenting=dissenting)

    if any(p.severity == "MEDIUM" for p in parsed) or any(p.status == "warn" for p in parsed):
        return TrustVerdict(verdict=VERDICT_APPROVAL, reasons=["medium risk finding requiring approval"], dissenting=dissenting)

    return TrustVerdict(verdict=VERDICT_ALLOW, reasons=["all audits clear"], dissenting=False)
