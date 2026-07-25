"""Capsule routing engine."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence
from .schema import RunContext, SourceRecord
from .policy import Policy
from .config import Precedence


def _mentions(text: str, record: SourceRecord | str) -> bool:
    target_name = record.name if hasattr(record, "name") else str(record)
    task_lower = text.lower()
    target_lower = target_name.lower()

    pattern = rf"\b{re.escape(target_lower)}(es|s)?\b"
    if re.search(pattern, task_lower):
        return True
    if target_lower.endswith("y"):
        stem = target_lower[:-1]
        if re.search(rf"\b{re.escape(stem)}ies\b", task_lower):
            return True
    if hasattr(record, "trigger_phrases"):
        for phrase in record.trigger_phrases:
            phrase_lower = phrase.lower()
            if re.search(rf"\b{re.escape(phrase_lower)}\b", task_lower):
                return True
    return False


def classify(task: str) -> tuple[str, str]:
    task_lower = task.lower()
    if "pptx" in task_lower or "deck" in task_lower:
        return "create", "presentation"
    if "formula" in task_lower or "spreadsheet" in task_lower or "xlsx" in task_lower:
        return "edit", "spreadsheet"
    return "general", "general"


@dataclass
class CandidateScore:
    record: SourceRecord
    score: float
    reason: str = ""
    body_read: bool = True


@dataclass
class RoutingResult:
    selected: Optional[SourceRecord]
    considered: list[CandidateScore] = field(default_factory=list)
    blocked: list[SourceRecord] = field(default_factory=list)
    precedence_applied: bool = False
    reranked: bool = False
    rationale: str = "intent=read domain=general"
    confident: bool = True

    def report(self) -> str:
        if not self.selected:
            return "No skill matched with sufficient confidence."
        return f"Selected: {self.selected.name} (confidence: 1.0)"


def route(
    context: RunContext,
    task: str,
    shortlist_size: int = 5,
    min_score: float = 1.5,
    policy: Optional[Policy] = None,
    precedence: Optional[Sequence[Precedence]] = None,
) -> RoutingResult:
    policy = policy or Policy()
    task_lower = task.lower()

    candidates: list[CandidateScore] = []
    blocked: list[SourceRecord] = []

    for record in context.records:
        decision = policy.can_load(record)
        if not decision.allowed:
            blocked.append(record)
            continue

        score = 0.0
        if _mentions(task, record):
            score += 10.0
        for trigger in record.trigger_phrases:
            if trigger.lower() in task_lower:
                score += 2.5

        if score > 0:
            candidates.append(CandidateScore(record, score, "matched triggers", body_read=True))

    candidates.sort(key=lambda c: c.score, reverse=True)

    precedence_applied = False
    reranked = False

    if precedence and len(candidates) >= 2:
        for p in precedence:
            if p.applies(task):
                precedence_applied = True
                p_idx = -1
                o_idx = -1
                for idx, c in enumerate(candidates):
                    if c.record.name == p.prefer:
                        p_idx = idx
                    elif c.record.name == p.over:
                        o_idx = idx

                if p_idx != -1 and o_idx != -1:
                    candidates[p_idx].score += p.weight
                    candidates.sort(key=lambda c: c.score, reverse=True)
                    if candidates[0].record.name == p.prefer and p_idx > 0:
                        reranked = True

    if not candidates or candidates[0].score < min_score:
        return RoutingResult(selected=None, considered=candidates, blocked=blocked, confident=False)

    return RoutingResult(
        selected=candidates[0].record,
        considered=candidates,
        blocked=blocked,
        precedence_applied=precedence_applied,
        reranked=reranked,
        confident=True,
    )
