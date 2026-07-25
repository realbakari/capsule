"""Routing: task -> intent/domain -> shortlist -> full-body rerank -> selection.

The contract requires that candidates be *inspected in full* before one is
selected, not chosen from the index alone. That is why routing is two-stage:
stage one is cheap and index-only, stage two reads bodies for the shortlist and
can overturn the stage-one ordering. Every selection carries its rationale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .policy import Policy
from .schema import RunContext, SourceRecord
from .taxonomy import Taxonomy, mentions

_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "with", "my",
    "me", "i", "please", "can", "you", "help", "make", "create", "this", "that",
    "it", "is", "are", "do", "how", "what", "some", "want", "need", "from",
}



def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9.]+", text.lower()) if t not in _STOPWORDS and len(t) > 1}


# Single implementation, shared with discovery and the taxonomy. Kept under
# the old name because the trap it guards against is worth naming twice:
# "art" is inside "party", "form" is inside "performance".
_mentions = mentions


@dataclass
class Candidate:
    record: SourceRecord
    stage1_score: float = 0.0
    stage2_score: float = 0.0
    evidence: list[str] = field(default_factory=list)
    body_read: bool = False

    @property
    def score(self) -> float:
        return self.stage2_score if self.body_read else self.stage1_score


@dataclass
class Routing:
    task: str
    intent: str
    domain: str
    selected: SourceRecord | None
    rationale: str
    considered: list[Candidate] = field(default_factory=list)
    reranked: bool = False
    confident: bool = True
    blocked: list[str] = field(default_factory=list)
    precedence_applied: list[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [
            f"task     : {self.task}",
            f"intent   : {self.intent}",
            f"domain   : {self.domain}",
            f"selected : {self.selected.name if self.selected else '<none>'}",
            f"rationale: {self.rationale}",
        ]
        if self.considered:
            lines.append("considered:")
            for c in sorted(self.considered, key=lambda x: -x.score)[:5]:
                mark = "*" if self.selected and c.record.name == self.selected.name else " "
                stage = "full-body" if c.body_read else "index-only"
                lines.append(f"  {mark} {c.record.name:<26} {c.score:5.2f}  ({stage})")
                for e in c.evidence[:2]:
                    lines.append(f"      evidence: {e}")
        if self.blocked:
            lines.append("blocked by trust gate:")
            for b in self.blocked[:5]:
                lines.append(f"    - {b}")
        return "\n".join(lines)


def classify(task: str, taxonomy: Taxonomy | None = None) -> tuple[str, str]:
    """Classify a task against the active taxonomy.

    Pass a taxonomy derived from the corpus to get labels that describe the
    workspace rather than the table Capsule shipped with.
    """
    return (taxonomy or Taxonomy()).classify(task)


def _stage1(task: str, record: SourceRecord) -> tuple[float, list[str]]:
    task_tokens = _tokens(task)
    score = 0.0
    evidence: list[str] = []

    for phrase in record.trigger_phrases:
        phrase_l = phrase.lower().strip()
        if not phrase_l:
            continue
        if len(phrase_l) > 3 and phrase_l in task.lower():
            score += 3.0
            evidence.append(f"trigger phrase matched: {phrase_l!r}")
        elif _tokens(phrase_l) & task_tokens:
            score += 0.6

    overlap = task_tokens & _tokens(f"{record.name} {record.purpose}")
    if overlap:
        score += 0.8 * len(overlap)
        evidence.append(f"purpose overlap: {sorted(overlap)[:4]}")

    for shortcut in record.shortcuts:
        if shortcut.lower() in task.lower():
            score += 4.0
            evidence.append(f"shortcut invoked: {shortcut}")

    return score * record.confidence, evidence


def _stage2(task: str, record: SourceRecord) -> tuple[float, list[str]]:
    """Read the candidate's SKILL.md in full and score against the real body."""
    if record.source_type == "registry":
        # No local body to read. Neither reward nor punish -- but never let a
        # remote skill outrank a local one it has not actually out-argued.
        return 0.0, ["remote skill; body not fetched, scored on index only"]

    skill_md = Path(record.source_path) / "SKILL.md"
    if not skill_md.exists():
        return 0.0, ["body unreadable"]
    try:
        body = skill_md.read_text(errors="replace")
    except OSError:
        return 0.0, ["body unreadable"]

    task_tokens = _tokens(task)
    body_lower = body.lower()
    name_tokens = _tokens(record.name.replace("-", " "))
    evidence: list[str] = []
    score = 0.0

    hits = sorted(t for t in task_tokens if len(t) > 3 and _mentions(body_lower, t))
    if hits:
        score += 1.2 * len(hits)
        evidence.append(f"body mentions {sorted(hits)[:5]}")

    # Distinctive coverage breaks ties between skills that both mention the
    # generic words: the skill matching *more* of the task wins.
    score += 0.3 * len(task_tokens & name_tokens)

    # A body that explicitly disclaims the task should lose, not win. One shared
    # token is not a disclaimer -- skill-creator's "do not use /skill-test"
    # shares only "skill" with a skill-authoring task and was being penalised
    # for it. Require at least two overlapping tokens, none of which is just the
    # skill's own name.
    for negation in re.findall(r"(?:do not use|don't use|not for)[^.\n]{0,120}", body_lower):
        overlap = (task_tokens & _tokens(negation)) - name_tokens
        if len(overlap) >= 2:
            score -= 5.0
            evidence.append(f"body disclaims this task: {negation[:70]!r}")

    return score, evidence


