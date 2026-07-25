# Capsule architecture

## Module map

| Module | Responsibility | Never does |
|---|---|---|
| `schema.py` | `SourceRecord` / `RunContext` data model, JSON round-trip | touch the filesystem |
| `policy.py` | deny-by-default gates, license classification, audit log | mutate anything |
| `discover.py` | walk roots in contract order, condense to records | write, or fail hard on one bad file |
| `router.py` | intent/domain classify, shortlist, full-body rerank | select without a rationale |
| `reconstruct.py` | rebuild packs, emit provenance, zip archives | reconstruct past a denied gate |
| `validate.py` | enforce the SKILL.md upload contract | repair a pack silently |
| `cli.py` | argument parsing, exit codes | contain business logic |

## Data flow

```
roots ──> discover ──> RunContext (records)
                            │
                            ├──> route(task) ──> Routing{selected, rationale, considered}
                            │
                            └──> reconstruct(record) ──┬─> Policy.can_reconstruct  [license gate]
                                                       ├─> Policy.can_write        [path gate]
                                                       ├─> copy tree verbatim
                                                       ├─> PROVENANCE.md
                                                       └─> validate_pack
```

The index is the only thing that crosses between phases. It is JSON, so a run is
replayable: capture `capsule-index.json` and the same task routes the same way.

## Discovery order

Fixed by contract, and the reason `discover()` uses one ordered walk rather than
per-type globs:

1. root instruction files (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`, ...)
2. nested scoped instruction files (same names, greater depth, scope `scoped`)
3. docs and architecture notes (`.md` under `docs/`, `adr/`, `rfcs/`)
4. skill folders (any directory containing `SKILL.md`)
5. trusted external skill references (roots under `/mnt/skills`, scope `external-trusted`)
6. tooling shortcuts and workflow notes (slash commands and script invocations
   scraped from bodies into `shortcuts`)

## Routing: why two stages

Selecting from the condensed index alone is cheap but wrong often enough to
matter — descriptions overlap heavily in this corpus (`pdf` vs `pdf-reading`,
`file-expenses` vs `benepass-reimbursement`). So stage one shortlists on index
signal, and stage two reads each shortlisted `SKILL.md` in full and rescores.

Stage two contributes three things stage one cannot:

- **Body evidence.** Distinct task tokens actually present in the body.
- **Coverage tie-break.** When two skills mention the same generic words, the one
  matching more of the task's distinctive tokens wins.
- **Disclaimers.** A body saying "do not use this for X" loses points for X.
  This needs at least two overlapping tokens, excluding the skill's own name —
  a one-token overlap produced a false penalty against `skill-creator`, whose
  body mentions not using a *different* testing skill.

`Routing.reranked` records whether stage two changed the ordering.

## Known sharp edges

- **Substring matching is a trap.** `"art" in "party"` routed a grocery order
  into the visual-design domain. All keyword matching goes through `_mentions()`,
  which enforces word boundaries and tolerates simple plurals. Never reintroduce
  bare `in` checks on keyword lists.
- **Confidence is provenance, not quality.** It reports how much of a record was
  read versus inferred. A hand-written skill with no frontmatter scores low even
  if it is excellent.
- **Category inference is first-match-wins.** Order in `_CATEGORY_RULES` is
  semantic; reordering it reclassifies the corpus.

## Extension points

- New source type: add to `SOURCE_TYPES`, emit from `discover()`.
- New policy rule: add a `can_*` method returning `Decision`; it self-logs.
- New routing signal: add to `_stage2` so it can overturn the index, not `_stage1`.
