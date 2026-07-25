# Run context reference

## Required fields

Every `SourceRecord` carries all eleven. None are optional; routing and policy
both read fields that look decorative.

| Field | Meaning |
|---|---|
| `source_type` | `skill` / `instruction` / `doc` / `manifest` / `config` / `tooling` |
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

`license_class`, `reconstructable`, `body_words`, `aux_dirs`, `content_hash`.
These exist so a policy decision can be replayed from the index alone, without
re-reading the filesystem.

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
