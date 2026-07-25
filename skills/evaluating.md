# Evaluating

A skill is code with no compiler and no type checker. This page is what you can
check anyway, and how to put it in CI.

---

## Build evaluations before documentation

Write the evals first. Otherwise you document imagined problems.

1. **Find the gap.** Run representative tasks *without* the skill. Write down the
   specific failures — not "it wasn't great", but "it queried the wrong table" and
   "it included test accounts".
2. **Write three scenarios** that exercise those failures.
3. **Baseline.** Record how it does with no skill.
4. **Write minimal instructions** — just enough to close the gaps.
5. **Iterate.** Re-run, compare to baseline, refine.

Three is a floor, not a target. If you can't name three failing scenarios, you may
not need a skill.

An eval is a task plus the behavior you expect:

```json
{
  "skills": ["pdf-processing"],
  "query": "Extract all text from this PDF and save it to output.txt",
  "files": ["test-files/document.pdf"],
  "expected_behavior": [
    "Reads the PDF using an appropriate library or CLI tool",
    "Extracts text from every page without skipping any",
    "Writes the result to output.txt in readable form"
  ]
}
```

There is no standard runner for this format. You supply the harness — a script that
runs the query and judges the output against the rubric, either by hand or with a
model as judge.

## The four mechanically checkable properties

Output quality needs a judge. These four don't, which makes them the parts worth
automating first.

### 1. Activation — does the right skill fire?

```bash
python3 -m capsule.cli route --index capsule-index.json --task "pull the numbers out of this attachment"
```

Routing is deterministic — same index plus same task, same selection, with a
rationale and the runner-up margins printed. That turns trigger expectations into
assertions, so under- and over-triggering fail in CI instead of in production.

Exit `2` means nothing cleared the confidence threshold. Treat it as a failing
assertion, not an error.

### 2. Collisions — do two descriptions compete?

```bash
python3 -m capsule.cli lint --index capsule-index.json
```

Trigger-phrase collisions and description-budget truncation are **corpus-level**
properties. No per-skill validator can see them by construction — `skills-ref
validate` checks one folder at a time and is blind to the skill you added last week
that claims the same words.

`lint` also runs the OWASP AST10 starter rules, a lethal-trifecta detector, and any
custom rules from `capsule.toml`.

### 3. Calibration — is this skill tuned for current models?

```bash
python3 -m capsule.cli doctor --index capsule-index.json --severity medium
```

```
setup-writing-style   7024w  behav=91  policy=8  presc=1.3   altitude=brittle
  [medium] progressive-disclosure: 7024w in a single file with 1 supporting file
```

