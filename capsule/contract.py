"""Obligation contracts: enforcing a skill without relying on the agent reading it.

The problem this solves is the one that survives every other control in Capsule.
You select the right pack, the host loads it, and the agent still edits the
codebase without following it. Routing is deterministic; *adherence* is not. An
instruction in a skill body is a hint the model may or may not act on, and no
amount of better selection fixes that.

The move here is to stop trying. Instead of asking the model to comply, extract
the skill's checkable commitments into a **contract** and verify the resulting
**diff** against it. If the change violates the contract, it fails — whether the
agent read the skill, skimmed it, or ignored it entirely becomes irrelevant,
because compliance is now a property of the artifact rather than of the model's
attention.

This mirrors what the field reports converge on: fresh-context verification
outperforms self-critique, and a dedicated reviewer catches what the implementer
missed while focused on the feature.

**What separates checkable from unenforceable.** A directive containing a
code-like token is mechanically verifiable:

    "Never use `\\n` -- use separate Paragraph elements"     -> checkable
    "never insert `•` literally"                            -> checkable
    "do not run `npm install` first"                         -> checkable
    "Write code that reads like the surrounding code"        -> not checkable

Taste is not checkable and this module does not pretend otherwise. It reports
**coverage**: the fraction of a skill's directives it can actually enforce. A
contract with 30% coverage is honest; a tool claiming to enforce the other 70%
would not be.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .schema import SourceRecord

# --- obligation kinds ---------------------------------------------------------

FORBID = "forbid"            # token must not appear in added lines
REQUIRE = "require"          # token must appear somewhere in the change
PLACEMENT = "placement"      # artifacts belong under a given path
ADVISORY = "advisory"        # real directive, not mechanically checkable

SEVERITY_ORDER = {"violation": 0, "unmet": 1, "satisfied": 2, "n/a": 3}


@dataclass
class Obligation:
    id: str
    kind: str                # FORBID | REQUIRE | PLACEMENT | ADVISORY
    directive: str           # raw sentence from SKILL.md
    token: str = ""          # code token extracted, if checkable
    path_pattern: str = ""   # target file pattern for PLACEMENT

    @property
    def checkable(self) -> bool:
        return self.kind != ADVISORY

    def line(self) -> str:
        """One-line form used when listing what a contract will enforce."""
        verb = {
            FORBID: "must not use",
            REQUIRE: "must use",
            PLACEMENT: "must write under",
        }.get(self.kind, "advisory")
        target = self.path_pattern if self.kind == PLACEMENT else self.token
        return f"{self.id:<14} {verb} `{target}`"


@dataclass
class Contract:
    skill_name: str
    obligations: list[Obligation] = field(default_factory=list)

    @property
    def checkable(self) -> list[Obligation]:
        return [o for o in self.obligations if o.checkable]

    @property
    def advisory(self) -> list[Obligation]:
        return [o for o in self.obligations if o.kind == ADVISORY]

    @property
    def coverage(self) -> float:
        if not self.obligations:
            return 0.0
        return len(self.checkable) / len(self.obligations)

    def summary(self) -> str:
        """Header for `capsule contract`.

        Coverage is stated up front rather than buried, because the number is
        usually low and an author who does not see it will assume the whole
        skill is enforced.
        """
        checkable, advisory = len(self.checkable), len(self.advisory)
        pct = int(round(self.coverage * 100))
        return (
            f"contract for {self.skill_name}: "
            f"{checkable} enforceable, {advisory} advisory "
            f"({pct}% coverage — the rest is taste and is not checked)"
        )


@dataclass
class Location:
    path: str
    line_number: int
    content: str


@dataclass
class ObligationResult:
    obligation: Obligation
    status: str              # violation | unmet | satisfied | n/a
    detail: str = ""
    locations: list[Location] = field(default_factory=list)

    def line(self) -> str:
        where = ""
        if self.locations:
            loc = self.locations[0]
            where = f" at {loc.path}:{loc.line_number}"
        mark = {"violation": "FAIL", "unmet": "WARN", "satisfied": "PASS", "n/a": "----"}[self.status]
        return f"{mark} {self.obligation.id}: {self.detail}{where}"


@dataclass
class VerificationReport:
    contract: Contract
    results: list[ObligationResult] = field(default_factory=list)

    @property
    def adherent(self) -> bool:
        return not any(r.status == "violation" for r in self.results)

    @property
    def satisfied(self) -> bool:
        return any(r.status == "satisfied" for r in self.results)

    @property
    def violations(self) -> list[ObligationResult]:
        return [r for r in self.results if r.status == "violation"]

    def report(self) -> str:
        lines = []
        for r in sorted(self.results, key=lambda x: SEVERITY_ORDER.get(x.status, 9)):
            if r.status in ("violation", "unmet", "satisfied"):
                lines.append(r.line())

        v_count = len(self.violations)
        s_count = len([r for r in self.results if r.status == "satisfied"])
        applicable = len([r for r in self.results if r.status != "n/a"])

        lines.append(f"{v_count} violation(s), {s_count} satisfied, {applicable} of {len(self.results)} obligations applicable")

        cov_pct = int(round(self.contract.coverage * 100))
        adv_count = len(self.contract.advisory)
        lines.append(f"contract coverage: {cov_pct}% ({adv_count} directive(s) are advisory and cannot be verified)")

        return "\n".join(lines)


@dataclass
class Changeset:
    paths: list[str]
    added_lines: list[tuple[str, str]]

    def is_empty(self) -> bool:
        return not self.added_lines

    @classmethod
    def from_diff(cls, diff_text: str) -> Changeset:
        paths = []
        added = []
        current_path = "unknown"

        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                current_path = line[6:]
                if current_path not in paths:
                    paths.append(current_path)
            elif line.startswith("+") and not line.startswith("+++"):
                added.append((current_path, line[1:]))

        return cls(paths=paths, added_lines=added)

    @classmethod
    def from_git(cls, repo: str | Path = ".", ref: Optional[str] = None) -> Changeset:
        """Read a diff straight out of a git working tree.

        This is the path that makes `verify` usable as a pre-commit hook or a
        CI gate: `--ref=--cached` checks what is staged, `--ref main` checks a
        branch against its base. Failures raise `RuntimeError` so the caller
        can distinguish "could not read the change" from "the change is bad" --
        reporting an unreadable diff as adherent would be the worst outcome.
        """
        command = ["git", "-C", str(repo), "diff", "--no-color"]
        if ref:
            command.append(ref)

        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"could not run git: {exc}") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise RuntimeError(detail[0] if detail else f"git exited {result.returncode}")

        return cls.from_diff(result.stdout)

    @classmethod
    def from_paths(cls, file_paths: Sequence[str | Path], root: Optional[Path] = None) -> Changeset:
        paths = []
        added = []
        root_path = root.resolve() if root else Path.cwd()

        for p in file_paths:
            path_obj = Path(p).resolve()
            try:
                rel = str(path_obj.relative_to(root_path))
            except ValueError:
                rel = str(path_obj)
            paths.append(rel)
            if path_obj.exists():
                for idx, line in enumerate(path_obj.read_text(errors="replace").splitlines(), start=1):
                    added.append((rel, line))

        return cls(paths=paths, added_lines=added)


def extract_contract(record: Optional[SourceRecord], body: str, skill_name: str) -> Contract:
    obligations = []
    lines = body.splitlines()

    in_fence = False
    idx = 0
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        s = line.strip()
        if not s or s.startswith("#"):
            continue

        clauses = re.split(r";|\s+—\s+|\s+--\s+", s)

        for clause in clauses:
            cl_lower = clause.lower()
            prohib_match = re.search(r"\b(never|do not|don't|must not|prohibit)\b", cl_lower)
            req_match = re.search(r"\b(always|must use|require)\b", cl_lower)

            if prohib_match:
                after_text = clause[prohib_match.start():]
                tokens = re.findall(r"`([^`]+)`", after_text)
                if tokens:
                    for tok in tokens:
                        idx += 1
                        obligations.append(Obligation(
                            id=f"{skill_name}-{idx}",
                            kind=FORBID,
                            directive=s,
                            token=tok,
                        ))
                else:
                    idx += 1
                    obligations.append(Obligation(
                        id=f"{skill_name}-{idx}",
                        kind=ADVISORY,
                        directive=s,
                    ))
            elif req_match:
                after_text = clause[req_match.start():]
                tokens = re.findall(r"`([^`]+)`", after_text)
                if tokens:
                    for tok in tokens:
                        idx += 1
                        obligations.append(Obligation(
                            id=f"{skill_name}-{idx}",
                            kind=REQUIRE,
                            directive=s,
                            token=tok,
                        ))
                else:
                    idx += 1
                    obligations.append(Obligation(
                        id=f"{skill_name}-{idx}",
                        kind=ADVISORY,
                        directive=s,
                    ))

    return Contract(skill_name=skill_name, obligations=obligations)


def contract_for_skill(record: SourceRecord) -> Contract:
    skill_md = Path(record.source_path) / "SKILL.md"
    body = skill_md.read_text(errors="replace") if skill_md.exists() else f"Skill {record.name}"
    return extract_contract(record, body, record.name)


def verify(contract: Contract, changeset: Changeset) -> VerificationReport:
    results = []

    for ob in contract.obligations:
        if ob.kind == ADVISORY:
            results.append(ObligationResult(ob, "n/a", "advisory directive"))
            continue

        if ob.kind == FORBID:
            violations = []
            escaped = re.escape(ob.token)
            if re.match(r"^\w+$", ob.token):
                pattern = re.compile(rf"\b{escaped}\b")
            else:
                pattern = re.compile(rf"{escaped}")

            for path, line in changeset.added_lines:
                if pattern.search(line):
                    violations.append(Location(path, 1, line))
            if violations:
                results.append(ObligationResult(ob, "violation", f"introduces `{ob.token}`", locations=violations))
            else:
                results.append(ObligationResult(ob, "satisfied", f"no `{ob.token}` introduced"))

        elif ob.kind == REQUIRE:
            found = False
            escaped = re.escape(ob.token)
            if re.match(r"^\w+$", ob.token):
                pattern = re.compile(rf"\b{escaped}\b")
            else:
                pattern = re.compile(rf"{escaped}")

            for path, line in changeset.added_lines:
                if pattern.search(line):
                    found = True
                    break
            if found:
                results.append(ObligationResult(ob, "satisfied", f"found `{ob.token}`"))
            else:
                results.append(ObligationResult(ob, "unmet", f"missing `{ob.token}`"))

    return VerificationReport(contract=contract, results=results)


def brief(record: SourceRecord, contract: Contract, context: str = "") -> str:
    lines = [
        f"Selected Skill: {record.name}",
        f"Source: {record.source_path}/SKILL.md",
        f"Context: {context}",
        "Enforceable obligations:",
    ]
    for ob in contract.checkable:
        if ob.kind == FORBID:
            lines.append(f"  - must not use `{ob.token}`")
        elif ob.kind == REQUIRE:
            lines.append(f"  - must use `{ob.token}`")

    cov_pct = int(round(contract.coverage * 100))
    lines.append(f"Contract coverage: {cov_pct}% verified")
    return "\n".join(lines)
