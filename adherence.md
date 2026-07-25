# Adherence: making a skill bind without the agent reading it

Two complaints survive every other control in Capsule, and they are the ones
people actually hit:

1. **You have to name the skill by hand.** Description matching is weak enough
   that in practice you end up saying "use the X skill" instead of trusting
   activation.
2. **The agent edits the codebase without following the skill.** The pack is
   selected. The pack is loaded. The diff ignores it.

The second is the harder one, and earlier versions of `references/limitations.md`
called it out of reach: *"Capsule turns 'which skill should fire' into a tested
question; it cannot make the model obey the answer."* That was true of the design
at the time and too quick as a conclusion.

## The reframe

You cannot make a model comply. You **can** make compliance a property of the
artifact instead of the attention.

An instruction in a skill body is a hint. A check against the diff is a gate. So
rather than asking harder, extract the skill's checkable commitments into a
**contract** and verify the resulting change against it. If the change violates
the contract it fails — and whether the agent read the pack, skimmed it, or
ignored it becomes irrelevant, because that question no longer determines the
outcome.

This is the same instinct the field reports keep arriving at: fresh-context
verification beats self-critique, and a dedicated reviewer catches what the
implementer missed while focused on the feature. Contracts are that reviewer,
reduced to the part a machine can do reliably.

## What is checkable, and what is not

The dividing line turns out to be crisp. A directive carrying a **code-like
token** is mechanically verifiable; one that does not is taste:

| Directive | Checkable |
|---|---|
| ``Never use `\n` — use separate Paragraph elements`` | yes |
| ``never insert `•` literally`` | yes |
| ``do not run `npm install` first`` | yes |
| ``use `ShadingType.CLEAR`, never `SOLID` `` | yes |
| "Write code that reads like the surrounding code" | no |
| "Never leave a heading ambiguous" | no |

So every contract reports **coverage**: the fraction of a skill's directives it
can actually enforce. Across the 34 mounted skills that is **62 enforceable
obligations against 314 advisory ones — 16% coverage**. Per-skill it ranges from
71% (`xlsx`) and 62% (`docx`) down to 9% (`setup-writing-style`) and 0% for packs
that are pure guidance, which tracks how mechanical the skill is.

That 16% is lower than the first measurement of 35%, and the drop is the point:
fixing the polarity bug below removed 85 obligations that were being extracted
wrongly. A correctness fix that *lowers* your headline number is the kind worth
trusting.

A tool claiming to enforce the other 84% would be lying. The number is printed on
every report for that reason.

## Usage

```bash
# 1. Activation: emit an injectable block instead of hoping the matcher agrees
capsule brief --task "build a Word report generator"

# 2. Inspect what will actually be enforced
capsule contract --skill docx --advisory

# 3. Verify the change. Exit 5 on violation, so this gates a commit.
capsule verify --skill docx --ref --cached
capsule verify --task "build a Word report generator" --repo . --ref main
capsule verify --skill docx --paths dist/report.js   # no VCS needed
```

As a pre-commit hook:

```bash
#!/bin/sh
capsule verify --task "$(git log -1 --format=%s)" --ref=--cached || exit 1
```

## Worked example

Against the real `docx` pack, a change that ignores it:

```
FAIL docx-1: introduces `npm install`, which the skill prohibits
FAIL docx-3: introduces `SOLID`, which the skill prohibits
FAIL docx-4: introduces `•`, which the skill prohibits
FAIL docx-6: introduces `\n`, which the skill prohibits
PASS docx-5: `Paragraph` present

4 violation(s), 1 satisfied, 7 of 8 obligations applicable to this change
contract coverage: 62% (5 directive(s) are advisory and cannot be verified)
```

Exit code 5. The same change with `ShadingType.CLEAR`, no bullet literal, no
`\n`, and no install step: zero violations, exit 0.

## The activation half

`capsule brief` emits a block naming the selected pack, why it was selected, its
enforceable obligations, and the fact that the diff will be checked:

```
<capsule_activation>
Selected skill pack: docx
Location: /mnt/skills/public/docx/SKILL.md
Why: intent=create, domain=document
Read this pack in full before editing. It is the governing guidance for this task.

This change will be verified against the following extracted obligations:
  - must not use `npm install`
  - must not use `SOLID`
  ...

Verification is mechanical and covers 62% of this pack's directives. The
remainder is judgement and is not checked — follow the pack for those.
</capsule_activation>
```

Stating the verification step is not a threat, it is actionable information. A
rule buried at line 200 of a 3,000-word pack competes for attention with
everything else; a short list of what will be checked does not.

## The bug this nearly shipped with

The first extractor applied one polarity to every token in a sentence. On a real
line from the `docx` pack:

```
`docx` is preinstalled — do not run `npm install` first;
write the script and `require('docx')` directly
```

it concluded that **`docx` was prohibited**. Following the skill exactly would
have failed the check — worse than no tool at all, because it would have trained
people to disable it.

The fix: a directive governs from its keyword to the next clause boundary and
never reaches backwards. Two follow-on bugs came out of the same area:

- A bare `[.]` boundary matched the dot **inside** `yaml.load`, truncating the
  clause to `` use `yaml `` and losing the token the rule was about. The period
  now has to be followed by whitespace.
- `_token_hits` excluded a preceding dot, so `ShadingType.SOLID` slipped a ban on
  `SOLID`. Qualified access is still the prohibited constant.

This is the fourth time polarity or boundary blindness has produced a confidently
wrong result in this codebase — after the router's disclaimer penalty, the
health check's reasoning-extraction false positive, and its malware-prohibition
false positive. The lesson has been identical every time: **bound the window, do
not trust co-occurrence.** Three regression tests guard this one, including
`test_corpus_contracts_never_ban_their_own_preinstalled_library`.

## What this does not do

- **It does not verify taste.** 84% of this corpus's directives are advisory. On
  guidance-heavy packs it is 100%: `theme-factory` yields nothing checkable, and
  that is an accurate reading, not a failure.
- **It does not run the skill's own tests.** Contracts check tokens in a diff, not
  behavior. A real test suite remains the stronger signal where one exists.
- **It does not detect semantic non-compliance.** Code can satisfy every token
  rule and still miss the point of the pack.
- **It is not a security control.** Contract verification runs *after* a change
  exists. The trust and license gates are what run before.
- **`require` obligations warn rather than fail.** An "always use X" rule
  asserted against an unrelated diff would produce noise, and noise trains people
  to ignore reports.

## Honest scoring

The adherence gap moves from **not solved** to **mitigated**, with the boundary
stated precisely: mechanically checkable obligations are now enforced at the
diff, at measured coverage; judgement-based guidance is still unenforceable and
labelled as such.