Catches over-prescription, contradictory directives, monolithic bodies, thin example
density, and reasoning-extraction refusal risk. Security invariants are deliberately
excluded from the prescription count — see [authoring.md](authoring.md#calibration)
for why that exclusion exists.

### 4. Adherence — did the agent actually follow the skill?

The failure that survives good routing. The pack is selected, loaded, and the diff
ignores it. You cannot make a model comply, so stop trying: check the artifact.

```bash
python3 -m capsule.cli contract --index capsule-index.json --skill docx
python3 -m capsule.cli verify   --index capsule-index.json --skill docx --ref=--cached
```

```
FAIL docx-1: introduces `npm install`, which the skill prohibits
FAIL docx-3: introduces `SOLID`, which the skill prohibits
FAIL docx-4: introduces `•`, which the skill prohibits
4 violation(s), 1 satisfied, 7 of 8 obligations applicable
contract coverage: 62% (5 directive(s) are advisory and cannot be verified)
```

**Coverage is printed on every report.** Across the reference corpus only about 16%
of directives are mechanically checkable; the other 84% is taste. Claiming to
enforce the rest would be a lie, and a tool that fails a change for following the
skill correctly is worse than no tool.

## Prevention beats verification

`verify` gates a change after it exists — a real gate, but the last one available.
The edit landed, the turn was spent, someone has to read the failure and go back.

| Mechanism | When it acts | Cost of a violation |
|---|---|---|
| Skill body says it | never, mechanically | nothing — a hint |
| `capsule verify` | after the diff exists | a failed commit, a round trip |
| `PreToolUse` hook | before the write lands | one blocked tool call |
| Permission deny rule | before the command runs | nothing runs at all |

```bash
python3 -m capsule.cli harness --index capsule-index.json --skill docx --dest ./.claude
```

Command-shaped prohibitions (`npm install`) become `Bash(npm install *)` deny rules.
Content-shaped ones (`SOLID`, `•`) become a `PreToolUse` hook. Both are needed — the
reference corpus splits 5 to 31 across them.

Only `deny` rules are generated, never `allow`: inferring a grant from a regex over
prose widens access on weak evidence. The hook **fails open** with a printed reason
when it can't parse a payload — failing closed is more secure in theory and worse in
practice, because a hook that blocks every edit gets deleted.

## CI

```yaml
name: skills
on: [push, pull_request]

jobs:
  skills:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Validate format
        run: npx skills-ref validate .claude/skills/*

      - name: Build index
        run: python3 -m capsule.cli index --out capsule-index.json

      - name: Corpus diagnostics
        run: python3 -m capsule.cli lint --index capsule-index.json

      - name: Calibration
        run: python3 -m capsule.cli doctor --index capsule-index.json --severity medium

      - name: Routing expectations
        run: ./scripts/check-routing.sh

      - name: Contract adherence
        run: python3 -m capsule.cli verify --index capsule-index.json --ref origin/main
```

`check-routing.sh` is your trigger assertions — the task strings you care about,
each checked against the skill you expect:

```bash
#!/usr/bin/env bash
set -euo pipefail

expect() {
  local task="$1" want="$2"
  if ! python3 -m capsule.cli route --index capsule-index.json --task "$task" \
       | grep -q "$want"; then
    echo "FAIL: '$task' did not route to $want" >&2
    return 1
  fi
}

expect "ship what I've got staged"                  writing-commits
expect "what changed this week"                     changelog
expect "pull the numbers out of this attachment"    pdf-processing
```

Exit codes, so you can gate precisely:

`0` success · `1` nothing built · `2` low-confidence route · `3` policy refusal ·
`4` registry unavailable · `5` contract violation

## Pre-commit

The cheapest place to catch adherence failures is before the commit exists:

```bash
#!/usr/bin/env bash
# .git/hooks/pre-commit
python3 -m capsule.cli verify --index capsule-index.json --ref=--cached || exit 1
```

## What none of this checks

Be clear about the boundary. Automation covers bookkeeping under uncertainty. It
does not cover:

- **Whether the output is good.** Needs a human or a model judge.
- **Whether the instructions are correct.** A confidently wrong schema passes every
  check here.
- **Advisory directives.** ~84% of what skills say is taste, and coverage reporting
  exists so you know that rather than assuming otherwise.
- **Runtime behavior in the host.** Capsule is a gate and a selector, not a sandbox.

`references/limitations.md` scores the documented skill failure modes against what
Capsule actually fixes, using **solved / mitigated / not solved** — deliberately
harshly. Read it before assuming a green build means a working skill.

## Team feedback

Once it works for you, watch someone else use it:

1. Share it and observe, don't instruct.
2. Ask: did it activate when expected? Were the instructions clear? What was missing?
3. Feed the answers back — other people's phrasings are the best source of
   description improvements you will get.

## Checklist

- [ ] At least three evals written before the body was
- [ ] Baseline measured without the skill
- [ ] Routing assertions for every phrasing you care about
- [ ] `capsule lint` clean — no collisions, no truncation risk
- [ ] `capsule doctor` clean at `--severity medium`
- [ ] `capsule verify` wired into pre-commit or CI
- [ ] `capsule harness` generated for prohibitions that must not be reachable
- [ ] Tested with every model you plan to run
- [ ] Someone other than the author has used it
