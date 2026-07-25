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

Runs three things no per-skill validator can:

- **Rule + trifecta scan** over every skill body and its scripts.
- **Description budget** — total description size vs. the truncation line, naming
  which skills sit past it. Silent truncation stops skills firing with no error.
- **Trigger overlap** — pairwise trigger-vocabulary collisions, the main driver
  of routing ambiguity. Compares tokens, not whole phrases, so it catches real
  vocabulary overlap rather than trivially-distinct strings.

See `references/limitations.md` for what each diagnostic can and cannot tell you.
