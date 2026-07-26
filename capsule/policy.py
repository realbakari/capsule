"""Capsule policy engine.

Deny by default. Every meaningful decision is logged with a reason so a run can
be replayed and audited. The license gate is the load-bearing rule here: Capsule
will index anything it can read, but it will only *reconstruct* sources whose
license permits derivative works.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from .rules import ACTION_APPROVAL, ACTION_DENY, RuleSet
from .schema import (
    LICENSE_APACHE,
    LICENSE_RESTRICTED,
    LICENSE_UNKNOWN,
    SourceRecord,
)

# Markers used to classify a LICENSE.txt without parsing legalese.
_APACHE_MARKERS = ("Apache License", "www.apache.org/licenses")
_RESTRICTED_MARKERS = (
    "ADDITIONAL RESTRICTIONS",
    "may not:",
    "Create derivative works",
)

# Actions Capsule knows about. Anything not listed is denied outright.
READ_ACTIONS = {"index", "read", "route", "validate", "audit"}
WRITE_ACTIONS = {"reconstruct", "package", "write"}
RISKY_ACTIONS = {"delete", "overwrite", "network", "exec"}


@dataclass
class Decision:
    action: str
    subject: str
    allowed: bool
    reason: str
    requires_approval: bool = False
    at: float = 0.0

    def line(self) -> str:
        verdict = "ALLOW" if self.allowed else "DENY "
        flag = " (approval required)" if self.requires_approval else ""
        return f"{verdict} {self.action}:{self.subject} -- {self.reason}{flag}"


class PolicyError(RuntimeError):
    """Raised when a denied action is attempted anyway."""


class Policy:
    """Evaluates actions against Capsule's constraints and records an audit log."""

    def __init__(
        self,
        writable_roots: list[str] | None = None,
        readonly_roots: list[str] | None = None,
        allow_restricted_reconstruction: bool = False,
        allow_unaudited_registry_skills: bool = False,
        ruleset: RuleSet | None = None,
    ) -> None:
        self.writable_roots = [
            str(Path(p).resolve()) for p in (writable_roots or ["./outputs", "./packs", "./out"])
        ]
        self.readonly_roots = [
            str(Path(p).resolve()) for p in (readonly_roots or ["./skills", "./docs"])
        ]
        # Escape hatch for operators who hold rights Capsule cannot verify.
        # Off by default; flipping it is itself an audited decision.
        self.allow_restricted_reconstruction = allow_restricted_reconstruction
        # Lets a warn/medium-risk registry skill load. Never clears a fail,
        # a HIGH/CRITICAL risk, or a pending audit -- those stay denied.
        self.allow_unaudited_registry_skills = allow_unaudited_registry_skills
        self.ruleset = ruleset or RuleSet()
        self.log: list[Decision] = []

    def add_rule(self, fn) -> None:
        """Register a programmatic rule. Explicit import only -- never by path."""
        self.ruleset.add_programmatic(fn)

    # -- license classification ------------------------------------------------

    @staticmethod
    def classify_license(skill_dir: Path) -> str:
        license_file = skill_dir / "LICENSE.txt"
        if not license_file.exists():
            return LICENSE_UNKNOWN
        try:
            text = license_file.read_text(errors="replace")
        except OSError:
            return LICENSE_UNKNOWN
        if any(m in text for m in _RESTRICTED_MARKERS):
            return LICENSE_RESTRICTED
        if any(m in text for m in _APACHE_MARKERS):
            return LICENSE_APACHE
        return LICENSE_UNKNOWN

    # -- gates -----------------------------------------------------------------

    def _record(self, decision: Decision) -> Decision:
        decision.at = time.time()
        self.log.append(decision)
        return decision

    def can_reconstruct(self, record: SourceRecord) -> Decision:
        """The license gate. Deny unless the license affirmatively permits it."""
        if record.license_class == LICENSE_APACHE:
            return self._record(
                Decision(
                    "reconstruct",
                    record.name,
                    True,
                    "Apache-2.0 permits derivative works; attribution carried forward",
                )
            )
        if record.license_class == LICENSE_RESTRICTED:
            if self.allow_restricted_reconstruction:
                return self._record(
                    Decision(
                        "reconstruct",
                        record.name,
                        True,
                        "restricted license overridden by operator assertion of rights",
                        requires_approval=True,
                    )
                )
            return self._record(
                Decision(
                    "reconstruct",
                    record.name,
                    False,
                    "license forbids extraction and derivative works",
                )
            )
        return self._record(
            Decision(
                "reconstruct",
                record.name,
                False,
                "license could not be determined; deny by default",
            )
        )

    def can_load(self, record: SourceRecord) -> Decision:
        """Trust gate for registry skills.

        Distinct from the license gate: licensing governs whether Capsule may
        *rebuild* a skill, trust governs whether it may *run* one. A remote skill
        can be freely licensed and still hostile.

        Local sources bypass this gate — they arrived with the workspace and are
        governed by the license and path gates instead.
        """
        if record.source_type != "registry":
            return self._record(
                Decision("load", record.name, True, "local source; trust gate does not apply")
            )

        if record.trust_verdict == "allow":
            if record.is_duplicate:
                return self._record(
                    Decision(
                        "load", record.name, False,
                        "flagged as a fork or copy of another skill; prefer the original",
                        requires_approval=True,
                    )
                )
            return self._record(
                Decision("load", record.name, True, record.trust_reason or "audits clear")
            )

        if record.trust_verdict == "approval-required":
            if self.allow_unaudited_registry_skills:
                return self._record(
                    Decision("load", record.name, True,
                             f"operator override: {record.trust_reason}", requires_approval=True)
                )
            return self._record(
                Decision("load", record.name, False, record.trust_reason or "audit warns")
            )

        return self._record(
            Decision(
                "load", record.name, False,
                record.trust_reason or "no clearing audit; deny by default",
            )
        )

    def can_write(self, path: str | Path) -> Decision:
        resolved = str(Path(path).resolve())
        for root in self.readonly_roots:
            if resolved == root or resolved.startswith(root + "/"):
                return self._record(
                    Decision("write", resolved, False, f"path is under read-only root {root}")
                )
        for root in self.writable_roots:
            if resolved == root or resolved.startswith(root + "/"):
                return self._record(
                    Decision("write", resolved, True, f"path is under writable root {root}")
                )
        return self._record(
            Decision("write", resolved, False, "path is outside every declared writable root")
        )

    def can_read(self, path: str | Path) -> Decision:
        resolved = Path(path)
        if not resolved.exists():
            return self._record(Decision("read", str(resolved), False, "path does not exist"))
        return self._record(Decision("read", str(resolved), True, "read is permitted within mounted roots"))

    def check_action(self, action: str, subject: str = "") -> Decision:
        if action in READ_ACTIONS:
            return self._record(Decision(action, subject, True, "read-class action"))
        if action in WRITE_ACTIONS:
            return self._record(
                Decision(action, subject, True, "write-class action, gated per-path")
            )
        if action in RISKY_ACTIONS:
            return self._record(
                Decision(action, subject, False, "destructive or escaping action", requires_approval=True)
            )
        return self._record(Decision(action, subject, False, "unknown action; deny by default"))

    def apply_rules(self, record: SourceRecord, body: str = "") -> Decision:
        """Evaluate user-defined rules against a record.

        Rules escalate only. A rule can turn an allowed record into a denied one;
        nothing here can clear a denial issued by a built-in gate. Loosening is
        the job of the explicit override flags, which are audited one by one.
        """
        hits = self.ruleset.evaluate(record, body)
        verdict = self.ruleset.verdict(hits)
        if verdict is None:
            return self._record(
                Decision("rules", record.name, True, "no custom rule matched")
            )

        detail = "; ".join(h.line() for h in hits[:3])
        if verdict == ACTION_DENY:
            return self._record(Decision("rules", record.name, False, detail))
        if verdict == ACTION_APPROVAL:
            return self._record(
                Decision("rules", record.name, False, detail, requires_approval=True)
            )
        return self._record(Decision("rules", record.name, True, f"flagged only: {detail}"))

    def enforce(self, decision: Decision) -> None:
        if not decision.allowed:
            raise PolicyError(decision.line())

    # -- audit -----------------------------------------------------------------

    def audit_text(self) -> str:
        return "\n".join(d.line() for d in self.log)

    def audit_json(self) -> str:
        return json.dumps([asdict(d) for d in self.log], indent=2)
