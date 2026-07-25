# Scripts

When to ship code instead of prose, and how to write code an agent can actually use.

Skip this page if your skill is markdown only.

---

## Why bundle a script at all

The model could write the code. Ship it anyway when any of these hold:

- **Reliability** — a pre-written validator behaves identically every run; generated
  code does not.
- **Token cost** — the script's *source never enters context*. Only stdout and
  stderr do. A 400-line validator costs you one line of output.
- **Latency** — no generation step.
- **Consistency** — every user of the skill gets the same behavior.

The token point is the one people underuse. Bundling comprehensive tooling is
close to free; bundling comprehensive *prose* is not.

## Execute or read?

Say which, explicitly. This is the single most common source of confusion in
script-bearing skills.

```markdown
Run `scripts/analyze_form.py` to extract fields.        ← execute
See `scripts/analyze_form.py` for the extraction algorithm.  ← read as reference
```

Execution is right for most utility scripts. Reading is right only when the agent
needs to reimplement or adapt the logic.

## Solve, don't defer

A script that throws its problem back to the agent has wasted its main advantage:
determinism. Handle the error conditions you can anticipate.

```python
def process_file(path):
    """Process a file, creating it if absent."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        print(f"File {path} not found, creating default", file=sys.stderr)
        with open(path, "w") as f:
            f.write("")
        return ""
    except PermissionError:
        print(f"Cannot access {path}, using default", file=sys.stderr)
        return ""
```

Not:

```python
def process_file(path):
    return open(path).read()   # let the agent figure out the traceback
```

The second version turns a deterministic step back into a judgement call, which is
what you were trying to avoid.

## No voodoo constants

Every configuration value should justify itself. If you don't know why it's 47, the
agent certainly doesn't, and it will change it.

```python
# HTTP requests typically complete within 30s;
# the longer timeout accounts for slow connections
REQUEST_TIMEOUT = 30

# Three retries balances reliability against latency —
# most intermittent failures clear by the second retry
MAX_RETRIES = 3
```

Not `TIMEOUT = 47` and `RETRIES = 5` with no comment.

## Verbose, specific error messages

The error message is the interface between your script and the agent's next action.
Make it actionable.

```text
bad:   ValidationError: invalid field
good:  Field 'signature_date' not found.
       Available fields: customer_name, order_total, signature_date_signed
```

The second one gets fixed on the next turn. The first one produces guessing.

## Document the contract

Give each script a name, an invocation, and an output shape:

````markdown
## Utility scripts

**`scripts/analyze_form.py`** — extract form fields from a PDF

```bash
python scripts/analyze_form.py input.pdf > fields.json
```

Output:
```json
{
  "field_name": {"type": "text", "x": 100, "y": 200},
  "signature":  {"type": "sig",  "x": 150, "y": 500}
}
```

**`scripts/validate_boxes.py`** — check for overlapping bounding boxes

```bash
python scripts/validate_boxes.py fields.json
# prints "OK" or lists conflicts
```

**`scripts/fill_form.py`** — apply values to a PDF

```bash
python scripts/fill_form.py input.pdf fields.json output.pdf
```
````

## Plan → validate → execute

For batch, destructive, or high-stakes operations, have the agent write a plan to a
file, validate the plan with a script, and only then execute.

Updating 50 PDF form fields from a spreadsheet, without validation, the agent can
reference fields that don't exist, produce conflicting values, or miss required
ones — and you find out after the file is written.

```text
analyze  →  create plan file  →  validate plan  →  execute  →  verify
            (changes.json)       (script)                     (script)
```

Why it works:

- Errors surface before anything is modified
- Verification is machine-checkable, not a judgement
- The agent can iterate on the plan without touching originals
- Failures point at a specific field, not a general failure

Use it for batch operations, destructive changes, complex validation rules, and
anything where the cost of being wrong exceeds the cost of a second pass.

## Runtime constraints

Where the skill runs determines what your script may assume. Getting this wrong is
the most common portability failure.

| | Claude Code | Claude API | claude.ai |
|---|---|---|---|
| Network | Full — as any local program | **None** | Varies by admin setting |
| Package install | Local only, never global | **None** — pre-installed only | npm, PyPI, GitHub |
| Filesystem | Your machine | Sandboxed container | Managed container |

Consequences:

- A script that calls an external API works locally and fails silently on the API.
- `pip install` in a script is fine locally, impossible in the API container.
- Never install globally from a skill — you're modifying someone's machine.

Declare requirements so the mismatch is visible up front:

```yaml
compatibility: Requires Python 3.11+, pdfplumber, and internet access
```

## Visual analysis

When an input can be rendered, rendering it is often better than parsing it:

```markdown
## Form layout analysis

1. Convert to images: `python scripts/pdf_to_images.py form.pdf`
2. Analyze each page image to identify field locations and types
```

You have to write `pdf_to_images.py` — but the layout reasoning that follows is
something vision handles well and coordinate math handles badly.

## Scripts and enforcement

A script is deterministic, but *calling* it is not. "Run `validate.py` before
packing" is a hint the agent may skip, and the failure looks exactly like success
until something downstream breaks.

Extract the checkable commitments and gate the diff:

```bash
python3 -m capsule.cli contract --index capsule-index.json --skill my-skill
python3 -m capsule.cli verify   --index capsule-index.json --skill my-skill --ref=--cached
```

Or push the rules earlier, into the host's own enforcement:

```bash
python3 -m capsule.cli harness --index capsule-index.json --skill my-skill --dest ./.claude
```

Command-shaped prohibitions like `npm install` become permission `deny` rules and
never run at all. See [evaluating.md](evaluating.md) and [capsule.md](capsule.md).

## Checklist

- [ ] Each script says whether to **execute** or **read** it
- [ ] Errors are handled in the script, not deferred to the agent
- [ ] Every constant has a comment justifying its value
- [ ] Error messages name the specific problem and the valid alternatives
- [ ] Invocation and output shape documented in `SKILL.md`
- [ ] Dependencies declared; no global installs
- [ ] Batch or destructive operations use plan → validate → execute
- [ ] Runtime assumptions match every host you target
