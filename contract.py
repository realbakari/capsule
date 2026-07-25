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

# Directive openers, split by polarity.
_PROHIBITIVE = r"(?:NEVER|Never|never|DO NOT|Do not|do not|DON'T|Don't|don't|avoid|Avoid)"
_MANDATORY = r"(?:ALWAYS|Always|always|MUST|Must|must|REQUIRED|Required)"

# A code-like token: backticked, dotted identifier, path, or file with extension.
_CODE_TOKEN = re.compile(
    r"`([^`\n]{1,60})`"
    r"|(?<![\w.])([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]{2,})(?![\w])"
    r"|((?:[\w.-]+/){1,}[\w.-]+)"
)

# Tokens too generic to enforce. Matching on these produces noise, not signal.
_TOKEN_STOPLIST = {
    "the", "this", "that", "it", "them", "one", "a", "an", "and", "or", "not",
    "true", "false", "none", "null", "yes", "no", "n/a", "e.g.", "i.e.",
}


@dataclass
class Obligation:
    id: str
    kind: str
    directive: str
    token: str = ""
    skill: str = ""
    trigger: str = ""

    @property
    def checkable(self) -> bool:
        return self.kind != ADVISORY

    def line(self) -> str:
        tail = f"  [{self.token}]" if self.token else ""
        return f"{self.id} ({self.kind}): {self.directive[:90]}{tail}"


@dataclass
class Contract:
    skill: str
    obligations: list[Obligation] = field(default_factory=list)

    @property
    def checkable(self) -> list[Obligation]:
        return [o for o in self.obligations if o.checkable]

    @property
    def advisory(self) -> list[Obligation]:
        return [o for o in self.obligations if not o.checkable]

    @property
    def coverage(self) -> float:
        """Fraction of extracted directives that can be mechanically enforced."""
        if not self.obligations:
            return 0.0
        return round(len(self.checkable) / len(self.obligations), 2)

    def summary(self) -> str:
        return (
            f"{self.skill}: {len(self.checkable)} enforceable / "
            f"{len(self.obligations)} directives (coverage {self.coverage:.0%})"
        )


# --- changesets ---------------------------------------------------------------


@dataclass
class Changeset:
    """Added lines and touched paths for a set of changes."""

    added_lines: list[tuple[str, str]] = field(default_factory=list)  # (path, line)
    paths: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def added_text(self) -> str:
        return "\n".join(line for _, line in self.added_lines)

    def is_empty(self) -> bool:
        return not self.added_lines and not self.paths

    @classmethod
    def from_git(cls, repo: str | Path = ".", ref: str | None = None) -> "Changeset":
        """Parse `git diff`. Only added lines count: a rule about what code may
        contain applies to code being introduced, not to context lines around it."""
        cmd = ["git", "-C", str(repo), "diff", "--unified=0", "--no-color"]
        if ref:
            cmd.append(ref)
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"could not run git diff: {exc}") from exc
        if out.returncode != 0:
            raise RuntimeError(f"git diff failed: {out.stderr.strip()[:200]}")
        return cls.from_diff(out.stdout, source=f"git diff {ref or ''}".strip())

    @classmethod
    def from_diff(cls, diff_text: str, source: str = "diff") -> "Changeset":
        changeset = cls(source=source)
        current = ""
        for raw in diff_text.splitlines():
            if raw.startswith("+++ "):
                path = raw[4:].strip()
                current = path[2:] if path.startswith("b/") else path
                if current and current != "/dev/null":
                    changeset.paths.append(current)
            elif raw.startswith("+") and not raw.startswith("+++"):
                changeset.added_lines.append((current, raw[1:]))
        return changeset

    @classmethod
    def from_paths(cls, paths: list[str | Path], root: str | Path = ".") -> "Changeset":
        """Treat whole files as added. For verifying generated output with no VCS."""
        changeset = cls(source="paths")
        for path in paths:
            p = Path(path)
            if not p.is_file():
                continue
            rel = str(p.relative_to(root)) if str(p).startswith(str(root)) else str(p)
            changeset.paths.append(rel)
            try:
                for line in p.read_text(errors="replace").splitlines():
                    changeset.added_lines.append((rel, line))
            except OSError:
                continue
        return changeset


# --- extraction ---------------------------------------------------------------


def _clean_token(match: re.Match) -> tuple[str, bool]:
    """Return (token, was_backticked). Backticks are an explicit author signal."""
    backticked = match.group(1) is not None
    token = next((g for g in match.groups() if g), "")
    return token.strip().strip("`"), backticked


def _usable_tokens(clause: str) -> list[str]:
    """Tokens worth enforcing, from a directive clause.

    Single characters pass only when backticked. `#` and `•` are real obligations
    in this corpus ("never `#`, never 8 digits"), but an unmarked one-character
    match is noise.
    """
    out: list[str] = []
    for match in _CODE_TOKEN.finditer(clause):
        token, backticked = _clean_token(match)
        if not token or token.lower() in _TOKEN_STOPLIST:
            continue
        if len(token) < 2 and not backticked:
            continue
        if token not in out:
            out.append(token)
    return out


