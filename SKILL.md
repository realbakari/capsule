---
name: capsule
description: "Agent control plane for governed, replayable execution. Discovers instruction files, docs and skill folders in a workspace, condenses them into a compact run context, routes a task to exactly one skill pack, and rebuilds skills as portable, license-gated, validated packages. Use when asked to inventory or index available skills, choose which skill applies to a task, reconstruct or port a skill into a distributable pack, or enforce deny-by-default policy over what an agent may read and write. Not for authoring brand-new skills from scratch: route to skill-creator for that."
license: Complete terms in LICENSE.txt
---

Capsule turns a workspace into a small, auditable run context, then spends that
context carefully. Its bias is conservative: index everything readable, load as
little as possible, and refuse rather than guess.

## Purpose

Four operations, in order of use:

1. **Discover** — walk the workspace and condense every source to one record,
   locally and from the skills.sh registry.
2. **Route** — classify a task, shortlist candidates, read them in full, select one.
3. **Reconstruct** — rebuild a skill as a portable pack, gated by license.
4. **Govern** — two gates, deny by default, log every meaningful decision:
   *license* decides what may be rebuilt, *trust* decides what may be run.
5. **Extend** — custom rules and precedence in `capsule.toml`; corpus diagnostics
   for the failure modes no per-skill check can see.
6. **Assess** — check each skill's calibration for current models: prescription
   density, contradictions, monolithic bodies, refusal risk.
7. **Enforce** — extract a pack's checkable obligations, verify the diff against
   them, and push the same rules into the harness so violations are prevented
   rather than reported.

## Scope

In scope: skill discovery, run-context construction, task routing, skill
reconstruction and packaging, policy enforcement over read/write boundaries.

Out of scope: authoring new skills from nothing (use `skill-creator`), executing
the skills it routes to, and any network or destructive action.

## Usage rules

**Never skip discovery.** Routing against a stale index is how the wrong pack
gets loaded. Rebuild the index whenever the task, repo, or instructions change.

**Read candidate bodies before selecting.** The condensed index is a shortlisting
device, not a decision. Stage two reads full `SKILL.md` bodies and may overturn
stage one. A selection without a recorded rationale is a bug.

**Deny by default.** Unknown action, unknown license, unrecognised path: refuse.
The only affirmative permission is an explicit rule, never an inference.

**Never reconstruct what the license forbids.** Capsule indexes restricted
sources (metadata only) but will not rebuild them. Overriding this requires an
operator who holds the rights, and the override is itself logged.

**Never load what the audits will not clear.** For registry skills, take the
worst verdict any provider reports — not the majority, not the average. Install
count and publisher reputation are not safety evidence, and pending is not a
pass.

**Custom rules escalate only.** A rule in `capsule.toml` can turn an allow into a
deny; it can never clear a denial from a built-in gate. Loosening is the job of
the explicit override flags, each audited on its own. Config is data, never code:
a file dropped into a repo cannot make Capsule execute anything.

**Prevent before you report.** A rule checked after the diff costs a round trip; the
same rule as a deny rule or a blocking hook costs nothing. Push enforcement to the
earliest layer that can hold it. Generate only denials, never grants: inferring a
permission from a pattern match widens access on weak evidence.

**Enforce at the artifact, not the prompt.** A rule in a skill body is a hint. The
same rule checked against a diff is a gate. Extract what is mechanically checkable,
verify it, and always report coverage — claiming to enforce judgement would be a
lie, and a tool that fails a change for following the skill correctly is worse than
no tool.

**Separate policy from prescription.** Absolutes belong in security policy, where
they are invariants. Behavioral guidance should state intent and leave the model
latitude — current models are degraded, not helped, by enumerated rules. When
measuring instruction density, never count security invariants: that math tells you
to weaken the gate that is working.

**Preserve fidelity.** Reconstruction copies `SKILL.md` verbatim. Workflow logic,
validation behavior, dependencies and failure conditions are never paraphrased,
summarised, or "improved" in transit.

**Stop when confidence is low.** If the best candidate scores below threshold,
report the near-misses and stop. Do not load a marginal pack.

## Quickstart

```bash
python3 -m capsule.cli index --out capsule-index.json
python3 -m capsule.cli show  --index capsule-index.json --type skill
python3 -m capsule.cli route --index capsule-index.json --task "clean up this xlsx"
python3 -m capsule.cli reconstruct --index capsule-index.json --skill paint --dest ./packs --package
python3 -m capsule.cli validate ./packs/paint
python3 -m capsule.cli registry --fixtures tests/fixtures/skills-sh --limit 5
python3 -m capsule.cli lint --index capsule-index.json          # rules + diagnostics
python3 -m capsule.cli doctor --index capsule-index.json        # model calibration
python3 -m capsule.cli brief --task "build a Word report"        # injectable activation
python3 -m capsule.cli verify --skill docx --ref=--cached        # gate the diff (exit 5)
python3 -m capsule.cli harness --skill docx --dest ./.claude     # prevent, don't report
python3 -m capsule.cli audit --index capsule-index.json
python3 -m capsule.cli route --config capsule.toml --index capsule-index.json --task "..."
```

Exit codes: `0` success, `1` nothing built, `2` low-confidence route, `3` policy
refusal, `4` registry unavailable, `5` contract violation.

## Conflict resolution

Applied in this order, highest first. A lower source never overrides a higher one:

1. nearest scoped instruction file
2. active policy
3. selected skill pack
4. repository docs
5. condensed global index
6. general fallback behavior

## Supporting files

- `references/architecture.md` — module map, data flow, extension points
- `references/trust.md` — the audit trust model and the evidence behind it
- `references/customization.md` — writing rules, precedence, and config
- `references/context-engineering.md` — the Claude 5 shift and what it changed here
- `references/adherence.md` — contracts, diff verification, and coverage honesty
- `references/harness.md` — deny rules, PreToolUse hooks, untrusted-input tiers
- `references/limitations.md` — what skills break at, and what Capsule does and does not fix
- `references/policy.md` — the full policy model and audit format
- `references/run-context.md` — record field reference and confidence scoring
- `tests/test_capsule.py` — executable specification

## Failure handling

| Condition | Behavior |
|---|---|
| Filesystem not mounted | Say so explicitly; do not fabricate an index |
| Workspace present, sources absent | Continue; index what exists; report the gap |
| Instructions ambiguous | Stop and ask; do not pick an interpretation |
| Security, policy or scope conflict | Refuse; log the decision; do not guess |
| Confidence below threshold | Do not proceed |
| Registry unreachable | Exit 4; never fall back to unvetted skills |
