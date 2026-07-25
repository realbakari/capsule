# Capsule

An agent control plane for governed, replayable execution.

Capsule inspects a workspace, condenses everything readable into a compact run
context, routes a task to exactly one skill pack, and rebuilds skills as
portable, license-gated, validated packages.

## Install & Distribute

### 1. Install Skills via `npx skills` (Universal Agent Distribution)

Install skills directly into Claude Code, Cursor, Codex, Windsurf, or GitHub Copilot:

```bash
# Install all skills from Capsule repository
npx skills add realbakari/capsule

# Or install a specific skill
npx skills add realbakari/capsule --skill evaluating
```

### 2. Python Control Plane CLI

```bash
pip install pyyaml pytest
python3 -m pytest -q
```

## Use

```bash
# Indexing & Taxonomy
python3 -m capsule.cli index --out capsule-index.json --by-category --lifecycle stable
python3 -m capsule.cli show --index capsule-index.json --type skill
python3 -m capsule.cli route --index capsule-index.json --task "clean up this xlsx"

# Reconstructing & Validating
python3 -m capsule.cli reconstruct --index capsule-index.json --dest ./packs --package --audit
python3 -m capsule.cli validate ./packs/*
python3 -m capsule.cli audit --index capsule-index.json

# Multi-Host Plugin Manifest Emission (.claude-plugin, .codex-plugin, .cursor-plugin, .grok-plugin)
python3 -m capsule.cli emit-plugins --repo realbakari/capsule --out .

# Deterministic Skill Evaluations
python3 -m capsule.cli eval --evals ./skill-evals --output agent-output.txt
```

## Customization

Everything Capsule exposes lives in `capsule.toml` — roots, thresholds, overrides,
precedence, and custom rules. Pass it with `--config`; an absent file means
built-in defaults. Rules are declarative data (regex + field matchers) that can
tighten a decision but never loosen one; programmatic rules are Python callables
you import and register explicitly, never loaded from disk. See
`references/customization.md`.

`capsule lint` runs your rules plus the OWASP-AST10 starter set, a lethal-trifecta
detector, a description-quality check, and two corpus-level diagnostics —
description-budget truncation risk and trigger-phrase collisions — that no
per-skill validator can perform.

The description is the only resident part of a skill and the sole input to
triggering, so it gets its own check: first- or second-person phrasing (the text
is injected into a system prompt) and descriptions that say what a skill does but
never when to use it. Across the 22-skill marketplace corpus that finds two
skills with no trigger clause at all.

## Agents are a governed surface too

Capsule indexes any `.md` under an `agents/` directory. An agent definition has
the same triggering problem a skill does, plus an explicit permission grant:

```
code-architect:
  [info]   agent-high-reach-tools: grants 10 tool(s) including BashOutput, WebFetch
code-simplifier:
  [medium] agent-inherits-all-tools: names no tools and therefore inherits every
           tool the host allows, including write and execute
```

The defect worth naming is the second one, and it is an omission rather than an
excess: a definition with no `tools:` key inherits everything, and the omission
reads as a blank line rather than as a grant. In the installed marketplace
corpus that is **12 of 24 agents**. This is the same "declared but never
derived" gap `references/limitations.md` #9 records for skills, one layer over.

## Getting the right skill in front of the agent, every turn

`brief` emits an activation block, but something has to inject it. `capsule
harness --route-prompts` emits a `UserPromptSubmit` hook that routes every
prompt against the index and injects the brief automatically:

```bash
capsule harness --index capsule-index.json --route-prompts --dest ./.claude
```

```
<capsule-activation>
Selected Skill: specs-websocket
Source: ~/.agents/skills/specs-websocket/SKILL.md
Context: score 9.72 over specs-leaf-write-scenarios (2.28)
Enforceable obligations:
  - must use `this.socket?.readyState === WebSocket.OPEN`
</capsule-activation>
```

This is the only point where Capsule can influence *which* pack the model
reads. `PreToolUse` fires once the agent has already decided to write, and
`verify` runs after the diff exists — both are too late to change the choice.

It **fails open** and stays silent below the confidence threshold: an unrelated
prompt gets nothing, a two-word prompt gets nothing, a broken payload gets
nothing. Injecting a marginal pack is worse than injecting none, and a hook
that interrupts the conversation when routing is uncertain gets deleted.

## Working on skills Capsule has never seen

Categories, intents and domains are data in `capsule.toml`, not hardcoded
tables — and domains are derived from your own index, so a workspace of
`specs-websocket` / `specs-depth` / `specs-asr` yields a `specs` domain with
nothing declared. On a 62-skill Lens Studio corpus that lifts domain
classification from 1 task in 10 to 7, and category mislabelling
(`perfetto-trace-analysis` → admin-tasks, because "form" is inside
"performance") drops to zero. See `references/customization.md`.

## Adherence: when the agent ignores the skill

The failure that survives good routing. The pack is selected, loaded — and the diff
ignores it. You cannot make a model comply, so Capsule stops trying: it extracts the
pack's checkable commitments and verifies the **diff** against them.