def _sentences(body: str) -> list[str]:
    # Strip fenced code so example code isn't mined as directives.
    without_code = re.sub(r"```.*?```", " ", body, flags=re.DOTALL)
    without_code = re.sub(r"^\s*\|.*\|\s*$", " ", without_code, flags=re.MULTILINE)
    parts = re.split(r"(?<=[.!?])\s+|\n(?=[-*#]|\s*\n)", without_code)
    return [p.strip() for p in parts if p.strip()]


def _directive_clause(sentence: str, keyword_end: int) -> str:
    """The span a directive actually governs.

    Critical for correctness. Consider a real line from the docx skill:

        "`docx` is preinstalled — do not run `npm install` first;
         write the script and `require('docx')` directly"

    A naive extractor applies the prohibition to every token in the sentence and
    concludes that `docx` is banned — when the skill is telling you to use it. The
    ban covers only "run `npm install` first", which ends at the semicolon.

    Same shape in "use `ShadingType.CLEAR`, never `SOLID`": the token before the
    keyword is the recommendation, not the prohibition.

    So a directive governs from its keyword to the next clause boundary, and never
    reaches backwards. This is the third time polarity blindness has produced a
    confidently wrong result in this codebase; the fix is always the same, which is
    to bound the window rather than trust co-occurrence.
    """
    tail = sentence[keyword_end:]
    # Boundary words are limited to explicit polarity flips. An earlier version
    # also treated "use" as a boundary, which silently emptied the clause in
    # "Never use `yaml.load`" -- there, "use" is the verb the prohibition governs,
    # not the start of a contrasting clause. Punctuation already separates the
    # recommendation in cases like "do not run X; write Y instead".
    #
    # The period must be followed by whitespace or end-of-string: a bare `[.]`
    # matched the dot inside `yaml.load` and truncated the clause to " use `yaml",
    # losing the very token the rule was about.
    boundary = re.search(
        r"[;]|\.(?=\s|$)|\s+[—–]\s+|\s+--\s+|(?<=\s)(?:but|instead|rather than|whereas|prefer)\s",
        tail,
        re.IGNORECASE,
    )
    return tail[: boundary.start()] if boundary else tail


def extract_contract(record: SourceRecord | None, body: str, skill: str = "") -> Contract:
    """Mine a skill body for obligations.

    Conservative by construction. Only sentences with a directive opener become
    obligations, only the clause the opener governs is searched for tokens, and
    only tokens found there become *enforceable* obligations. Everything else is
    recorded as advisory so coverage stays honest.
    """
    name = skill or (record.name if record else "unknown")
    contract = Contract(skill=name)
    seen: set[tuple[str, str]] = set()
    counter = 0

    for sentence in _sentences(body):
        prohibitive = re.search(rf"\b{_PROHIBITIVE}\b", sentence)
        mandatory = re.search(rf"\b{_MANDATORY}\b", sentence)
        if not (prohibitive or mandatory):
            continue

        # Prohibition wins a mixed sentence: "never X, always Y" is safest read
        # as a ban on X. Enforcing the positive half could contradict the ban.
        opener = prohibitive or mandatory
        kind_hint = FORBID if prohibitive else REQUIRE
        clause = _directive_clause(sentence, opener.end())

        tokens = _usable_tokens(clause)

        directive = re.sub(r"\s+", " ", sentence)[:220]

        if not tokens:
            counter += 1
            contract.obligations.append(Obligation(
                id=f"{name}-adv-{counter}", kind=ADVISORY, directive=directive, skill=name,
            ))
            continue

        # One obligation per distinct token; a clause can ban several things.
        for token in tokens[:3]:
            key = (kind_hint, token)
            if key in seen:
                continue
            seen.add(key)
            counter += 1
            kind = PLACEMENT if (kind_hint == REQUIRE and "/" in token) else kind_hint
            contract.obligations.append(Obligation(
                id=f"{name}-{counter}", kind=kind, directive=directive,
                token=token, skill=name,
            ))

    return contract


# --- verification -------------------------------------------------------------


@dataclass
class Result:
    obligation: Obligation
    status: str  # violation | unmet | satisfied | n/a
    detail: str = ""
    locations: list[str] = field(default_factory=list)

    def line(self) -> str:
        mark = {"violation": "FAIL", "unmet": "WARN", "satisfied": "PASS", "n/a": "----"}[self.status]
        where = f"  ({', '.join(self.locations[:3])})" if self.locations else ""
        return f"{mark} {self.obligation.id}: {self.detail}{where}"


