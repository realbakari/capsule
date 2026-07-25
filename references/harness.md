# Harness integration: enforcing earlier than the diff

`capsule verify` checks a change after it exists. That is a real gate, and it was
the only one Capsule had — but it is also the last one available. The edit already
landed, the turn was already spent, and someone has to read the failure and go
back. The host harness exposes two earlier points, and a contract translates into
both.

## Targets: the permission models are not the same

`capsule harness --target` emits for one host at a time, because the two
permission models are genuinely different and a file written for one does
nothing on the other.

| | Claude Code | Managed Agents |
|---|---|---|
| Policies | `allow` / `ask` / `deny` | `always_allow` / `always_ask` — **no deny** |
| Granularity | command pattern: `Bash(npm install *)` | the tool: `bash` |
| Removing a capability | a deny rule | disable the tool entirely |
| Content-shaped bans | `PreToolUse` hook | no interception point |
| Defaults | rules you write | agent toolset allows; MCP toolsets ask |

The consequence for a contract is a real loss of fidelity, and the emitted
artifact records it rather than leaving it to be discovered. A prohibition on
`npm install` becomes, on Managed Agents:

```json
{"name": "bash", "permission_policy": {"type": "always_ask"}}
```

That is broader than intended — it pauses before *every* shell command, not
just the prohibited one — and weaker, because a human can approve it. It is
still worth emitting: an approval prompt in front of the shell is a real
control and the strongest this host has. It is not a deny rule, and Capsule
does not call it one. To remove a capability outright here, disable the tool
rather than setting a policy on it.

Content-shaped prohibitions (`SOLID`, `•`) have no equivalent at all on this
host and are listed under `_capsule.unenforced`. They remain verify-only.

One default worth knowing: **MCP toolsets default to `always_ask`** while the
agent toolset defaults to `always_allow`. The stated reason is that new tools
appearing on an MCP server should not execute without approval — the same
reasoning behind Capsule's own registry trust gate.

## Enforcement ordering

Weakest to strongest:

| Mechanism | When it acts | What it costs to violate |
|---|---|---|
| Skill body says it | never, mechanically | nothing — a hint the model may act on |
| `capsule verify` | after the diff exists | a failed commit and a round trip |
| `PreToolUse` hook | before the write lands | one blocked tool call |
| Permission deny rule | before the command runs | nothing runs at all |

Everything Capsule did before this lived on row two. The lower two rows are where
enforcement actually belongs.

## The split, and why the corpus forces it

A prohibition is either **command-shaped** or **content-shaped**, and each goes to
a different mechanism:

- **Command-shaped** (`npm install`, `pip install`, `cat`, `head`) → a `deny`
  permission rule. Harness rules match command patterns, so `npm install` becomes
  `Bash(npm install *)` and the command simply never runs.
- **Content-shaped** (`SOLID`, `yaml.load`, `•`, `\n`, `fonts.googleapis.com`) →
  a `PreToolUse` hook on `Write|Edit`. A permission rule cannot see file contents,
  but the hook receives the pending content and can block the call.

This is not a design preference. Across the mounted corpus the prohibitions split
**5 command-shaped to 31 content-shaped**, so covering the set requires both paths.
`docx` alone needs both: it bans `npm install` (a command) and `SOLID`, `•`, `\n`
(content).

## Usage

```bash
capsule harness --skill docx --dest ./.claude          # write the artifacts
capsule harness --task "build a Word report" --dry-run # preview them
```

One merge into the file the project owns, and everything generated under a
single directory that is safe to delete wholesale:

```
merged into .claude/settings.json  (2 deny rule(s) total)
wrote .claude/capsule/hooks.json
wrote .claude/capsule/capsule-hook.py
wrote .claude/capsule/README.md
```

`settings.json` is **merged, never overwritten**: deny lists are unioned, the
project's `allow`, `env` and own denials are untouched, and the rules Capsule
manages are listed under `_capsule.managed_deny` so re-running is visibly
idempotent. An earlier version overwrote the file — which deleted a project's
own `Bash(rm -rf *)` denial while installing a security control.

Emission goes through the same write gate as everything else — pointing `--dest`
at a read-only mount is refused, not warned about.

## Only deny rules are generated

Never `allow`. Capsule's policy escalates and never loosens, and generating an
allow rule from a regex match over prose would widen a user's permissions on the
strength of a pattern. If that inference is wrong, the failure mode is a silently
broadened permission — the worst available direction to be wrong in.

## The hook fails open, deliberately

Hook payload field names are harness- and version-specific. The generated script
probes several plausible names (`content`, `new_string`, `new_str`, `text`,
`replacement`) and, when it recognises none, **allows the call and says why on
stderr**:

```
capsule-hook: no content field recognised in tool_input
(looked for content, new_string, new_str, text, replacement); allowing.
Run with CAPSULE_HOOK_DEBUG=1 and update CONTENT_KEYS.
```

Failing closed would be more secure in the narrow sense and worse in practice: a
hook that blocks every edit after a payload change gets deleted by whoever is
trying to work, and then it enforces nothing at all. An unparsable event is
treated the same way.

Verify the field names against your harness version before relying on this. The
same reasoning is why the block message says what it is:

```
Blocked by the docx skill contract in src/report.js:
  - introduces 'SOLID': **Table shading:** use `ShadingType.CLEAR`, never `SOLID`.

This is a rule from the skill pack governing this task, not a permission problem.
Revise the content or ask the user to override.
```

A block that reads as a permission error invites a workaround. One that names the
governing rule invites a fix.

## Untrusted input tiers

Treat every retrieved result as untrusted input. Retrieval mode bounds the
exposure, so Capsule tiers skills by how they acquire external content:

| Tier | Meaning |
|---|---|
| `disabled` | no external fetching; nothing to inject through |
| `indexed` | a maintained index narrows what an attacker can place in the path |
| `live` | arbitrary pages fetched at runtime; fully open |

The middle tier is the one worth naming explicitly: a cached or indexed path
**lowers prompt-injection risk without removing it**, so `indexed` is neither safe
nor equivalent to live fetching.

Tiering measures **ingestion, not intent**. A skill whose entire purpose is
fetching web content is legitimate and still lands in `live` — that is the point.
Legitimacy is not the same as low exposure. Across this corpus: **32 `disabled`,
2 `live`** (`mcp-builder`, `morning`), 0 `indexed`. `capsule lint` reports the
`live` ones.

## What this does not do

- **It does not make Capsule a sandbox.** Deny rules and hooks are harness
  features; OS-level isolation is separate and Capsule replaces none of it.
- **It does not cover the advisory 84%.** Only mechanically checkable obligations
  translate. Taste stays unenforceable at every layer.
- **It does not verify the harness applied them.** Capsule writes the files; it
  cannot confirm the harness loaded them or that a hook fired.
- **Command patterns are approximate.** `Bash(npm install *)` matches the common
  form. A prohibition evaded by an unusual invocation is not caught, and this is
  a quality gate, not a security boundary.
