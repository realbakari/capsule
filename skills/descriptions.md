# Descriptions

The 1,024 characters that decide whether anything else you wrote ever runs.

A skill with a perfect body and a vague description does nothing. A skill with a
mediocre body and a precise description works. Spend your time accordingly.

---

## What the field is actually for

The `description` is the only part of your skill resident in context. At startup the
agent loads every skill's `name` and `description` into the system prompt; when a
request arrives it matches against that text and decides which body to read.

So the description is not documentation. It is a **retrieval key**, competing
against every other skill installed. Write it for the matcher, not for a human
browsing a catalog.

Two clauses, always:

```yaml
description: >-
  Extracts text and tables from PDF files, fills forms, merges documents.
  Use when working with PDF files, or when the user mentions PDFs, forms,
  or document extraction.
```

**What it does** — capabilities, in third person.
**When to use it** — the literal words a user would type.

The second clause is the one people skip, and it is the one that does the work.

## Third person, always

```yaml
description: Processes Excel files and generates reports     # good
description: I can help you process Excel files              # avoid
description: You can use this to process Excel files         # avoid
```

The text is injected into a system prompt written in third person. Mixing
point-of-view degrades discovery. This is a small rule with a real effect.

## The four failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Never fires | States capability, not trigger | Add the literal phrases users type |
| Wrong skill fires | Two descriptions overlap | Differentiate, or say what one is *not* for |
| Fires constantly | Trigger clause too broad | Constrain to a file type, tool, or domain |
| Fires only when named | Description truncated past the cap | Front-load the decisive use case |

### Never fires

The most common bug. You described the implementation instead of the trigger.

```yaml
description: Analyzes commit history using git log parsing and heuristic
  classification to produce structured summaries.
```

Nothing there matches "what changed this week?". Rewrite around the request:

```yaml
description: Summarizes recent commit history into a changelog. Use when the
  user asks what changed, wants a release summary, or asks to write release notes.
```

Test it by writing down three ways a real user would ask, in their words. If none of
those phrasings appear in your description, it will not fire.

### Wrong skill fires

Two skills claiming the same words. If `code-review` and `security-review` both say
"review changes", the match is a coin flip.

Fix by differentiating on the axis that actually separates them, and by stating the
exclusion:

```yaml
# code-review
description: Reviews a diff for correctness, readability and convention
  adherence. Use for general code review. Not for security review — use
  security-review for vulnerability analysis.

# security-review
description: Audits a diff for injection, authentication, secrets handling and
  dependency vulnerabilities. Use when the user asks about security,
  vulnerabilities, or requests a security review specifically.
```

Naming the sibling skill is legitimate and effective. The format has no way to
declare that one skill specialises another, so the description is where you say it.

You cannot find these by inspection once you have more than a handful of skills.
That is a corpus-level property:

```bash
python3 -m capsule.cli lint --index capsule-index.json
```

### Fires constantly

Trigger words like "code", "files", "data", "review" match nearly everything. A
skill that fires on every turn is worse than a skill that never fires — it burns
context and displaces better matches.

Constrain to something concrete: a file extension, a tool name, a domain noun.

```yaml
# too broad
description: Helps with data analysis. Use when analyzing data.

# constrained
description: Queries the analytics BigQuery warehouse and builds revenue,
  pipeline and usage reports. Use when the user asks about ARR, sales pipeline,
  API usage metrics, or names a table in the analytics dataset.
```

Where the host supports it, the mechanical version is better than the prose version.
Claude Code's `paths: ["**/*.tf"]` gates activation on the files in play; that is a
guarantee, where a description is a hope.

### Fires only when named

Your description got cut. Two different caps, two different behaviours:

- **1,024 characters** — spec limit, enforced by the Claude API. Over it, upload is
  **rejected**.
- **1,536 characters** — Claude Code's cap on `description` plus `when_to_use`
  combined in the skill listing. Over it, the tail is **silently truncated**.

The silent one is the dangerous one. A 1,800-character description doesn't fail
loudly; it just loses whatever you put at the end, which is usually the trigger
clause, because people write capabilities first.

Front-load the decisive use case. Then neither cap can hurt you, and
`capsule lint` will tell you if you drift.

## Good and bad, side by side

```yaml
# Good — specific verbs, specific triggers, specific nouns
description: Extract text and tables from PDF files, fill forms, merge documents.
  Use when working with PDF files or when the user mentions PDFs, forms, or
  document extraction.

description: Analyze Excel spreadsheets, create pivot tables, generate charts.
  Use when analyzing Excel files, spreadsheets, tabular data, or .xlsx files.

description: Generate descriptive commit messages by analyzing git diffs. Use
  when the user asks for help writing commit messages or reviewing staged changes.
```

```yaml
# Bad — nothing to match against
description: Helps with documents
description: Processes data
description: Does stuff with files
```

The bad ones aren't bad because they're short. They're bad because no user request
contains their words.

## Testing that it triggers

Do not test by typing the skill's own vocabulary — of course `"process this PDF"`
fires `pdf-processing`. Test with the phrasings you'd actually use on a bad day.

```bash
python3 -m capsule.cli route --index capsule-index.json --task "pull the numbers out of this attachment"
```

Routing is deterministic — same index plus same task gives the same result, with the
rationale and the runner-up margins printed. That makes trigger expectations
assertable:

```bash
for task in \
  "pull the numbers out of this attachment" \
  "what's in this form" \
  "merge these two files into one document"
do
  python3 -m capsule.cli route --index capsule-index.json --task "$task"
done
```

Exit `2` means nothing cleared the confidence threshold — a real finding, and
better than a marginal pack loading silently. Wire these into CI and under- and
over-triggering fail in the build rather than in someone's session. See
[evaluating.md](evaluating.md).

## Checklist

- [ ] Third person, no "I" or "you"
- [ ] States what it does **and** when to use it
- [ ] Contains at least three phrasings a real user would type
- [ ] Trigger nouns are concrete — a file type, tool, or domain, not "data"
- [ ] Names its nearest sibling skill if one exists
- [ ] Decisive use case in the first sentence
- [ ] Under 1,024 characters
- [ ] `capsule lint` reports no collision or truncation risk
- [ ] `capsule route` selects it for each of your three phrasings
