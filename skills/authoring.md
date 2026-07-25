# Authoring

Once the skill fires reliably, the body decides whether the output is any good.

---

## Assume the model is already smart

The context window is shared. Your body competes with conversation history,
everything else loaded, and the actual request. Every paragraph should earn its
tokens.

Challenge each piece: *does the model already know this?* Usually yes.

```markdown
## Extract PDF text

Use pdfplumber:

    import pdfplumber
    with pdfplumber.open("file.pdf") as pdf:
        text = pdf.pages[0].extract_text()
```

Not:

```markdown
## Extract PDF text

PDF (Portable Document Format) files are a common file format containing text,
images, and other content. To extract text you'll need a library. There are many
available, but pdfplumber is recommended because it's easy to use and handles
most cases well. First install it with pip. Then you can use the code below...
```

Same information, a third of the tokens. The long version explains PDFs to
something that has read more PDFs than you have.

What *does* justify tokens: your table schemas, your conventions, the rule about
excluding test accounts, the reason step 3 must precede step 2. Facts about your
world, not facts about the world.

## Match freedom to fragility

Specificity is a cost. Pay it where being wrong is expensive.

**High freedom — prose direction.** Multiple valid approaches, context decides.

```markdown
## Code review process

1. Analyze structure and organization
2. Check for bugs and edge cases
3. Suggest readability improvements
4. Verify adherence to project conventions
```

**Medium freedom — a pattern with parameters.** A preferred shape exists, variation
is fine.

```markdown
## Generate report

Use this shape, customize as needed:

    def generate_report(data, format="markdown", include_charts=True):
        ...
```

**Low freedom — an exact command.** Fragile, sequence-critical, or destructive.

```markdown
## Database migration

Run exactly:

    python scripts/migrate.py --verify --backup

Do not modify the command or add flags.
```

