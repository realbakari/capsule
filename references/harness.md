# Harness integration: enforcing earlier than the diff

`capsule verify` checks a change after it exists. That is a real gate, and it was
the only one Capsule had — but it is also the last one available. The edit already
landed, the turn was already spent, and someone has to read the failure and go
back. The host harness exposes two earlier points, and a contract translates into
both.

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

Four files, and the command reports what each one buys:

```
wrote .claude/settings.json            [1 command prohibition(s) -> deny rules (never run)]
wrote .claude/hooks/hooks.json         [wires the checker into PreToolUse for Write|Edit]
wrote .claude/capsule-hook.py          [3 content prohibition(s) -> blocked before the write lands]
wrote .claude/.claude-plugin/plugin.json [makes the pack installable as a single-skill plugin]
```

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
