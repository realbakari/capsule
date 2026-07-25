# Trust model for registry skills

Capsule has two independent gates and they answer different questions:

| Gate | Question | Applies to |
|---|---|---|
| License | may Capsule **rebuild** this? | local sources |
| Trust | may Capsule **run** this? | registry sources |

A remote skill can be permissively licensed and still hostile. Passing one gate
says nothing about the other.

## Evidence that shaped this model

All four observations are from the live skills.sh audit table, not hypotheticals.

**Popularity is not safety.** `find-skills` is the most-installed skill in the
ecosystem at 2.6M installs and carries a Snyk *Medium* risk. Any heuristic that
ranks by install count — including the one `find-skills` itself recommends,
"prefer 1K+ installs" — would clear it without review.

**Source reputation is not safety.** `azure-resource-visualizer` is *High* risk
and ships from Microsoft, a curated first-party publisher.

**Providers disagree, and the disagreement is the signal.** `azure-validate` is
rated Safe by Gen Agent Trust Hub and 0-alerts by Socket, while Snyk rates it
**Critical**. A majority vote clears it 2–1. Capsule takes the worst verdict.

**Pending is not a pass.** Two dozen `lark-*` skills show Pending across all
three providers. An unfinished audit carries exactly as much assurance as no
audit: none.

## Providers

Five now, not the three this model was first written against: **Gen Agent Trust
Hub, Socket, Snyk, Runlayer, ZeroLeaks**. The list is informational —
aggregation takes the worst verdict from whatever arrives, so a provider added
next year is handled without a code change. `KNOWN_PROVIDERS` exists so a
verdict's `providers` can be read against what was expected, not to filter.

Two fields worth keeping: `slug` links to the per-provider detail page, and
`categories` (Agent Trust Hub only, e.g. `["NO_CODE", "SAFE"]`) says what the
provider actually classified.

## Aggregation

Rank every provider on two ladders, take the worst:

```
status:  pass(0) < unknown/pending(1) < warn(2) < fail(3)
risk:    NONE(0) < LOW(1) < MEDIUM(2) < HIGH(3) < CRITICAL(4)
```

| Worst signal | Verdict |
|---|---|
| `fail`, or risk >= HIGH | **deny** |
| `pending` / `unknown` / no audits at all | **deny** |
| `warn`, or risk == MEDIUM | **approval-required** |
| all clear | **allow** |

`dissenting` is set when providers actually disagree. Note that a provider
*omitting* an optional field is not disagreement — Socket reports no
`riskLevel`, and folding that absence into the comparison flagged unanimous
passes as disputes. Dissent compares statuses, and risks only among providers
that reported one.

## Where the gate sits

Blocked candidates are excluded **before scoring**, not after. If a skill
Capsule may not load could still win a route and be refused downstream, the
runner-up silently loses its slot and the task gets no pack at all. `Routing`
reports what was excluded and why.

## Overrides

`--allow-unaudited` promotes *approval-required* to allow, flags the decision as
requiring approval, and logs it. It never clears a `fail`, a HIGH/CRITICAL risk,
or a pending audit. There is no flag that does.

## Duplicates

Entries flagged `isDuplicate` are forks or copies. Even with clean audits they
require approval — the audit attaches to the fork, and the original is the thing
that was actually vetted.

## Rate limits and caching

**There is no unauthenticated tier.** Every endpoint answers 401 without a
credential, so `--api-key` is required in practice rather than optional, and
`--fixtures` is the only offline path.

The credential is a **Vercel OIDC token**, not a long-lived API key: minted per
request, scoped to a (team, project), rotating roughly every 12 hours. Capsule
sends it as a bearer token, which is the documented form — but a token pasted
into a config file stops working within the day. Read it from the environment
(`VERCEL_OIDC_TOKEN`) each run.

600 req/min per (team, project). Every response carries `X-RateLimit-Limit`,
`X-RateLimit-Remaining` and `X-RateLimit-Reset`; `HttpTransport` records the
last two so a caller can back off before being told to. Capsule mirrors the documented
Cache-Control windows (60s listings, 300s detail/curated) and backs off on
429/503 with `Retry-After`. A 404 on the audit endpoint means un-audited, which
the trust gate treats as deny — never as clean.