The heuristic: an open field with no hazards gets a direction; a narrow bridge with
cliffs gets a railing. Putting railings on the open field is the more common
mistake, and it makes output worse — see [calibration](#calibration) below.

## Calibration

Current-generation models are degraded, not helped, by long enumerated rule lists.
A skill that grew to 3,000 words of edge cases usually performs worse than the
300-word version it started as.

```bash
python3 -m capsule.cli doctor --index capsule-index.json
```

```
setup-writing-style   7024w  behav=91  policy=8  presc=1.3   altitude=brittle
  [medium] progressive-disclosure: 7024w in a single file with 1 supporting file
```

`doctor` checks prescription density, contradictory directives (weighted by how
close together they are), monolithic bodies, example density, and
reasoning-extraction refusal risk.

One deliberate exclusion: **security invariants are not counted as prescription.**
This came out of running the check on Capsule's own pack, which first rated as the
most prescriptive artifact in the corpus almost entirely on lines like "never load
what the audits will not clear". A metric that cannot tell a license gate from a
style rule will tell you to weaken the license gate.

The general rule that falls out of this: absolutes belong in security policy, where
they are invariants. Behavioral guidance should state intent and leave latitude.

## Progressive disclosure in practice

Keep `SKILL.md` under 500 lines. Past that, it is a table of contents, not a manual.

**Pattern 1 — overview with references.**

```markdown
# PDF Processing

## Quick start

Extract text with pdfplumber:

    import pdfplumber
    with pdfplumber.open("file.pdf") as pdf:
        text = pdf.pages[0].extract_text()

## Advanced

**Form filling**: see [references/FORMS.md](references/FORMS.md)
**API reference**: see [references/REFERENCE.md](references/REFERENCE.md)
**Examples**: see [references/EXAMPLES.md](references/EXAMPLES.md)
```

**Pattern 2 — split by domain.** When a skill spans several areas, split so a task
in one area doesn't load the others.

```text
bigquery-skill/
├── SKILL.md              # overview and navigation
└── references/
    ├── finance.md        # revenue, billing
    ├── sales.md          # pipeline, opportunities
    ├── product.md        # API usage, adoption
    └── marketing.md      # campaigns, attribution
```

A question about sales loads `sales.md` and nothing else. Give the agent a way in:

```markdown
## Quick search

    grep -i "revenue"   references/finance.md
    grep -i "pipeline"  references/sales.md
```

**Pattern 3 — conditional depth.** Basic inline, advanced behind a link.

```markdown
## Editing documents

For simple edits, modify the XML directly.

**Tracked changes**: see [references/REDLINING.md](references/REDLINING.md)
**OOXML details**: see [references/OOXML.md](references/OOXML.md)
```

### One level deep

**All reference files link directly from `SKILL.md`.** When a referenced file
references another, agents tend to preview rather than read — `head -100` and
similar — and then act on partial content.

```text
bad:   SKILL.md → advanced.md → details.md   (details.md gets skimmed or missed)
good:  SKILL.md → advanced.md
       SKILL.md → reference.md
       SKILL.md → examples.md
```

For reference files over ~100 lines, open with a table of contents so a partial read
still shows the full scope.

## Workflows

Break complex operations into numbered steps, and for anything with more than about
four, give a checklist the agent can copy and tick off.

````markdown
## Research synthesis

Copy this checklist and track progress:

```
- [ ] Step 1: Read all source documents
- [ ] Step 2: Identify key themes
- [ ] Step 3: Cross-reference claims
- [ ] Step 4: Create structured summary
- [ ] Step 5: Verify citations
```

**Step 1: Read all source documents**
Review each file in `sources/`. Note main arguments and supporting evidence.

**Step 2: Identify key themes**
Look for patterns across sources. Where do they agree or disagree?

...

**Step 5: Verify citations**
Check every claim references the correct source. If citations are incomplete,
return to Step 3.
````

The checklist is not decoration — it is the mechanism that stops step 3 from being
skipped when steps 1, 2 and 4 went smoothly. It works for pure-analysis workflows
with no code at all.

## Feedback loops

**Run validator → fix → repeat.** The single highest-return pattern in skill
authoring.

With code:

```markdown
## Document editing

1. Edit `word/document.xml`
2. **Validate immediately**: `python ooxml/scripts/validate.py unpacked_dir/`
3. If validation fails: read the error, fix the XML, validate again
4. **Only proceed when validation passes**
5. Rebuild: `python ooxml/scripts/pack.py unpacked_dir/ output.docx`
```

Without code — the "validator" is a document, the check is a read:

```markdown
## Content review

1. Draft following `references/STYLE_GUIDE.md`
2. Review against the checklist:
   - terminology consistency
   - examples in standard format
   - all required sections present
3. If issues: note each with a section reference, revise, re-check
4. Only finalize when all requirements are met
```

The step that matters is 4, stated as a gate. "Validate your work" without "do not
proceed until it passes" gets skipped under time pressure.

## Common patterns

### Templates

Match strictness to actual need.

Strict, for formats consumed by something downstream:

````markdown
ALWAYS use this exact structure:

```markdown
# [Analysis Title]

## Executive summary
[One paragraph]

## Key findings
- Finding with supporting data

## Recommendations
1. Specific actionable recommendation
```
````

Flexible, where adaptation helps:

```markdown
Here is a sensible default; use judgment based on the analysis.
Adjust sections for the specific analysis type.
```

Don't write "ALWAYS" unless you mean it. A template marked mandatory that turns out
to be wrong for a case forces a bad choice between following the skill and doing the
job.

### Examples

Where output quality depends on style, show input→output pairs. They convey level of
detail far better than description does.

````markdown
## Commit message format

**Example 1**
Input: added user authentication with JWT tokens
Output:
```
feat(auth): implement JWT-based authentication

Add login endpoint and token validation middleware
```

**Example 2**
Input: fixed dates displaying incorrectly in reports
Output:
```
fix(reports): correct date formatting in timezone conversion

Use UTC timestamps consistently across report generation
```
````

Three examples covering different shapes beat one example plus three paragraphs
about the rules.

### Conditional branches

```markdown
## Document modification

1. Determine the type:
   **Creating new content?** → Creation workflow
   **Editing existing content?** → Editing workflow

2. Creation workflow:
   - Use docx-js, build from scratch, export .docx

3. Editing workflow:
   - Unpack, modify XML, validate after each change, repack
```

If branches grow past a screen each, move them into separate files and have
`SKILL.md` route to the right one.

## Content rules

**No time-sensitive information.** It becomes wrong and nothing tells you.

```markdown
# bad
If you're doing this before August 2025, use the old API.

# good
## Current method
Use the v2 endpoint: `api.example.com/v2/messages`

## Old patterns
<details>
<summary>Legacy v1 API (deprecated 2025-08)</summary>
The v1 API used `api.example.com/v1/messages`. No longer supported.
</details>
```

**One term per concept.** Pick "field" or "box" or "element" and never switch. Pick
"extract" or "pull" or "retrieve" and never switch. Inconsistent vocabulary makes
instructions ambiguous for exactly the reason it does in code.

**Forward slashes in every path**, including on Windows. `scripts/helper.py`, never
`scripts\helper.py`.

**Descriptive filenames.** `form_validation_rules.md`, not `doc2.md`. The agent
decides what to open based on the name.

## Anti-patterns

**Offering too many options.** "You can use pypdf, or pdfplumber, or PyMuPDF, or
pdf2image..." is a decision handed back to the caller. Give a default with an escape
hatch: "Use pdfplumber for text extraction. For scanned PDFs needing OCR, use
pdf2image with pytesseract instead."

**Assuming tools are installed.** State dependencies explicitly, or use
`compatibility:` in frontmatter. This matters more across hosts than it looks — the
Claude API container has no network and no runtime package installation.

**Unqualified MCP tool names.** Use `ServerName:tool_name` —
`BigQuery:bigquery_schema`, `GitHub:create_issue`. Without the prefix the tool may
not be found when several MCP servers are loaded.

**Writing prose that describes an exact command sequence.** That is a script wearing
a markdown costume. Move it → [scripts.md](scripts.md).

## Iterate with the model, test with a fresh one

The effective loop uses two instances. One helps you write the skill; a fresh one
with the skill loaded reveals whether it works.

1. Do the task normally, without a skill. Notice what context you keep supplying.
2. Identify the reusable part — schemas, conventions, the filtering rule.
3. Ask for a skill capturing it. Models understand the format natively; no special
   prompt is needed.
4. Cut what it over-explains. "Remove the paragraph defining win rate — that's
   already known."
5. Restructure for navigation: "Move the table schema to a reference file."
6. **Test in a fresh session.** Give it real tasks, not test scenarios.
7. Bring specifics back: "It forgot to filter test accounts on the regional report.
   The rule is in there — is it prominent enough?"

Watch how it navigates, and treat that as data about your structure:

- **Unexpected read order** → your organization isn't as intuitive as you thought
- **Missed references** → links need to be more prominent
- **Same file read every time** → that content belongs in `SKILL.md`
- **A file never read** → it's unnecessary, or badly signposted

Test with every model you plan to run. Skills are additions to a model, so what is
sufficient for a large model may be too thin for a small one, and what is helpful
for a small model may be noise for a large one. If you need one skill for both, aim
for the intersection.

## Checklist

- [ ] Body under 500 lines; detail in `references/`
- [ ] References one level deep from `SKILL.md`
- [ ] Reference files over 100 lines have a table of contents
- [ ] No time-sensitive statements outside an "old patterns" section
- [ ] One term per concept throughout
- [ ] Examples are concrete input/output pairs, not descriptions
- [ ] Workflows have numbered steps and an explicit "do not proceed until" gate
- [ ] Freedom matches fragility — railings only on the narrow bridges
- [ ] Forward slashes everywhere; MCP tools fully qualified
- [ ] `capsule doctor` reports no `brittle` altitude or high-severity findings
