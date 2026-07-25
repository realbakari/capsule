# Agent Skills

A standardized way to give an agent new capabilities and expertise — and, in this
repo, a way to govern them.

---

## What is a skill

A skill is a folder with a `SKILL.md` in it.

```text
my-skill/
├── SKILL.md          # required: metadata + instructions
├── scripts/          # optional: executable code
├── references/       # optional: documentation
└── assets/           # optional: templates, resources
```

`SKILL.md` holds YAML frontmatter — `name` and `description` at minimum — followed
by markdown instructions. That is the entire format. Everything else is ordinary
files the agent may choose to open.

[Agent Skills](https://agentskills.io) is an open standard, originally developed by
Anthropic and now implemented by Claude Code, Cursor, GitHub Copilot, VS Code,
OpenAI Codex, Gemini CLI, opencode, Goose, Amp, Kiro, Factory, and others. A skill
you write once is portable across all of them.

## Why bother

Agents are capable and under-informed. They do not know your table schemas, your
release procedure, or the rule about excluding test accounts. Skills package that
procedural knowledge into version-controlled folders loaded on demand:

- **Domain expertise** that would otherwise be pasted into chat every time.
- **Repeatable workflows** — multi-step procedures that run the same way twice.
- **Cross-product reuse** — one folder, any skills-compatible agent.

The honest framing: a skill is a prompt fragment with a filesystem and a trigger
condition. Its power comes from *not* being loaded until it is needed, and its
failure modes all come from that same trigger being a fuzzy match rather than a
function call.

## How they work

Loading happens in three stages, commonly called progressive disclosure:

| Stage | What loads | Cost |
|---|---|---|
| **Discovery** | `name` + `description` of every skill, at startup | ~100 tokens each, **× N**, every turn |
| **Activation** | The full `SKILL.md` body, when the description matches | Recommended under 5k tokens / 500 lines |
| **Execution** | Referenced files, if opened. Scripts run via bash | Files cost when read; scripts cost only their output |

Two things the usual description of this gets wrong, and which you should design
around:

**The context penalty is real and linear.** Fifty skills is roughly 5k tokens of
resident listing carried on every request before anyone types anything. Tokens are
the smaller problem — fifty descriptions competing for the same match degrade
selection quality well before they degrade your budget.

**Loading is not relevance.** The mechanism guarantees unread files cost nothing. It
does not guarantee the *right* file was read. That is a fuzzy match over your
description text, and it is wrong often enough that any skill whose misfire is
expensive should be invoked explicitly rather than automatically.

Anthropic's own guidance calls this **recall degradation** and gives no safe
number, because it depends on how well your descriptions separate. The only two
hard limits published are per-surface, not per-corpus:

| Surface | Cap |
|---|---|
| Claude API | **8 skills per request** |
| Managed Agents | **500 per session**, across all agents; more slows sandbox start |
| Claude Code, claude.ai | none — recall degrades before anything rejects you |

The API cap is the one that forces the issue: past eight skills, something has to
choose which eight go in. That is routing, whether you do it deliberately or not.

## Governance, and what it maps to

Anthropic's [enterprise guidance](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/enterprise)
lists the controls an organization needs before deploying skills at scale. Most
of them are mechanical, and each has a command:

| Control | Command |
|---|---|
| Triggering accuracy — fires when it should, stays quiet otherwise | `capsule route` as an assertion |
| **Coexistence** — a new skill doesn't steal another's triggers | `capsule lint` (trigger collisions) |
| Recall limits — metadata competing in the system prompt | `capsule lint` (description budget) |
| Risk-tier review — credentials, network, code execution | `capsule lint` (AST10 rules, lethal trifecta) |
| Least privilege on agent definitions | `capsule lint` (tool grants) |
| Integrity verification — checksums of reviewed skills | `content_hash` per record; `capsule audit` |
| Instruction following | `capsule contract` · `capsule verify` |

Three controls it does **not** cover, stated plainly: output quality needs a
human or a model judge; separation of duties is a process, not a check; and
signature verification is [not implemented](references/limitations.md) — Capsule
records provenance, it does not cryptographically verify it.

## Where Capsule fits

Skills have no compiler. Nothing checks that your descriptions don't collide, that
the right pack was chosen, or that the agent honoured the pack it loaded. Capsule is
that missing layer:

| Question | Command |
|---|---|
| What skills exist here? | `capsule index` · `capsule show` |
| Which one applies to this task, and why? | `capsule route --task "..."` |
| Do my descriptions collide or truncate? | `capsule lint` |
| Is this skill well-calibrated for current models? | `capsule doctor` |
| Can I safely rebuild/redistribute it? | `capsule reconstruct` · `capsule audit` |
| Should I install this one from the registry? | `capsule registry` |
| Did the agent actually follow the skill? | `capsule contract` · `capsule verify` |
| Can I stop the violation before it happens? | `capsule harness` |

Start with [skills/capsule.md](skills/capsule.md) if you already know the format.

## Guide

| Page | Covers |
|---|---|
| [Quickstart](skills/quickstart.md) | Build a working skill, then govern it. ~10 minutes. |
| [Specification](skills/specification.md) | The format: frontmatter fields, constraints, directory rules, and where hosts diverge from the standard. |
| [Descriptions](skills/descriptions.md) | The highest-leverage 1,024 characters you will write. Triggering, collisions, truncation. |
| [Authoring](skills/authoring.md) | Conciseness, degrees of freedom, workflows, feedback loops, anti-patterns. |
| [Scripts](skills/scripts.md) | When to ship code instead of prose, and how to write it so an agent can use it. |
| [Evaluating](skills/evaluating.md) | The five evaluation dimensions, CI gates, and which of them are mechanically checkable. |
| [Capsule](skills/capsule.md) | Every command, what it prints, and when to reach for it. |

## Getting skills

| Source | Install |
|---|---|
| [anthropics/skills](https://github.com/anthropics/skills) | `/plugin marketplace add anthropics/skills` then `/plugin install document-skills@anthropic-agent-skills` |
| [skills.sh](https://www.skills.sh/) — the open directory | `npx skills add <owner/repo>` |
| Your own | `.claude/skills/<name>/SKILL.md`, committed to the repo |

Both public sources are unvetted by default. Run
`capsule registry --query <term>` before installing anything from skills.sh — it
aggregates Gen Agent Trust Hub, Socket and Snyk verdicts by taking the **worst**
report, and install count is not safety evidence. See
[Security](skills/specification.md#security) and `references/trust.md`.

## Reference

| | |
|---|---|
| [agentskills.io](https://agentskills.io) | The open standard |
| [Specification](https://agentskills.io/specification) | Normative format definition |
| [skills-ref](https://github.com/agentskills/agentskills/tree/main/skills-ref) | Official validator — `skills-ref validate ./my-skill` |
| [Claude Code skills](https://code.claude.com/docs/en/skills) | Host extensions: subagents, hooks, invocation control |
| [Skills with the Claude API](https://platform.claude.com/docs/en/build-with-claude/skills-guide) | Upload, `skill_id`, container config |
| [Skills for enterprise](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/enterprise) | Risk tiers, review checklist, lifecycle, recall limits |
| [Skills in Managed Agents](https://platform.claude.com/docs/en/managed-agents/skills) | Attaching by `skill_id`, version pinning, the 500-per-session cap |
| [Authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) | Descriptions, progressive disclosure, degrees of freedom |
| [anthropics/skills](https://github.com/anthropics/skills) | Reference skills and the `spec/` + `template/` directories |

---

> Portions of this guide are derived from Anthropic's Agent Skills documentation and
> the agentskills.io specification. Restructured, corrected, and extended for
> developers committing skills into a codebase. Errors in the additions are ours.
