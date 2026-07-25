# Quickstart

Build a skill that works, then prove it works. About ten minutes.

The example is a commit-message skill because it is small enough to read in full and
real enough to have opinions.

---

## 1. Create the folder

The directory name and the `name` field must match. This is a spec requirement, not
a convention.

```bash
mkdir -p .claude/skills/writing-commits
```

Committing to `.claude/skills/` puts the skill in the repo, where your teammates and
your CI get it. `~/.claude/skills/` is for personal skills and travels with you, not
with the code.

## 2. Write SKILL.md

````markdown
---
name: writing-commits
description: >-
  Writes conventional-commit messages by reading the staged diff.
  Use when the user says "commit this", asks for a commit message,
  or asks to review what is staged before committing.
---

# Writing commits

## Process

1. Run `git diff --cached` — never write a message without reading the diff.
2. Pick one type: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.
3. Scope is the package or directory touched, lowercase, no slashes.
4. Subject is imperative and under 60 characters. No trailing period.
5. Body explains *why*, not what. Skip it if the subject is self-evident.

## Format

```
type(scope): imperative subject

Why this change was needed. Wrap at 72.
```

## Examples

Input: added JWT validation middleware and a login route
```
feat(auth): validate JWTs on protected routes

Sessions were trusted on the client. Move verification server-side.
```

Input: bumped lodash, standardised error envelopes
```
chore: update dependencies and unify error responses

- lodash 4.17.21
- all handlers now return {error: {code, message}}
```

## Rules

- Never `git add` files the user did not stage.
- Never amend or force-push without being asked.
- If the diff spans unrelated concerns, say so and propose splitting it.
````

Three things in there are doing the real work: the description names the *phrases a
user actually types*; the examples show input→output rather than describing it; and
the rules are prohibitions, which are the only part a machine can later check.

## 3. Check the format

```bash
npx skills-ref validate .claude/skills/writing-commits
```

This checks frontmatter validity and naming rules. It does not check whether the
skill is any good — that comes next.

## 4. See whether it triggers

Restart your agent, or let live-reload pick it up, then type something a user would
actually type — not the skill's own vocabulary:

```
ship what I've got staged
```

If the skill does not fire, the description is wrong. It is almost never the body.
Go to [descriptions.md](descriptions.md).

## 5. Index it with Capsule

```bash
python3 -m capsule.cli index --out capsule-index.json
python3 -m capsule.cli show --index capsule-index.json --type skill
```

`index` walks the workspace and condenses every skill, instruction file and doc into
one record apiece. Everything downstream reads this index, so rebuild it whenever a
skill changes.

## 6. Confirm it routes

```bash
python3 -m capsule.cli route --index capsule-index.json --task "ship what I've got staged"
```

Routing is deterministic: same index plus same task gives the same selection, with a
recorded rationale and the runner-up margins. That is what makes it testable — the
task strings you care about become assertions in CI instead of things you re-check
by hand.

Exit code `2` means no candidate cleared the confidence threshold. That is a
finding, not a crash: your description does not cover the phrasing you just typed.

## 7. Lint the corpus

```bash
python3 -m capsule.cli lint --index capsule-index.json
```

Per-skill validators cannot see corpus-level problems. `lint` can:

- **Description-budget truncation** — descriptions long enough that the tail is cut
  before matching ever happens.
- **Trigger-phrase collisions** — two skills competing for the same words. If you
  also have a `review-changes` skill mentioning "staged", this is where you find out.
- **OWASP AST10 + lethal-trifecta checks** — plus any rules you define in
  `capsule.toml`.

## 8. Check calibration

```bash
python3 -m capsule.cli doctor --index capsule-index.json
```

```
writing-commits   340w  behav=12  policy=3  presc=0.9   altitude=ok
```

`doctor` flags the things that make a skill *worse* on current models: over-
prescription, contradictory directives, monolithic bodies, refusal risk. Current
models are degraded, not helped, by enumerated rules — a skill that grew to 3,000
words of edge cases usually performs worse than the 300-word version.

## 9. Enforce the prohibitions

The three rules at the bottom of the skill are worth nothing if the agent skims
past them. Extract what is mechanically checkable:

```bash
python3 -m capsule.cli contract --index capsule-index.json --skill writing-commits
```

Then gate a real change against it:

```bash
python3 -m capsule.cli verify --index capsule-index.json --skill writing-commits --ref=--cached
```

Exit `5` on violation, so this drops into a pre-commit hook or a CI job unchanged.
Coverage is printed on every report — most directives are taste and cannot be
verified, and claiming otherwise would be a lie.

## 10. Prevent instead of reporting

```bash
python3 -m capsule.cli harness --index capsule-index.json --skill writing-commits --dest ./.claude --dry-run
```

Drop `--dry-run` to write. Command-shaped prohibitions become permission `deny`
rules; content-shaped ones become a `PreToolUse` hook. A rule checked after the diff
costs a round trip. The same rule as a deny rule costs nothing, because the command
never runs.

---

## Where to go next

- The skill does not fire, or the wrong one does → [descriptions.md](descriptions.md)
- The skill fires but the output is inconsistent → [authoring.md](authoring.md)
- A step must happen the same way every time → [scripts.md](scripts.md)
- You want this in CI → [evaluating.md](evaluating.md)
- Full command reference → [capsule.md](capsule.md)
