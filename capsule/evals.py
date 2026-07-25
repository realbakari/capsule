"""Skill evaluation: deterministic checks against agent output.

The evaluation module addresses a gap that contract verification alone cannot
fill. Contracts check whether a *diff* violates prohibitions after the fact.
Evaluations check whether the agent *used the skill correctly* — did it produce
the expected output given a prompt and the skill's instructions?

This is what resend/resend-skills pioneered with their `skill-evals/` directory.
Each skill can have a set of eval cases: a prompt, the skill to apply, and a
list of assertions that the agent's output should satisfy.

The key design decision: evals are deterministic string checks, not LLM calls.
You supply the agent's output (from whatever model you're testing), and this
module checks it against pattern assertions. This keeps the eval module
dependency-free and reproducible.

Usage:
    suite = load_evals("skill-evals/resend/evals.json")
    result = run_eval(suite, agent_output="import { Resend } from 'resend'; ...")
    print(result.report())
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence


@dataclass
class Assertion:
    """A single check against agent output."""
    kind: str           # "contains", "not_contains", "regex", "not_regex"
    pattern: str        # The string or regex pattern
    message: str = ""   # Human-readable description of what this checks

    def check(self, output: str) -> bool:
        if self.kind == "contains":
            return self.pattern in output
        elif self.kind == "not_contains":
            return self.pattern not in output
        elif self.kind == "regex":
            return bool(re.search(self.pattern, output))
        elif self.kind == "not_regex":
            return not re.search(self.pattern, output)
        return False


@dataclass
class EvalCase:
    """A single evaluation test case."""
    id: str
    prompt: str
    skill_name: str
    assertions: list[Assertion] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> EvalCase:
        assertions = [
            Assertion(
                kind=a.get("kind", "contains"),
                pattern=a.get("pattern", ""),
                message=a.get("message", ""),
            )
            for a in data.get("assertions", [])
        ]
        return cls(
            id=data.get("id", "unnamed"),
            prompt=data.get("prompt", ""),
            skill_name=data.get("skill_name", ""),
            assertions=assertions,
            tags=data.get("tags", []),
            description=data.get("description", ""),
        )


@dataclass
class EvalSuite:
    """Collection of eval cases for one or more skills."""
    skill_name: str
    cases: list[EvalCase] = field(default_factory=list)
    version: str = "1"

    @classmethod
    def from_json(cls, data_str: str) -> EvalSuite:
        data = json.loads(data_str)
        cases = [EvalCase.from_dict(c) for c in data.get("cases", [])]
        return cls(
            skill_name=data.get("skill_name", ""),
            cases=cases,
            version=data.get("version", "1"),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> EvalSuite:
        return cls.from_json(Path(path).read_text())


@dataclass
class AssertionResult:
    """Result of a single assertion check."""
    assertion: Assertion
    passed: bool

    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        desc = self.assertion.message or f"{self.assertion.kind}: {self.assertion.pattern[:60]}"
        return f"  {mark} {desc}"


@dataclass
class CaseResult:
    """Result of evaluating a single case."""
    case: EvalCase
    assertion_results: list[AssertionResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.assertion_results)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.assertion_results if not r.passed)

    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        return f"{mark} {self.case.id}: {self.case.description or self.case.prompt[:60]}"


@dataclass
class EvalReport:
    """Aggregate result of running an eval suite."""
    suite: EvalSuite
    case_results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(cr.passed for cr in self.case_results)

    @property
    def pass_count(self) -> int:
        return sum(1 for cr in self.case_results if cr.passed)

    @property
    def fail_count(self) -> int:
        return sum(1 for cr in self.case_results if not cr.passed)

    @property
    def total(self) -> int:
        return len(self.case_results)

    def report(self) -> str:
        lines = [f"Eval: {self.suite.skill_name} ({self.total} cases)"]
        lines.append("")
        for cr in self.case_results:
            lines.append(cr.line())
            if not cr.passed:
                for ar in cr.assertion_results:
                    if not ar.passed:
                        lines.append(ar.line())
        lines.append("")
        lines.append(f"{self.pass_count}/{self.total} passed, {self.fail_count} failed")
        return "\n".join(lines)


def run_case(case: EvalCase, output: str) -> CaseResult:
    """Run a single eval case against the given output text."""
    results = []
    for assertion in case.assertions:
        passed = assertion.check(output)
        results.append(AssertionResult(assertion=assertion, passed=passed))
    return CaseResult(case=case, assertion_results=results)


def run_eval(suite: EvalSuite, output: str) -> EvalReport:
    """Run all cases in a suite against the given output text."""
    case_results = [run_case(case, output) for case in suite.cases]
    return EvalReport(suite=suite, case_results=case_results)


def run_eval_per_case(suite: EvalSuite, outputs: dict[str, str]) -> EvalReport:
    """Run each case against its corresponding output (keyed by case id)."""
    case_results = []
    for case in suite.cases:
        output = outputs.get(case.id, "")
        case_results.append(run_case(case, output))
    return EvalReport(suite=suite, case_results=case_results)


def load_evals(search_path: str | Path) -> list[EvalSuite]:
    """Discover and load eval suites from a directory.

    Searches for evals.json files in:
      - skill-evals/<skill-name>/evals.json
      - evals/<skill-name>/evals.json
      - <search_path>/evals.json (if search_path is a file)
    """
    root = Path(search_path)
    suites = []

    if root.is_file() and root.name == "evals.json":
        suites.append(EvalSuite.from_file(root))
        return suites

    for eval_dir_name in ("skill-evals", "evals"):
        eval_dir = root / eval_dir_name
        if eval_dir.is_dir():
            for evals_file in eval_dir.rglob("evals.json"):
                try:
                    suites.append(EvalSuite.from_file(evals_file))
                except (json.JSONDecodeError, KeyError):
                    continue

    return suites
