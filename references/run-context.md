# Run context reference

## Required fields

Every `SourceRecord` carries all eleven. None are optional; routing and policy
both read fields that look decorative.

| Field | Meaning |
|---|---|
| `source_type` | `skill` / `agent` / `instruction` / `doc` / `manifest` / `config` / `tooling` / `registry` |
| `source_path` | absolute path or identifier |
| `name` | frontmatter name, else directory name |
| `category` | inferred domain bucket |
| `purpose` | first substantive prose line, else description |
| `trigger_phrases` | phrases a router should match on |
| `shortcuts` | slash commands and script invocations found in the body |
| `scope` | `root` / `scoped` / `repo` / `external-trusted` |
| `policy_constraints` | e.g. `license:apache-2.0`, `source-tree:read-only` |
| `reload_rules` | when this record must be re-read |
| `confidence` | 0.0–1.0, how much was read vs inferred |

## Derived fields

`license_class`, `reconstructable`, `body_words`, `aux_dirs`, `content_hash`,
`lifecycle`, `tool_grants`, `model`. These exist so a policy decision can be
replayed from the index alone, without re-reading the filesystem.

## Symlinked skill directories

Discovery follows directory symlinks. The standard install layout is built
entirely out of them: `npx skills add` writes one copy under `~/.agents/skills/`
and symlinks it into each host's directory, so `~/.claude/skills/find-skills` is
a link rather than a folder. `Path.rglob` does not traverse symlinked
directories, so indexing a host directory found **nothing** — and exited 0 while
reporting `indexed 0 sources`.

Skills dedup by *resolved* target, so one skill exposed to four hosts is one
record, not four. `source_path` is the canonical path. Cycles are bounded by
remembering resolved directories.

`capsule index` now exits `1` when a run produces no records at all. An empty
index is almost always a pointing error, and every later command reads it
happily: routing finds no candidate, lint finds no problem, and `verify` passes
an empty contract.

## `description` vs `purpose`

Two fields that look interchangeable and are not:

- `description` — the frontmatter description, verbatim. This is what the host
  concatenates into the selection context, so it is the only correct input to
  budget and triggering checks.
- `purpose` — a prose summary read off the body, for human scanning.

Conflating them understated a 62-skill corpus at 2,745 characters against a
12,000 budget — comfortable. Measured correctly it is 18,972, over by half
again, with six skills past the truncation line.

`purpose` skips HTML comments. A licence header written as a comment under the
frontmatter is a common convention, and without that every skill in such a
corpus reports its purpose as `<!--`.

## Agent definitions

Any `.md` directly under an `agents/` directory is indexed as `source_type:
agent`. Agents are governed for the same reason skills are: a `description`
decides when the agent gets delegated to, and `tools:` is an explicit
permission grant.

`tool_grants` accepts both spellings found in the wild — `tools: ["Read",
"Grep"]` and `tools: Glob, Grep, Read` — and normalises them to a list. A
definition that names no tools records `["*"]`, not `[]`: omission means the
agent inherits every tool the host allows, and representing that as an empty
grant would make the most permissive definition in a corpus read as the least.

A file under `agents/` with no parsable frontmatter is **not** recorded. It is
not an agent definition, and indexing it as a zero-permission one would put a
non-entity at the top of any least-privilege report.

## Confidence scoring

Starts at 0.55, then adds: frontmatter parsed (+0.20), description present
(+0.15), license determined (+0.10). Capped at 1.0.

This measures **provenance, not quality**. A skill scoring 0.55 is one Capsule
had to infer almost everything about — not a bad skill.

## Trigger phrase extraction

Descriptions in this corpus are already written as trigger documentation, so
extraction pulls: the skill name (hyphenated and spaced), quoted fragments,
file extensions, and clauses following "use when", "use for", or
"triggers include". Capped at twelve, deduplicated, order preserved.

## Reload rules

| Value | Meaning |
|---|---|
| `always` | instruction files: re-read every run |
| `on-selection: read SKILL.md in full before use` | skills: index entry is never sufficient |
| `on-repo-change` | docs and manifests |
| `on-task-change` | default |

## Rebuild triggers

Rebuild the index when the task changes, the repo changes, instruction files
change, or any `content_hash` drifts. A stale index routing a new task is the
main way the wrong pack gets loaded.
