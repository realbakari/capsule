# Customizing Capsule

`capsule.toml` is the single customization surface. Copy it, edit it, pass it
with `--config`. A missing file means built-in defaults, so you only declare what
you want to change.

## Config sections

```toml
[discover]
roots = ["/mnt/skills/public", "/mnt/skills/examples"]

[policy]
writable_roots = ["/home/claude", "/mnt/user-data/outputs"]
readonly_roots = ["/mnt/skills", "/mnt/user-data/uploads"]
allow_restricted_reconstruction = false   # each flip is audited individually
allow_unaudited_registry_skills = false
use_default_rules = true                   # keep the OWASP AST10 starter rules

[routing]
min_score = 1.5
shortlist_size = 4
description_budget = 12000                  # truncation early-warning line
```

## Taxonomy: categories, intents, domains

These three vocabularies decide how your corpus is labelled and how a task is
classified. They used to be hardcoded Python tables tuned against one example
corpus, which is the main reason Capsule degraded on skills it had not seen.
New skills appear daily; a fixed keyword table describes the corpus its author
owned.

```toml
[taxonomy]
extend_defaults = true      # false drops the built-ins entirely

[[taxonomy.category]]
name = "lens-runtime"
keywords = ["lens", "lens studio", "spectacles"]

[[taxonomy.domain]]
name = "lens"
keywords = ["lens", "spectacles", "snapchat"]
```

Declared entries are consulted first. For a workspace that lives in one domain,
`extend_defaults = false` is usually right — there, `spreadsheet` and `commerce`
are pure noise and can only mislabel.

**Domains are also derived from your index automatically.** Any name token
shared by two or more skills becomes a domain, so a workspace of
`specs-websocket` / `specs-depth` / `specs-asr` yields a `specs` domain with
nothing declared. On a 62-skill Lens Studio corpus this lifts domain
classification from 1 task in 10 to 7. Declare entries when you want a label
the names do not spell out — that `lens` and `spectacles` are one subject, for
instance.

**Categories demand corroboration.** A keyword hit in the skill *name* is
strong; a single hit in the description is not. One incidental word made a
debugger `commerce` ("Returns diagnostics") and an API reference `writing`
("documentation reference"). Below two points of evidence the label is
`general`, which is the same posture as the rest of Capsule: refuse rather than
guess.

All keyword matching is word-boundary matched. Bare substring matching is the
documented trap — `form` is inside `performance`, `cart` inside `cartesian` —
and it lives in exactly one function now, `taxonomy.mentions`.

## Custom rules

Rules match on record fields and skill body text, then act. Matchers are ANDed —
every one you specify must match. Actions, weakest to strongest: `flag`,
`approval`, `deny`.

```toml
[[rules]]
id = "org-no-phone-calls"
action = "approval"
applies_to = "skill"
reason = "outbound telephony has cost and consent implications"
body_regex = "(place a call|dial |outbound call)"
```

Available matchers: `applies_to` (source type), `name_regex`, `body_regex`,
`path_prefix`, `license_class`, `trust_verdict`, `category`, `min_installs`,
`max_installs`. A rule with no matchers never fires — that is treated as an
authoring error, not a catch-all, so you cannot accidentally gate the whole
corpus.

**Rules escalate only.** A rule can turn an allowed record into a denied one. It
can never clear a denial issued by a built-in gate (license, trust, path).
Loosening is the job of the override flags, which are audited one at a time. A
hostile config can make Capsule refuse to work; it cannot make Capsule permissive.

### The OWASP AST10 starter rules

On by default (`use_default_rules = true`). These map to the OWASP Agentic Skills
Top 10 and are **triage signals, not verdicts** — pattern matching misses
language-level attacks entirely, so a clean lint means "nothing obvious", never
"safe":

