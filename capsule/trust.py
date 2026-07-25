"""Trust: aggregate multi-provider security audits into one verdict.

Grounded in what the skills.sh audit table actually shows, not in what a clean
model would predict:

  - `find-skills` is the single most-installed skill in the ecosystem (2.6M) and
    carries a Snyk *Medium* risk. Popularity is not safety.
  - `azure-validate` is rated Safe by Gen and 0-alerts by Socket while Snyk rates
    it **Critical**. Providers disagree, and the disagreement is not noise.
  - `azure-resource-visualizer` is High risk and comes from Microsoft, a curated
    first-party source. Source reputation is not safety either.
  - Two dozen `lark-*` skills are Pending on all three providers. Pending is not
    a pass.

So aggregation takes the **worst** verdict any provider reports. Averaging or
majority-voting would have cleared azure-validate on a 2-1 vote, which is
exactly the failure this module exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Normalized statuses used by the registry.
STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_PENDING = "pending"
STATUS_UNKNOWN = "unknown"

# Severity ladder. Higher wins when providers disagree.
_STATUS_RANK = {
    STATUS_PASS: 0,
    STATUS_UNKNOWN: 1,
    STATUS_PENDING: 1,
    STATUS_WARN: 2,
    STATUS_FAIL: 3,
}

_RISK_RANK = {
    "NONE": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "MED": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}

# Verdict -> what Capsule is allowed to do with the skill.
VERDICT_ALLOW = "allow"
VERDICT_APPROVAL = "approval-required"
VERDICT_DENY = "deny"


# Providers the registry aggregates. Five, not the three this module was first
# written against -- Runlayer and ZeroLeaks were added later. The list is
# informational: aggregation takes the worst verdict from whatever arrives, so
# a new provider is handled without changing this constant. It exists so
# `providers` in a verdict can be read against what was expected.
KNOWN_PROVIDERS = (
    "Gen Agent Trust Hub",
    "Socket",
    "Snyk",
    "Runlayer",
    "ZeroLeaks",
)


@dataclass
class ProviderAudit:
    provider: str
    status: str = STATUS_UNKNOWN
    risk_level: str = ""
    summary: str = ""
    audited_at: str = ""
    # URL-safe partner slug, used to link to the per-provider detail page.
    slug: str = ""
    # Only Agent Trust Hub reports these, e.g. ["NO_CODE", "SAFE"].
    categories: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> "ProviderAudit":
        raw_categories = data.get("categories") or []
        return cls(
            provider=str(data.get("provider", "unknown")),
            status=str(data.get("status", STATUS_UNKNOWN)).lower(),
            risk_level=str(data.get("riskLevel", "") or "").upper(),
            summary=str(data.get("summary", "")),
            audited_at=str(data.get("auditedAt", "")),
            slug=str(data.get("slug", "") or ""),
            categories=[str(c) for c in raw_categories] if isinstance(raw_categories, list) else [],
        )

    def rank(self) -> tuple[int, int]:
        return (_STATUS_RANK.get(self.status, 1), _RISK_RANK.get(self.risk_level, 0))


@dataclass
class TrustVerdict:
    verdict: str
    status: str
    risk_level: str
    reason: str
    providers: list[str] = field(default_factory=list)
    dissenting: bool = False

    def line(self) -> str:
        flag = " [providers disagree]" if self.dissenting else ""
        return f"{self.verdict} (status={self.status}, risk={self.risk_level or 'n/a'}){flag}: {self.reason}"


def aggregate(audits: list[ProviderAudit]) -> TrustVerdict:
    """Collapse provider audits to a single verdict, worst-case wins."""
    if not audits:
        return TrustVerdict(
            VERDICT_DENY, STATUS_UNKNOWN, "",
            "no provider has audited this skill; unknown is not safe",
        )

    worst = max(audits, key=lambda a: a.rank())

    # Dissent means providers actually disagree -- not that one of them omits an
    # optional field. Socket reports no riskLevel at all, so folding a missing
    # risk into the comparison flagged unanimous passes as disagreements.
    status_ranks = {_STATUS_RANK.get(a.status, 1) for a in audits}
    risk_ranks = {_RISK_RANK.get(a.risk_level, 0) for a in audits if a.risk_level}
    dissenting = len(status_ranks) > 1 or len(risk_ranks) > 1
    names = [a.provider for a in audits]

    status, risk = worst.status, worst.risk_level
    risk_rank = _RISK_RANK.get(risk, 0)

    if status == STATUS_FAIL or risk_rank >= _RISK_RANK["HIGH"]:
        verdict = VERDICT_DENY
        reason = f"{worst.provider} reports {status}/{risk or 'n/a'}"
    elif status in (STATUS_PENDING, STATUS_UNKNOWN):
        verdict = VERDICT_DENY
        reason = f"{worst.provider} audit is {status}; pending is not a pass"
    elif status == STATUS_WARN or risk_rank == _RISK_RANK["MEDIUM"]:
        verdict = VERDICT_APPROVAL
        reason = f"{worst.provider} reports {status}/{risk or 'n/a'}; review before loading"
    else:
        verdict = VERDICT_ALLOW
        reason = f"all {len(audits)} provider(s) clear"

    if dissenting and verdict != VERDICT_ALLOW:
        reason += "; verdict taken from the most severe provider, not a majority vote"

    return TrustVerdict(verdict, status, risk, reason, names, dissenting)


def aggregate_api(payload: dict) -> TrustVerdict:
    """Aggregate directly from a /api/v1/skills/audit/... response body."""
    return aggregate([ProviderAudit.from_api(a) for a in payload.get("audits", [])])
