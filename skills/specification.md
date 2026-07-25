# Specification

The format, its constraints, and — importantly — where individual hosts diverge from
it. Most skill bugs that survive review are portability bugs: something one host
accepts and another silently ignores.

Normative source: [agentskills.io/specification](https://agentskills.io/specification).
Where this page adds host-specific detail it says so explicitly.

---

## Directory structure

A skill is a directory containing at minimum a `SKILL.md`:

```text
skill-name/
├── SKILL.md          # required: metadata + instructions
├── scripts/          # optional: executable code
├── references/       # optional: documentation
├── assets/           # optional: templates, resources
└── ...               # any additional files or directories
```

The three optional directory names are conventions the spec names explicitly. Use
them — hosts and tooling look for them, and a reader scanning your repo shouldn't
have to guess whether `lib/` holds runnable code or reference prose.

## Frontmatter

`SKILL.md` opens with YAML, then markdown.

| Field | Required | Constraints |
|---|---|---|
| `name` | **Yes** | 1–64 chars. Lowercase `a–z`, `0–9`, hyphens only. No leading/trailing hyphen. No consecutive hyphens. **Must match the parent directory name.** |
| `description` | **Yes** | 1–1024 chars, non-empty. What it does *and* when to use it. |
| `license` | No | License name, or the name of a bundled license file. |
| `compatibility` | No | ≤500 chars. Environment requirements — target product, system packages, network access. |
| `metadata` | No | Arbitrary string→string map. Namespace your keys. |
| `allowed-tools` | No | Space-separated pre-approved tools. **Experimental** — support varies. |

Minimal:

```yaml
---
name: skill-name
description: A description of what this skill does and when to use it.
---
```

With optional fields:

```yaml
---
name: pdf-processing
description: Extract PDF text, fill forms, merge files. Use when handling PDFs.
license: Apache-2.0
compatibility: Requires Python 3.11+ and pdfplumber
metadata:
  author: example-org
  version: "1.0"
---
```

### name

Valid: `pdf-processing`, `data-analysis`, `code-review`.
Invalid: `PDF-Processing` (uppercase), `-pdf` (leading hyphen), `pdf--processing`
(consecutive hyphens).

Naming style is your call, but pick one and hold it across the corpus. Gerunds
(`processing-pdfs`, `analyzing-spreadsheets`) read well and describe an activity;
noun phrases (`pdf-processing`) are equally acceptable. What actually costs you is
inconsistency plus vagueness — `helper`, `utils`, `tools`, `documents`, `data` are
names that guarantee routing ambiguity later.

> **Host divergence.** The spec makes `name` required and requires it to match the
> directory. Claude Code treats it as optional and defaults it to the directory
> name. Anthropic's platform additionally rejects names containing `anthropic` or
> `claude` and rejects XML tags — that rule is **not** in the open spec. Write
> spec-conformant names and you satisfy everyone.

### description

The single most consequential field. It is the only part of your skill resident in
context, and it is what the agent matches a request against. It gets its own page:
[descriptions.md](descriptions.md).

Constraints worth memorising:

- **1,024 characters**, per the spec and enforced by the Claude API on upload.
- **Third person, always.** "Extracts text from PDFs", not "I can help you extract"
  and not "You can use this to extract". The text is injected into a system prompt;
  mixed point-of-view measurably degrades discovery.
- **One description per skill.** There is no second chance and no alias list.

> **Host divergence.** Claude Code truncates the combined `description` plus its own
> `when_to_use` extension at **1,536 characters** in the skill listing. Two different
> caps, two different mechanisms — the spec limit rejects; the host limit silently
> cuts. Front-load the decisive use case and neither can hurt you.

### license

Skip it and you have published a skill nobody can legally reuse. For anything you
put in a public repo, set it.

Capsule treats this field as load-bearing: `capsule reconstruct` rebuilds only
skills whose license permits derivative works, and raises `PolicyError` on the rest
rather than producing a half-legal artifact. `license: Proprietary. LICENSE.txt has
complete terms` is a valid and useful value.

### compatibility

Most skills don't need it. Set it when the skill genuinely won't work everywhere:

```yaml
compatibility: Designed for Claude Code (or similar products)
compatibility: Requires git, docker, jq, and internet access
compatibility: Requires Python 3.14+ and uv
```

The "internet access" case is the one that bites. A skill that curls an API works in
Claude Code and fails silently in the Claude API's no-network container.

### allowed-tools

Experimental in the spec, more developed in some hosts:

```yaml
allowed-tools: Bash(git:*) Bash(jq:*) Read
```

This is a *grant*. Its safety value is limited — it widens what runs without asking.
The restrictive counterpart is more useful where a host offers one. In Claude Code,
`disallowed-tools` removes tools from the pool entirely while the skill is active,
and `paths` limits automatic activation to matching files. Prefer narrowing.

## Body content

No format restrictions. Recommended shape: step-by-step instructions, input/output
examples, common edge cases.

The whole body loads on activation, so treat length as a budget:

- **Under 500 lines** for `SKILL.md`. Past that, split.
- **Under ~5,000 tokens** for the body. This is a recommendation, not a validated
  limit — nothing rejects a longer file, it just crowds out conversation history.

Split by moving detail into `references/`, not by writing tersely to the point of
ambiguity.

## File references

Relative paths from the skill root:

```markdown
See [the reference guide](references/REFERENCE.md) for details.
Run `scripts/extract.py` to pull fields.
```

**Keep references one level deep from `SKILL.md`.** When a referenced file
references another file, agents tend to preview rather than read — `head -100` and
similar — and act on partial content. A chain of `SKILL.md → advanced.md →
details.md` reliably loses whatever is in `details.md`.

For any reference file over ~100 lines, put a table of contents at the top. Then a
partial read still reveals the full scope of what's in the file.

```markdown
# API Reference

## Contents
- Authentication and setup
- Core methods (create, read, update, delete)
- Batch operations and webhooks
- Error handling patterns
```

## Progressive disclosure

| Stage | Loaded | Budget |
|---|---|---|
| Metadata | `name` + `description`, all skills, at startup | ~100 tokens each × N |
| Instructions | Full `SKILL.md` body on activation | <5,000 tokens recommended |
| Resources | `scripts/`, `references/`, `assets/` on demand | Files cost when read; scripts cost only their output |

Two clarifications the shorter descriptions of this omit:

**N matters.** Metadata is resident on every turn. Fifty skills is a fixed ~5k-token
tax and, more importantly, fifty descriptions competing for one match.

**Progressive disclosure controls loading, not correctness.** It guarantees unread
files are free. It does not guarantee the agent read the right file. Where a misfire
is expensive, disable automatic invocation and require an explicit call —
Claude Code's `disable-model-invocation: true` removes the skill from the resident
listing entirely, costing zero tokens and firing only when a human types `/name`.

## Validation

Official validator:

```bash
npx skills-ref validate ./my-skill
```

It checks frontmatter validity and naming rules — a syntax check, not a quality
check. It cannot see corpus-level problems, which is where real failures live:

```bash
python3 -m capsule.cli lint --index capsule-index.json
```

`lint` adds description-budget truncation risk, trigger-phrase collisions across the
whole corpus, OWASP AST10 starter rules, and a lethal-trifecta detector. No
per-skill validator can perform the first two by construction.

## Host divergence summary

Write to the spec; know where you're leaving it.

| | Spec | Claude Code | Claude API |
|---|---|---|---|
| `name` | Required, matches dir | Optional, defaults to dir | Required |
| `description` cap | 1,024 (reject) | 1,536 combined (truncate) | 1,024 (reject) |
| Reserved words | none | — | rejects `anthropic`, `claude` |
| Distribution | folder | filesystem / plugin | upload via `/v1/skills` |
| Network at runtime | unspecified | full | **none** |
| Package install | unspecified | local only | **none** |
| Extra frontmatter | — | `when_to_use`, `disable-model-invocation`, `user-invocable`, `disallowed-tools`, `paths`, `model`, `effort`, `context: fork`, `hooks`, `arguments` | — |

Unknown frontmatter keys are ignored rather than rejected, so host extensions are
safe to include in a portable skill. The reverse is not true: a skill that *depends*
on `paths` to avoid over-triggering will over-trigger everywhere else.

### Where skills live

Locations and precedence are host-defined. For Claude Code:

| Scope | Path | Applies to |
|---|---|---|
| Enterprise | managed settings | Everyone in the org |
| Personal | `~/.claude/skills/<name>/SKILL.md` | All your projects |
| Project | `.claude/skills/<name>/SKILL.md` | This repo — **commit this one** |
| Plugin | `<plugin>/skills/<name>/SKILL.md` | Where the plugin is enabled |

**Precedence on a name collision: enterprise > personal > project.** Note the
direction — a contributor's personal `~/.claude/skills/code-review/` silently beats
the one you committed, with no warning to either of you. If a skill must not be
overridable, ship it in a plugin: plugin skills are namespaced `plugin:skill` and
cannot collide.

Two more that surprise people:

- `--add-dir` / `/add-dir` **does** load `.claude/skills/` from the added directory.
  The `permissions.additionalDirectories` setting does **not**.
- Cloud and Cowork sessions never read `~/.claude/skills/` from your machine. A
  personal-only skill reports as not-found when a scheduled run invokes it. Commit
  it or ship it in a plugin.

## Security

Installing a skill is installing software that runs with your credentials. It
supplies instructions *and* code to an agent already holding your filesystem, your
shell, and your tokens.

The threat is not only `scripts/`. `SKILL.md` prose is instruction, and the
`description` sits in the system prompt on every turn — a hostile description is a
persistent injection that fires before anyone invokes anything.

Audit checklist for any skill you did not write:

- Read **every** file, not just `SKILL.md`.
- Flag network egress. A skill that fetches a URL at runtime imports whatever that
  URL serves today, which is not what you audited.
- Flag reads outside the skill directory and working tree — `~/.ssh`, `~/.aws`,
  `.env`, credential stores.
- Check that the stated purpose accounts for every capability present. A formatter
  that needs `curl` is not a formatter.
- Pin it. Vendor into your repo at a known revision; don't track someone's `main`.
- Constrain it. `disallowed-tools` and `paths` narrow blast radius without needing
  to trust the body.

For registry skills, `capsule registry` applies a trust gate that takes the **worst**
verdict across Gen Agent Trust Hub, Socket and Snyk rather than a majority — a
skill rated Safe by two providers and Critical by the third is blocked. Install
count is not safety evidence, and a pending audit is not a pass. See
`references/trust.md`.

There is no safe-by-default posture available at the skill layer. The host's
permission rules and hooks are the only mechanisms that bind regardless of what the
body says — which is what [`capsule harness`](capsule.md#harness) generates.