| Rule | Action | Catches |
|---|---|---|
| `ast02-remote-fetch-execute` | deny | `curl ... \| bash` supply-chain execution |
| `ast03-credential-paths` | approval | references to `.ssh`, `.aws`, `.env`, keys |
| `ast03-identity-file-write` | deny | writes to `SOUL.md`/`MEMORY.md`/`AGENTS.md` |
| `ast04-hidden-html-directives` | approval | long HTML comments (invisible instructions) |
| `ast05-unsafe-yaml-load` | deny | `yaml.load` without `SafeLoader` |
| `ast07-unpinned-dependency` | flag | `pip/npm install` without a pinned version |
| `ast09-destructive-shell` | deny | unguarded `rm -rf` on a real path |
| `ast03-memory-write` | approval | writes under `/memories`, the agent's durable store |
| `ast03-memory-path-traversal` | deny | `/memories/../../secrets.env` and the `%2e%2e` form |

The two memory rules split deliberately. Writing memory is legitimate and
common — but `/memories` is re-read at the start of every later session, so
anything stored there is durable instruction storage and wants a human look
once. Escaping it is not legitimate: the memory tool's implementation guidance
requires handlers to reject those paths, so a skill that constructs one is
attacking a control that is supposed to exist.

## Programmatic rules

For logic a regex cannot express, register a Python callable:

```python
from capsule.policy import Policy
from capsule.rules import RuleHit, ACTION_DENY

def no_gpl(record, body):
    if "GPL" in body and record.source_type == "registry":
        return RuleHit("no-gpl", ACTION_DENY, "GPL skills need legal review")
    return None

policy = Policy()
policy.add_rule(no_gpl)
```

Programmatic rules are **imported and registered by you, in code** — never loaded
from a path. This is deliberate: auto-importing rule files from a repo is exactly
the CVE-2025-59536 execution surface, where opening an untrusted project runs its
config. A rule that raises fails closed (denies), never open.

## Precedence

The skill format has no way to say "skill B is a narrower case of skill A", so
overlapping skills from unrelated authors compete arbitrarily. Declare the
relationship locally:

```toml
[[routing.precedence]]
prefer = "pdf-reading"
over = "pdf"
when = "extract"          # only when the task mentions this word
reason = "pdf owns creation and forms; pdf-reading owns extraction"
weight = 2.0              # advisory by default; raise to make authoritative
```

The default weight settles near-ties without overruling a decisive match. On
"fill out this PDF form", `pdf` beats `pdf-reading` by 2.40 and the default 2.0
nudge correctly leaves that alone. Raise `weight` past the margin to make the
relationship binding. `Routing.precedence_applied` records every rule that fired,
whether or not it changed the outcome.

## Diagnostics: `capsule lint`

Runs five things no per-skill validator can:

- **Rule + trifecta scan** over every skill body and its scripts.
- **Agent tool grants** — least privilege over `agents/*.md`. The defect this
  finds is omission: a definition that names no `tools:` inherits every tool the
  host allows, and the omission looks like a blank line rather than a grant. In
  the installed marketplace corpus that is 12 of 24 agents.
- **Description quality** — per skill, the two defects that stop a skill firing:
  a description written in first or second person (the text is injected into a
  system prompt, and mixed point-of-view degrades discovery), and a description
  that states what the skill does but never when to use it. Under-triggering is
  the common failure, so a missing trigger clause is a real defect rather than a
  style note.
- **Description budget** — total description size vs. the truncation line, naming
  which skills sit past it. Silent truncation stops skills firing with no error.
- **Trigger overlap** — pairwise trigger-vocabulary collisions, the main driver
  of routing ambiguity. Compares tokens, not whole phrases, so it catches real
  vocabulary overlap rather than trivially-distinct strings.

The trigger-clause matcher is deliberately generous. It was tightened after
running against the installed marketplace corpus, where `Use whenever the user
plugs in...` was flagged as having no trigger clause — a check that fails a
correctly-written description teaches authors to route around it.

See `references/limitations.md` for what each diagnostic can and cannot tell you.
