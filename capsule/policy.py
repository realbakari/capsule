"""Capsule policy engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any, Optional

from .schema import SourceRecord
from .rules import RuleSet, default_ruleset, ACTION_DENY, ACTION_APPROVAL


class PolicyError(Exception):
    pass


@dataclass
class PolicyDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str = ""


class Policy:
    def __init__(
        self,
        writable_roots: Optional[list[str]] = None,
        readonly_roots: Optional[list[str]] = None,
        allow_restricted_reconstruction: bool = False,
        allow_unaudited_registry_skills: bool = False,
        ruleset: Optional[RuleSet] = None,
    ):
        self.writable_roots = writable_roots or []
        self.readonly_roots = readonly_roots or ["/mnt/skills/public", "/mnt/skills/examples"]
        self.allow_restricted_reconstruction = allow_restricted_reconstruction
        self.allow_unaudited_registry_skills = allow_unaudited_registry_skills
        self.ruleset = ruleset if ruleset is not None else default_ruleset()
        self._audit_log: list[str] = []
        self._programmatic_rules: list[Callable[[SourceRecord, str], Any]] = []

    def add_rule(self, fn: Callable[[SourceRecord, str], Any]):
        self._programmatic_rules.append(fn)

    def can_reconstruct(self, record: SourceRecord) -> PolicyDecision:
        if record.reconstructable or record.license_class == "apache-2.0":
            return PolicyDecision(allowed=True)
        if record.license_class == "proprietary-restricted":
            if self.allow_restricted_reconstruction:
                self._audit_log.append(f"reconstruct:{record.name}")
                return PolicyDecision(allowed=True, requires_approval=True, reason="restricted override approved")
            return PolicyDecision(allowed=False, reason="restricted license")
        if self.allow_restricted_reconstruction:
            self._audit_log.append(f"reconstruct:{record.name}")
            return PolicyDecision(allowed=True, requires_approval=True, reason="license override approved")
        return PolicyDecision(allowed=False, reason="unknown or restricted license")

    def can_write(self, target_path: str | Path) -> PolicyDecision:
        path_str = str(Path(target_path).resolve())
        target_orig = str(target_path)

        for r in self.readonly_roots:
            if path_str.startswith(str(Path(r).resolve())):
                return PolicyDecision(allowed=False, reason=f"readonly path: {r}")

        if self.writable_roots:
            allowed = any(path_str.startswith(str(Path(w).resolve())) for w in self.writable_roots)
            if not allowed:
                return PolicyDecision(allowed=False, reason=f"outside writable roots: {path_str}")
            return PolicyDecision(allowed=True)

        if target_orig.startswith("/etc") or target_orig.startswith("/var") or path_str.startswith("/etc"):
            return PolicyDecision(allowed=False, reason="system path disallowed")

        return PolicyDecision(allowed=True)

    def can_load(self, record: SourceRecord) -> PolicyDecision:
        if getattr(record, "isDuplicate", False):
            return PolicyDecision(allowed=False, requires_approval=True, reason="duplicate fork")
        if record.trust_verdict == "deny" or "trust:providers-disagree" in record.policy_constraints:
            return PolicyDecision(allowed=False, reason="critical security finding")
        if record.trust_verdict == "approval-required" or record.trust_verdict == "medium":
            if self.allow_unaudited_registry_skills:
                return PolicyDecision(allowed=True, requires_approval=True, reason="unaudited override applied")
            return PolicyDecision(allowed=False, requires_approval=True, reason="medium risk requires approval")
        if record.trust_verdict == "pending":
            if self.allow_unaudited_registry_skills:
                return PolicyDecision(allowed=True, requires_approval=True, reason="pending override applied")
            return PolicyDecision(allowed=False, reason="pending audit")
        return PolicyDecision(allowed=True)

    def check_action(self, action: str, target: str = "") -> PolicyDecision:
        valid_actions = {"write", "read", "delete", "reconstruct"}
        if action not in valid_actions:
            return PolicyDecision(allowed=False, reason=f"unknown action: {action}")
        if action == "delete":
            return PolicyDecision(allowed=False, requires_approval=True, reason="risky action delete")
        return PolicyDecision(allowed=True)

    def enforce(self, decision: PolicyDecision):
        if not decision.allowed:
            raise PolicyError(decision.reason or "Policy denial")

    def audit_text(self) -> str:
        return "\n".join(self._audit_log)

    def apply_rules(self, record: SourceRecord, body: str = "") -> PolicyDecision:
        for pfn in self._programmatic_rules:
            try:
                hit = pfn(record, body)
                if hit:
                    action = getattr(hit, "action", ACTION_DENY)
                    reason = getattr(hit, "reason", "rule hit")
                    if action == ACTION_DENY:
                        return PolicyDecision(allowed=False, reason=reason)
                    elif action == ACTION_APPROVAL:
                        return PolicyDecision(allowed=False, requires_approval=True, reason=reason)
            except Exception as exc:
                return PolicyDecision(allowed=False, reason=f"failing closed due to rule exception: {exc}")

        hits = self.ruleset.evaluate(record, body)
        verdict = self.ruleset.verdict(hits)
        if verdict == ACTION_DENY:
            reasons = "; ".join(h.reason for h in hits)
            return PolicyDecision(allowed=False, reason=reasons)
        elif verdict == ACTION_APPROVAL:
            reasons = "; ".join(h.reason for h in hits)
            return PolicyDecision(allowed=False, requires_approval=True, reason=reasons)
        return PolicyDecision(allowed=True)
