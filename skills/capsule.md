# Using Capsule

Every command, what it prints, and when to reach for it.

Capsule is a control plane for skills, not a runtime. It decides what may be
indexed, rebuilt, loaded and written — then checks that the result matches. It never
executes the skills it routes to.

---

## Install

```bash
pip install pyyaml pytest
python3 -m pytest tests/ -q
```

Every command below is `python3 -m capsule.cli <command>`. Shortened to `capsule`
here for readability.

## What it puts in your repo

Seven files at most, and you only ever author two of them.

```text
your-project/
├── .claude/
│   ├── settings.json          ← YOURS. Merged, never overwritten.
│   ├── skills/…/SKILL.md      ← YOURS. The skills you write.
│   └── capsule/               ← generated. Delete it any time.
│       ├── hooks.json
│       ├── capsule-hook.py
│       └── README.md          ← explains the other two
├── capsule.toml               ← yours, optional. Absent = defaults.
└── capsule-index.json         ← derived. Gitignore it.
```

Three rules govern this, and they exist because the first version broke all
three:

**Your files are merged, never replaced.** `settings.json` is the project's.
Capsule unions its deny rules into it and leaves `allow`, `env` and your own
denials alone; the rules it manages are listed under `_capsule.managed_deny` so
you can see what it added and re-running never grows the list. An earlier
version overwrote the file, which deleted a project's `Bash(rm -rf *)` denial
while installing a security control.

**Everything generated lives in one directory.** `.claude/capsule/` is safe to
delete wholesale and regenerate. Nothing of Capsule's is scattered elsewhere in
`.claude/`, so "what did this tool add" has a one-word answer.

**Generated files explain themselves.** `.claude/capsule/README.md` says what
each file does, what it is enforcing, and the command that recreates it.
Generated config that the person who finds it cannot explain gets deleted, or
worse, copied into another repo unchanged.

If you want none of this: `capsule lint`, `doctor`, `route` and `verify` write
nothing at all. Only `harness` and `reconstruct` create files.

## Daily use

Most days you run two commands, and neither writes to your project.

**While working** — you do not run Capsule. If you installed the prompt router
(`harness --route-prompts`), it routes each message and injects the brief; if
you did not, nothing runs.

**Before committing** — one command, as a pre-commit hook:

```bash
capsule verify --skill <name> --ref=--cached    # exit 5 on violation
```

**In CI** — the corpus checks, which need no local state:

```bash
capsule index --out capsule-index.json
capsule lint  --index capsule-index.json        # collisions, budget, rules
capsule doctor --index capsule-index.json --severity medium
```

**When you add or change a skill** — rebuild the index and re-check routing,
because a new description can quietly steal another skill's triggers:

```bash
capsule index --out capsule-index.json && capsule lint --index capsule-index.json
```

**Rarely** — `harness` when a skill's prohibitions change, `reconstruct` when
you are packaging a skill for distribution, `registry` before installing
something from skills.sh.

That is the whole workflow. Everything else in this document is for when one of
those five reports something you want to understand.

## The shape of a session

```bash
capsule index                      # 1. discover
capsule show --type skill          # 2. see what you have
capsule lint                       # 3. corpus problems
capsule doctor                     # 4. per-skill calibration
capsule route --task "..."         # 5. which pack applies
capsule contract --skill X         # 6. what's enforceable
capsule verify --skill X --ref …   # 7. did the diff comply
capsule harness --skill X          # 8. stop it happening again
```

Steps 1–4 are corpus hygiene, run occasionally. Steps 5–8 are per-task, and are
where the daily value is.

## Global flags

Available on every subcommand:

| Flag | Effect |
|---|---|
| `--config PATH` | Config file. Default `capsule.toml`; absent file means built-in defaults. |
| `--allow-restricted` | Override the license gate. Marks the decision as requiring approval and writes it to the audit log. |
| `--allow-unaudited` | Override the trust gate. Same treatment. |