@dataclass
class AdherenceReport:
    skill: str
    changeset_source: str
    results: list[Result] = field(default_factory=list)
    coverage: float = 0.0
    advisory_count: int = 0

    @property
    def violations(self) -> list[Result]:
        return [r for r in self.results if r.status == "violation"]

    @property
    def satisfied(self) -> list[Result]:
        return [r for r in self.results if r.status == "satisfied"]

    @property
    def adherent(self) -> bool:
        return not self.violations

    def report(self) -> str:
        lines = [f"skill: {self.skill}", f"changes: {self.changeset_source}", ""]
        for result in sorted(self.results, key=lambda r: SEVERITY_ORDER[r.status]):
            if result.status != "n/a":
                lines.append(result.line())
        checked = len([r for r in self.results if r.status != "n/a"])
        lines.append("")
        lines.append(
            f"{len(self.violations)} violation(s), {len(self.satisfied)} satisfied, "
            f"{checked} of {len(self.results)} obligations applicable to this change"
        )
        lines.append(
            f"contract coverage: {self.coverage:.0%} "
            f"({self.advisory_count} directive(s) are advisory and cannot be verified)"
        )
        return "\n".join(lines)


def _token_hits(token: str, changeset: Changeset) -> list[str]:
    """Locate a token in added lines.

    The word boundary excludes a preceding word character but *not* a preceding
    dot. `ShadingType.SOLID` must count as a hit on a ban against `SOLID` — the
    prohibited constant is present regardless of how it is qualified. An earlier
    lookbehind of `(?<![\\w.])` treated attribute access as a different token and
    passed the violation as clean.
    """
    if re.fullmatch(r"[\w.]+", token):
        pattern = re.compile(rf"(?<!\w){re.escape(token)}(?!\w)")
    else:
        pattern = re.compile(re.escape(token))
    hits = []
    for path, line in changeset.added_lines:
        if pattern.search(line):
            hits.append(f"{path}: {line.strip()[:60]}")
    return hits


def verify(contract: Contract, changeset: Changeset) -> AdherenceReport:
    """Check a change against a skill's contract."""
    report = AdherenceReport(
        skill=contract.skill,
        changeset_source=changeset.source or "unknown",
        coverage=contract.coverage,
        advisory_count=len(contract.advisory),
    )

    for obligation in contract.checkable:
        hits = _token_hits(obligation.token, changeset)

        if obligation.kind == FORBID:
            if hits:
                report.results.append(Result(
                    obligation, "violation",
                    f"introduces `{obligation.token}`, which the skill prohibits "
                    f"— {obligation.directive[:100]}",
                    hits,
                ))
            else:
                report.results.append(Result(
                    obligation, "satisfied", f"`{obligation.token}` not introduced",
                ))

        elif obligation.kind == PLACEMENT:
            target = obligation.token.rstrip("/")
            relevant = [p for p in changeset.paths if target in p]
            if relevant:
                report.results.append(Result(
                    obligation, "satisfied", f"touches expected location `{target}`", relevant,
                ))
            else:
                report.results.append(Result(obligation, "n/a", f"no files under `{target}`"))

        else:  # REQUIRE
            # A requirement only binds when the change is plausibly in scope.
            # Asserting every "always" rule against an unrelated diff would
            # produce noise that trains people to ignore the report.
            if changeset.is_empty():
                report.results.append(Result(obligation, "n/a", "empty changeset"))
            elif hits:
                report.results.append(Result(
                    obligation, "satisfied", f"`{obligation.token}` present", hits,
                ))
            else:
                report.results.append(Result(
                    obligation, "unmet",
                    f"skill requires `{obligation.token}` but the change does not use it "
                    f"— may not apply here",
                ))

    return report


def contract_for_skill(record: SourceRecord) -> Contract:
    """Build a contract from a skill on disk."""
    skill_md = Path(record.source_path) / "SKILL.md"
    if not skill_md.exists():
        return Contract(skill=record.name)
    return extract_contract(record, skill_md.read_text(errors="replace"))


# --- activation brief ---------------------------------------------------------


def brief(record: SourceRecord, contract: Contract, rationale: str = "") -> str:
    """A compact, injectable activation block.

    Selection is only half the problem: a host that matches skills by description
    may still not surface the right one, which is why people end up naming skills
    by hand. This emits the decision as text to prepend to a turn, so activation
    does not depend on the host's matcher agreeing with Capsule.

    Enforceable obligations are listed explicitly, and the block states that the
    diff will be checked. That is not a threat, it is information the model can
    act on: a stated verification step is far more actionable than a buried rule.
    """
    lines = [
        "<capsule_activation>",
        f"Selected skill pack: {record.name}",
        f"Location: {record.source_path}/SKILL.md",
    ]
    if rationale:
        lines.append(f"Why: {rationale}")
    lines.append("Read this pack in full before editing. It is the governing guidance for this task.")

    if contract.checkable:
        lines.append("")
        lines.append("This change will be verified against the following extracted obligations:")
        for obligation in contract.checkable[:14]:
            verb = {FORBID: "must not use", REQUIRE: "should use", PLACEMENT: "place files under"}[obligation.kind]
            lines.append(f"  - {verb} `{obligation.token}`")
        if len(contract.checkable) > 14:
            lines.append(f"  - ... and {len(contract.checkable) - 14} more")
        lines.append("")
        lines.append(
            f"Verification is mechanical and covers {contract.coverage:.0%} of this pack's "
            "directives. The remainder is judgement and is not checked — follow the pack for those."
        )
    lines.append("</capsule_activation>")
    return "\n".join(lines)