def route(
    context: RunContext,
    task: str,
    shortlist_size: int = 4,
    min_score: float = 1.5,
    policy: Policy | None = None,
    precedence: list | None = None,
    taxonomy: Taxonomy | None = None,
) -> Routing:
    """Select one pack for a task.

    Candidates blocked by the trust gate are excluded *before* scoring, not
    after: a skill Capsule may not load should never be able to win a route and
    then be refused downstream, because that leaks the runner-up's slot.
    """
    intent, domain = classify(task, taxonomy)
    policy = policy or Policy()

    skills = list(context.of_type("skill"))
    blocked: list[str] = []
    for record in context.of_type("registry"):
        decision = policy.can_load(record)
        if decision.allowed:
            skills.append(record)
        else:
            blocked.append(f"{record.name} ({decision.reason})")

    candidates = []
    for record in skills:
        score, evidence = _stage1(task, record)
        if score > 0:
            candidates.append(Candidate(record=record, stage1_score=score, evidence=evidence))

    if not candidates:
        note = "no candidate scored above zero on the condensed index; refusing to guess"
        if blocked:
            note += f"; {len(blocked)} registry candidate(s) excluded by the trust gate"
        return Routing(task, intent, domain, None, note, confident=False, blocked=blocked)

    candidates.sort(key=lambda c: -c.stage1_score)
    shortlist = candidates[:shortlist_size]

    # Stage two: inspect shortlisted candidates in full, then rerank.
    for candidate in shortlist:
        bonus, evidence = _stage2(task, candidate.record)
        candidate.stage2_score = candidate.stage1_score + bonus
        candidate.evidence.extend(evidence)
        candidate.body_read = True

    # Declared precedence: a specialist beats a generalist it is competing with.
    # Applied as a bounded nudge rather than an absolute override, so a clearly
    # better generalist match still wins and the relationship stays advisory.
    applied_precedence: list[str] = []
    by_name = {c.record.name: c for c in shortlist}
    for rule in (precedence or []):
        if not rule.applies(task):
            continue
        winner, loser = by_name.get(rule.prefer), by_name.get(rule.over)
        if winner and loser:
            winner.stage2_score += getattr(rule, "weight", 2.0)
            note = f"precedence: {rule.prefer} preferred over {rule.over}"
            if rule.reason:
                note += f" ({rule.reason})"
            winner.evidence.append(note)
            applied_precedence.append(note)

    pre_order = [c.record.name for c in shortlist]
    shortlist.sort(key=lambda c: -c.stage2_score)
    reranked = [c.record.name for c in shortlist] != pre_order

    best = shortlist[0]
    if best.score < min_score:
        return Routing(
            task, intent, domain, None,
            f"best candidate {best.record.name} scored {best.score:.2f}, below threshold {min_score}; "
            "low confidence, not proceeding",
            considered=candidates, reranked=reranked, confident=False,
        )

    runner_up = shortlist[1] if len(shortlist) > 1 else None
    margin = f" over {runner_up.record.name} ({runner_up.score:.2f})" if runner_up else ""
    rationale = (
        f"intent={intent}, domain={domain}; selected after reading "
        f"{len(shortlist)} candidate bodies in full; score {best.score:.2f}{margin}. "
        + ("; ".join(best.evidence[:2]) if best.evidence else "")
    )

    return Routing(
        task, intent, domain, best.record, rationale,
        considered=candidates, reranked=reranked, confident=True, blocked=blocked,
        precedence_applied=applied_precedence,
    )