Both overrides are audited individually. Neither is a global "off switch" — that is
the point of having them be flags rather than config.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Nothing built |
| `2` | Low-confidence route |
| `3` | Policy refusal |
| `4` | Registry unavailable / changes unreadable |
| `5` | Contract violation |

`2` and `5` are the two you will script against. Both are findings, not crashes.

---

## Discovery

### index

```bash
capsule index --out capsule-index.json
capsule index --roots ./packages ./tools --out capsule-index.json
```

Walks the workspace and condenses every skill, instruction file and doc into one
record apiece. Everything downstream reads this file.

**Rebuild it whenever the task, repo, or instructions change.** Routing against a
stale index is how the wrong pack gets loaded, and nothing will warn you.

| Flag | Default |
|---|---|
| `--roots` | config-defined roots |
| `--out` | `capsule-index.json` |

### show

```bash
capsule show --index capsule-index.json --type skill
```

Prints the condensed index. `--type` filters by record type (`skill`, `doc`,
`instruction`). Use it to confirm discovery found what you expected before trusting
anything built on top.

### registry

```bash
capsule registry --query "pdf" --limit 10
capsule registry --fixtures tests/fixtures/skills-sh --limit 5
capsule registry --query "react" --merge --index capsule-index.json
```

Queries [skills.sh](https://www.skills.sh/) and applies the trust gate before
anything is installable.

```
BLOCK  find-skills       installs=2600000  trust=approval-required/MEDIUM
LOAD   frontend-design   installs=682100   trust=allow/LOW
BLOCK  azure-validate    installs=465200   trust=deny/CRITICAL
BLOCK  lark-approval     installs=435000   trust=deny/n-a  (pending is not a pass)
```

Verdicts aggregate across Gen Agent Trust Hub, Socket and Snyk by taking the
**worst** report, not a majority. `azure-validate` is rated Safe by two providers and
Critical by the third — a majority vote installs it. Install count is not safety
evidence; 2.6 million installs does not clear `find-skills`.

| Flag | Notes |
|---|---|
| `--query` | Search instead of the leaderboard |
| `--limit` | Result count, default 5 |
| `--fixtures` | Replay recorded responses offline |
| `--api-key` | Live queries |
| `--merge` | Write records into the index |
| `--allow-unaudited` | Override the gate, audited |

The sandbox egress allowlist does not include skills.sh, so the client is
transport-injectable and tests replay recorded fixtures offline. Add skills.sh to
the allowlist for live queries, or keep passing `--fixtures`. Exit `4` if the
registry is unreachable — it never falls back to unvetted skills.

---

## Corpus hygiene

### lint

```bash
capsule lint --index capsule-index.json
```

Runs your `capsule.toml` rules plus:

- **OWASP AST10** starter rule set
- **Lethal-trifecta detector** — private data access, untrusted content, and
  external communication in one skill
- **Description-budget truncation risk** — descriptions whose tail is cut before
  matching happens
- **Trigger-phrase collisions** — skills competing for the same words

The last two are corpus-level and cannot be computed per skill. `skills-ref
validate` and every other single-folder validator is blind to them by construction.

### doctor

```bash
capsule doctor --index capsule-index.json
capsule doctor --severity medium
capsule doctor --all
```

Assesses whether each skill is well-tuned for a current-generation model — inverting
the older instinct that more explicit guidance is safer.

```
setup-writing-style   7024w  behav=91  policy=8  presc=1.3   altitude=brittle
  [medium] progressive-disclosure: 7024w in a single file with 1 supporting file
```

Checks: reasoning-extraction refusal risk, safety-classifier domains, conflicting
directives (severity weighted by proximity), monolithic bodies, example density.

**Security invariants are excluded from the prescription count.** This came out of
running the check on Capsule's own pack, which first rated as the most prescriptive
artifact in the corpus almost entirely on lines like "never load what the audits will
not clear". A metric that cannot tell a license gate from a style rule will tell you
to weaken the license gate.

| Flag | Notes |
|---|---|
| `--severity` | Floor: `high`, `medium`, `low` (default), `info` |
| `--all` | Show every skill, including clean ones |

---

## Routing

### route

```bash
capsule route --index capsule-index.json --task "clean up this xlsx"
capsule route --config capsule.toml --index capsule-index.json --task "..."
```

Two-stage selection. Stage one shortlists from the condensed index; stage two reads
the full `SKILL.md` bodies and may overturn stage one. The condensed index is a
shortlisting device, not a decision.

Output is the selection, the rationale, and the runner-up margins. Same index plus
same task gives the same answer every time — which is what makes routing testable
rather than something you re-check by hand.

**Exit `2` means no candidate cleared the confidence threshold.** Near-misses are
reported and nothing is loaded. A marginal pack is worse than no pack.

### brief

```bash
capsule brief --index capsule-index.json --task "build a Word report generator"
```

Emits an injectable activation block for the selected pack: what was chosen, why,
and what its contract will enforce. Paste it into a session to make the selection
and its obligations explicit up front, rather than hoping the body gets read.

Accepts `--skill` to name a pack directly instead of routing to one.

---

## Reconstruction

### reconstruct

```bash
capsule reconstruct --index capsule-index.json --dest ./packs --package --audit
capsule reconstruct --index capsule-index.json --skill paint --dest ./packs --package
```

Rebuilds skills as portable packs. `SKILL.md` is copied **verbatim** — workflow
logic, validation behavior, dependencies and failure conditions are never
paraphrased, summarised, or "improved" in transit.

| Flag | Notes |
|---|---|
| `--skill` | One skill; omit for all rebuildable |
| `--dest` | Output directory, default `./packs` |
| `--package` | Also emit `.skill` archives |
| `--overwrite` | Replace existing output |
| `--audit` | Write the decision log |

**The license gate is not advisory.** Capsule indexes every skill it can read but
reconstructs only those whose license permits derivative works. On a restricted
source, `reconstruct()` raises `PolicyError` and leaves no artifacts behind.
Overriding requires `--allow-restricted`, which marks the decision as requiring
approval and logs it.

In the reference workspace that splits 24 Apache-2.0 sources (rebuildable) from 10
restricted-or-unknown (indexed, gated).

### validate

```bash
capsule validate ./packs/paint
capsule validate ./packs/*
```

Validates one or more built packs. Structural check on the output of
`reconstruct` — distinct from `skills-ref validate`, which checks source
conformance to the open spec. Run both.

### audit

```bash
capsule audit --index capsule-index.json
```

Replays the license decision for every indexed skill and prints the log. Use it to
answer "why can't I rebuild this one" and to produce a record of what was gated and
on what grounds.

---

## Enforcement

The failure that survives good routing: the pack is selected, loaded — and the diff
ignores it. You cannot make a model comply, so Capsule stops trying and checks the
artifact instead.

### contract

```bash
capsule contract --index capsule-index.json --skill docx
capsule contract --skill docx --advisory
```

Extracts the skill's checkable obligations and prints what will be enforced.

`--advisory` also lists the directives that **cannot** be verified. Run it once per
skill — the ratio is usually humbling, and knowing which half of your skill is
enforceable changes how you write the other half.

Accepts `--task` to route to a skill instead of naming it.

### verify

```bash
capsule verify --skill docx --ref=--cached
capsule verify --skill docx --ref main --repo ./service
capsule verify --skill docx --diff /tmp/change.patch
capsule verify --skill docx --paths src/report.ts src/render.ts
```

Checks a change against the contract.

```
FAIL docx-1: introduces `npm install`, which the skill prohibits
FAIL docx-3: introduces `SOLID`, which the skill prohibits
FAIL docx-4: introduces `•`, which the skill prohibits
4 violation(s), 1 satisfied, 7 of 8 obligations applicable
contract coverage: 62% (5 directive(s) are advisory and cannot be verified)
```

| Flag | Notes |
|---|---|
| `--ref` | Git ref to diff — `--cached`, `main`, a SHA |
| `--repo` | Repo to diff, default cwd |
| `--diff` | Read a unified diff from a file |
| `--paths` | Verify whole files instead of a diff |

Exit `5` on violation, so it drops into a pre-commit hook or CI job unchanged.
Exit `4` if the changes can't be read.

**Coverage is printed on every report.** Across the reference corpus only ~16% of
directives are mechanically checkable and 84% is taste. Claiming to enforce the rest
would be a lie — and a tool that fails a change for following the skill correctly is
worse than no tool.

### harness

```bash
capsule harness --skill docx --dest ./.claude
capsule harness --skill docx --dry-run
```

Pushes the same contract into the host's own enforcement primitives, so violations
are prevented instead of reported.

| Mechanism | When it acts | Cost of a violation |
|---|---|---|
| Skill body says it | never, mechanically | nothing — a hint |
| `capsule verify` | after the diff exists | a failed commit, a round trip |
| `PreToolUse` hook | before the write lands | one blocked tool call |
| Permission deny rule | before the command runs | nothing runs at all |

Command-shaped prohibitions (`npm install`, `pip install`) become `Bash(npm install
*)` deny rules. Content-shaped ones (`SOLID`, `•`, `\n`) become a `PreToolUse` hook
that blocks the write. The reference corpus splits 5 to 31 across those, so both are
needed.

Two deliberate choices:

- **Only `deny` rules are generated, never `allow`.** Inferring a grant from a regex
  over prose widens access on weak evidence.
- **The hook fails open** with a printed reason when it can't parse a payload.
  Failing closed is more secure in theory and worse in practice — a hook that blocks
  every edit gets deleted.

Hook payload field names are harness-specific. Verify with `CAPSULE_HOOK_DEBUG=1`
before relying on the hook.

`--dry-run` prints the artifacts instead of writing them. Writing to a path the
policy forbids exits `3`.

---

## Configuration

Everything Capsule exposes lives in `capsule.toml` — roots, thresholds, overrides,
precedence, custom rules. Pass it with `--config`; an absent file means built-in
defaults.

**Rules are declarative data** — regex and field matchers. They can tighten a
decision but never loosen one: a rule can turn an allow into a deny, never clear a
denial from a built-in gate. Loosening is the job of the explicit override flags,
each audited on its own.

**Config is data, never code.** A file dropped into a repo cannot make Capsule
execute anything. Programmatic rules are Python callables you import and register
explicitly, never loaded from disk.

See `references/customization.md`.

## Conflict resolution

Applied highest first. A lower source never overrides a higher one:

1. nearest scoped instruction file
2. active policy
3. selected skill pack
4. repository docs
5. condensed global index
6. general fallback behavior

## Failure handling

| Condition | Behavior |
|---|---|
| Filesystem not mounted | Say so explicitly; do not fabricate an index |
| Workspace present, sources absent | Continue; index what exists; report the gap |
| Instructions ambiguous | Stop and ask; do not pick an interpretation |
| Security, policy or scope conflict | Refuse; log the decision; do not guess |
| Confidence below threshold | Do not proceed |
| Registry unreachable | Exit `4`; never fall back to unvetted skills |

## What Capsule does not do

It is a gate and a selector, not a sandbox. It does not execute skills, it has no
seat in the execution path, and it cannot evaluate whether instructions are
*correct* — a confidently wrong schema passes every check here.

`references/limitations.md` scores the documented skill failure modes against what
Capsule actually fixes, using **solved / mitigated / not solved**, deliberately
harshly. Short version: strong on bookkeeping under uncertainty, weak wherever the
answer needs semantic understanding.

## Design notes

- `references/architecture.md` — module map, two-stage routing, sharp edges
- `references/policy.md` — gates, override semantics, audit format
- `references/run-context.md` — record fields, confidence scoring
- `references/trust.md` — audit aggregation and the evidence behind it
- `references/customization.md` — rules, precedence, config surface
- `references/context-engineering.md` — the Claude 5 shift, and Capsule's self-audit
- `references/adherence.md` — obligation contracts and diff verification
- `references/harness.md` — deny rules, blocking hooks, untrusted-input tiers
- `tests/test_capsule.py` — the executable specification