```bash
capsule brief  --task "build a Word report generator"   # injectable activation block
capsule contract --skill docx                            # what will be enforced
capsule verify --skill docx --ref=--cached               # exit 5 on violation
```

```
FAIL docx-1: introduces `npm install`, which the skill prohibits
FAIL docx-3: introduces `SOLID`, which the skill prohibits
FAIL docx-4: introduces `•`, which the skill prohibits
4 violation(s), 1 satisfied, 7 of 8 obligations applicable
contract coverage: 62% (5 directive(s) are advisory and cannot be verified)
```

Whether the agent read the pack stops determining the outcome. **Coverage is printed
on every report** — across this corpus only 16% of directives are mechanically
checkable and 84% are taste. Claiming to enforce the rest would be a lie. See
`references/adherence.md`.

## Prevention, not just reporting

`verify` gates a change after it exists. `capsule harness` pushes the same contract
into the host's own enforcement primitives, so violations are prevented instead:

```bash
capsule harness --skill docx --dest ./.claude
```

| Mechanism | When it acts |
|---|---|
| skill body says it | never, mechanically |
| `capsule verify` | after the diff exists |
| `PreToolUse` hook | before the write lands |
| permission deny rule | before the command runs |

Command-shaped prohibitions (`npm install`) become `Bash(npm install *)` deny
rules. Content-shaped ones (`SOLID`, `•`, `\n`) become a `PreToolUse` hook that
blocks the write. The corpus splits 5 to 31 across those, so both are needed.

Only `deny` rules are generated, never `allow` — inferring a grant from a regex over
prose widens access on weak evidence. The hook **fails open** with a printed reason
when it can't parse a payload: failing closed is more secure in theory and worse in
practice, because a hook that blocks every edit gets deleted. See
`references/harness.md`.

## Model calibration

`capsule doctor` assesses whether a skill is well-tuned for a current-generation
model, inverting the older instinct that more explicit guidance is safer:

```
setup-writing-style   7024w  behav=91  policy=8  presc=1.3   altitude=brittle
  [medium] progressive-disclosure: 7024w in a single file with 1 supporting file
```

Checks: reasoning-extraction refusal risk (high), safety-classifier domains,
conflicting directives (severity by proximity), monolithic bodies, example density.

Crucially, **security invariants are excluded from the prescription count.** This
came out of running the check on Capsule's own pack, which first rated as the most
prescriptive artifact in the corpus — almost entirely on lines like "never load what
the audits will not clear". A metric that cannot tell a license gate from a style
rule will tell you to weaken the license gate. See
`references/context-engineering.md`.

## What skills break at

`references/limitations.md` is a grounded review of documented skill failure modes
(Snyk ToxicSkills, OWASP AST10, the context-rot literature) scored against what
Capsule actually fixes. Short version: strong on bookkeeping under uncertainty,
weak wherever the answer needs semantic understanding or a seat in the execution
path. It is a gate and a selector, not a sandbox.

## The license gate

Capsule indexes every skill it can read. It **reconstructs** only those whose
license permits derivative works. In this workspace that splits 24 Apache-2.0
sources (rebuildable) from 10 restricted-or-unknown sources (indexed, gated).

That gate is not advisory. `reconstruct()` raises `PolicyError` and leaves no
artifacts behind. Overriding it requires `--allow-restricted`, which marks the
decision as requiring approval and writes it to the audit log.

## The trust gate

Registry skills pass through a second, independent gate. Verdicts aggregate
across Gen Agent Trust Hub, Socket and Snyk by taking the **worst** report, not
a majority:

```
BLOCK  find-skills       installs=2600000  trust=approval-required/MEDIUM
LOAD   frontend-design   installs=682100   trust=allow/LOW
BLOCK  azure-validate    installs=465200   trust=deny/CRITICAL
BLOCK  lark-approval     installs=435000   trust=deny/n-a  (pending is not a pass)
```

`azure-validate` is rated Safe by two providers and Critical by the third. A
majority vote loads it. See `references/trust.md`.

The sandbox egress allowlist does not include skills.sh, so the client is
transport-injectable and the tests replay recorded fixtures offline. Add
skills.sh to the allowlist for live queries, or keep passing `--fixtures`.

## Design notes

- `references/architecture.md` — module map, two-stage routing, sharp edges
- `references/policy.md` — gates, override semantics, audit format
- `references/run-context.md` — record fields, confidence scoring
- `references/trust.md` — audit aggregation and the evidence behind it
- `references/customization.md` — rules, precedence, config surface
- `references/limitations.md` — skill failure modes vs. what Capsule solves
- `references/context-engineering.md` — the Claude 5 shift, and Capsule's self-audit
- `references/adherence.md` — obligation contracts and diff verification
- `references/harness.md` — deny rules, blocking hooks, untrusted-input tiers
- `tests/test_capsule.py` — 197 tests; the executable specification
  (33 need the `/mnt/skills` corpus and skip without it)

## Exit codes

`0` success · `1` nothing built · `2` low-confidence route · `3` policy refusal · `4` registry unavailable · `5` contract violation
