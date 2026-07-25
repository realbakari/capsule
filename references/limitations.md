# Limitations of skills for coding agents — and where Capsule helps

A review of documented failure modes in the agent-skills paradigm, each scored
against what Capsule actually does. The scoring is deliberately harsh: **solved**
means Capsule removes the failure mode, **mitigated** means it makes it visible
or cheaper, **not solved** means Capsule does nothing useful and says so.

Nothing below is a claim about Capsule making agents safe. Most of these are
properties of the model and the host, and no control plane reaches them.

---

## 1. Non-determinism in activation

**The problem.** Skills fire on description matching, not deterministic rules.
Two skills with overlapping triggers can both fire, neither fire, or fire in the
wrong order; a reworded description silently changes behavior; the same prompt
can trigger different skill combinations across runs depending on phrasing,
context state, or how many other skills are loaded. There is no compiler to
catch any of it, and you find out in production.

**Capsule: mitigated, not solved.** Routing is deterministic and replayable —
same index plus same task yields the same selection, with a recorded rationale
and the runner-up margins. `capsule route` is a testable harness: trigger
expectations become assertions, so under- and over-triggering are caught in CI
rather than in production.

But this only governs what Capsule selects. Whether the *host agent* honours that
selection is a separate problem — see **#12 Adherence** below, which is where the
practical pain actually lives and which earlier versions of this document wrote off
too quickly.

## 2. Routing ambiguity from overlapping descriptions

**The problem.** The most-cited root cause of skill misfires. If three skills say
"review", the model is guessing. Skills are authored by different people and
teams with no awareness of each other, and the format has no mechanism for
declaring that one skill is a specialisation of another — so overlapping skills
land in the same catalog and compete arbitrarily.

**Capsule: mitigated.** Two things:

- `capsule lint` computes pairwise trigger-vocabulary overlap and names the
  colliding pairs. On the 34-skill corpus here it surfaces `pdf ↔ pdf-reading`
  (0.39), `grocery-shopping ↔ meal-delivery` (0.36),
  `cancel-unsubscribe ↔ return-refund` (0.38). The `pdf` pair is the one that
  broke a routing test during development — the diagnostic finds it independently.
- `[[routing.precedence]]` supplies the missing specialisation mechanism
  *locally*. It cannot fix the ecosystem-wide gap, and a declaration in your
  config does not travel with the skill.

Default precedence weight is advisory (2.0): it settles near-ties without
overruling a decisive match. Raise `weight` to make it authoritative.

## 3. Silent truncation of the description budget

**The problem.** Combined skill descriptions are concatenated into the selection
context. Past a host-specific character budget the overflow is dropped, and the
affected skills simply stop triggering — with no error surfaced anywhere. This
is a failure mode you cannot see from inside the agent.

**Capsule: mitigated, and this is the cheapest win in the list.**
`description_budget()` measures the whole corpus and names which skills sit past
the line. The 34-skill corpus here totals 5,891 chars against a 12,000 default —
comfortable, but the number is a config knob because the real ceiling is
host-dependent and Capsule cannot read it.

## 4. Context rot and long-horizon degradation

**The problem.** Models degrade on long contexts even when everything technically
fits — information mid-context gets less attention, accuracy varies with position
rather than presence, and agent context is cumulative across every tool result.
Context exhaustion in coding agents shows up as agents losing earlier
requirements, contradicting prior instructions, and hallucinating function
signatures that do not exist in the codebase.

**Capsule: partially mitigated, mostly upstream of it.** "Prefer the smallest
useful set of files" and loading exactly one pack is the right direction, and the
condensed index is designed to be cheap to hold. But Capsule governs *selection*,
not the agent's running context. It does not compact, summarise, or evict, and it
has no visibility into token growth during execution. Treat it as reducing what
enters the context, not as managing what is already there.

## 5. Staleness and update drift

**The problem.** Markdown does not update itself. A skill keeps confidently
instructing an agent to do something that stopped being true, and produces
plausible wrong output rather than an error. Downstream, unpinned dependencies
mean a skill's behavior can change without its content changing at all.

**Capsule: mitigated.** Every record carries a `content_hash`; registry records
carry the upstream `hash` too, so drift is detectable without refetching bodies.
Reconstruction is deterministic — same source, same tree hash — so a changed hash
means the upstream skill moved and the pack must be regenerated rather than
hand-edited. The `ast07-unpinned-dependency` rule flags unpinned installs.

Capsule detects drift. It cannot tell you whether the *content* is still correct;
a skill can be byte-identical and completely out of date.

## 6. Supply chain compromise and malicious skills

**The problem.** This is not theoretical. Snyk's ToxicSkills audit scanned 3,984
skills and found 36.8% with security flaws and 13.4% with critical-level issues.
Broader analyses across 30,000+ skills report over 25% containing at least one
vulnerability. The ClawHavoc campaign flooded a registry with 1,184 malicious
skills across 12 publisher accounts, harvesting SSH credentials, wallet keys and
`.env` files — and at peak, five of the top seven most-downloaded skills on that
registry were confirmed malware. Skills run with the full permissions of the
agent process: if the agent can reach your AWS credentials, so can any skill it
loads.

**Capsule: mitigated at the gate, not at runtime.** The trust gate aggregates
multi-provider audits worst-verdict-wins, denies on fail/HIGH/CRITICAL, denies on
pending, and denies on un-audited. Duplicates and typosquat-shaped forks require
approval. Registry records are never reconstructable and are scoped
`external-untrusted`.

**But Capsule is a gate, not a sandbox.** It decides what may load. Once a skill
loads, Capsule has no runtime visibility and cannot contain it. Isolation belongs
to the host — containerisation, network restrictions, filesystem scoping — and
Capsule replaces none of that.

## 7. Prompt injection through skill files

**The problem.** Skill files are a first-class injection channel, and a harder one
than classic input injection: skills are inherently high-trust, densely
instructional, and routinely hold shell, API and filesystem access. Automated
skill-poisoning achieves attack success rates in the 70–98% range across
backdoor, disclosure, privilege-escalation and unauthorized-write categories.
Payloads hide in HTML comments, split across code, or blend into procedural steps.
Around 9% of skills.sh's curated top-100 fetch untrusted third-party content —
often legitimately, which is exactly what makes it an injection surface.

**Capsule: barely mitigated. Read this one carefully.** The
`ast04-hidden-html-directives` rule flags long HTML comments and
`ast02-remote-fetch-execute` catches shell-piped downloads. The lethal-trifecta
detector flags skills combining private-data access, untrusted-content ingestion,
and network egress — the configuration in which an injection becomes an
exfiltration.

Capsule also tiers each skill by how it acquires external content —
`disabled` / `indexed` / `live` — because retrieval mode bounds exposure. A cached
or indexed path lowers injection risk without removing it, so `indexed` is neither
safe nor equivalent to live fetching. The tier measures ingestion, not intent: a
scraper is legitimate and still lands in `live`. This corpus is 32 `disabled` to
2 `live`.

**Pattern matching does not solve this and Capsule must not be sold as if it
does.** The critical threats are natural-language instructions with no code
signature at all, and LLM-judge scanners have been measured at up to 92% false
positives on clean skills. Capsule's own lint demonstrates the problem: it flags
`mcp-builder` as a complete lethal trifecta. That is a *correct pattern match on
a benign skill* — a guide to building MCP servers naturally discusses credentials,
fetching, and posting. A clean lint means "nothing obvious", never "safe".

## 8. Over-prescription for current-generation models

**The problem.** This one runs opposite to every other entry here, which is why it
is easy to miss. Skills written for earlier models are often *too prescriptive* for
current ones and can degrade output quality. Claude Code removed over 80% of its
system prompt for the Claude 5 generation with no measurable loss on coding
evaluations. Enumerating every rule used to be defensive engineering; it is now a
cost, paid in attention budget and in reasoning spent resolving contradictions
between overlapping instructions.

There is also a hard failure mode inside this soft one: skills that tell the model
to echo or explain its internal reasoning as response text can trigger the
`reasoning_extraction` refusal category on Fable-class models, causing silent
fallbacks to a weaker model.

**Capsule: mitigated.** `capsule doctor` measures behavioral prescription density,
detects conflicting directives (severity weighted by proximity, since scoped
exceptions in a long document are not contradictions), flags monolithic bodies that
should be split into `references/`, and flags reasoning-extraction instructions as
high severity.

Two caveats stated plainly. First, altitude is **advisory** — 13 of the 34 shipping
skills here read as `brittle`, and these are first-party skills, so the number is a
prompt to review rather than a verdict. Second, Capsule preserves bodies verbatim on
reconstruction and will not rewrite someone's workflow to be less prescriptive; the
check informs authoring, it does not perform it.

## 9. Permissions declared but never derived

**The problem, corrected.** An earlier version of this document said skills carry
no permission model at all. That was wrong, and the correction matters because it
changes what Capsule can do. Claude Code skills support an `allowed-tools`
frontmatter field, and the harness has a full permission-rule system
(`ToolName(specifier)`, evaluated deny→ask→allow) plus `PreToolUse` hooks that can
block a tool call outright.

The real problem is narrower and more actionable: **the mechanism exists and goes
unused.** Zero of the 34 skills in this corpus declare `allowed-tools`. What is
missing is not the capability but the practice — nothing derives permissions from
what a skill actually says about itself. The over-permissioning findings stand;
the cause is disuse, not absence.

**Capsule: mitigated.** `capsule harness` translates a contract into the harness's
own enforcement primitives. Command-shaped prohibitions become `deny` rules, so
the command never runs. Content-shaped ones become a `PreToolUse` hook on
`Write|Edit`, so a banned token never reaches disk. The corpus splits 5 to 31
across those two, so both are needed.

Only `deny` rules are generated, never `allow`: inferring a permission grant from a
regex over prose would widen a user's permissions on the strength of a pattern
match. The hook fails *open* with a printed reason when it cannot parse a payload —
failing closed would be more secure in the narrow sense and worse in practice,
because a hook that blocks every edit after a schema change gets deleted, and then
it enforces nothing. See `references/harness.md`.

## 10. No signing or provenance verification

**The problem.** Publishing a skill can require nothing more than a markdown file
and a week-old account: no code signing, no security review, no sandbox by
default. Ed25519 signing plus content hashes for Merkle-root registry
verification is the recommended fix and is not yet widespread.

Anthropic's enterprise guidance now names the same two controls explicitly —
"compute checksums of reviewed Skills and verify them at deployment time" and
"use signed commits in your Skill repository to ensure provenance". Capsule does
the first and not the second, which sharpens rather than softens what follows.

**Capsule: partially.** Provenance is recorded — origin path, tree hash,
SKILL.md hash, license class — in a `PROVENANCE.md` that travels with every
rebuilt pack. **Signature verification is not implemented.** Capsule records what
it saw; it does not cryptographically verify that what it saw is what the author
published. This is the largest concrete gap and the obvious next build.

## 11. Adherence: the skill is loaded and the agent ignores it

**The problem.** The complaint people actually voice, in two parts. First, you end
up naming skills by hand — description matching is weak enough that trusting
activation does not work. Second, and worse: the pack is selected, the pack is
loaded, and the diff ignores it anyway. Instructions in a skill body are hints the
model may act on. Nothing in the format makes one binding.

Earlier versions of this document called this out of reach. That was too quick.

**Capsule: mitigated, with the boundary stated precisely.** You cannot make a model
comply, but you can stop making compliance depend on its attention. `capsule
contract` extracts a skill's checkable commitments; `capsule verify` checks them
against the actual diff and exits 5 on violation, so it gates a commit or a CI run.
Whether the agent read the pack stops determining the outcome.

The dividing line is crisp: a directive carrying a code-like token is verifiable
(``never insert `•` literally``, ``do not run `npm install` first``); one that is not
is taste ("write code that reads like the surrounding code"). Every report prints
**coverage** — across this corpus, 62 enforceable obligations against 314 advisory,
**16%**, ranging from 71% (`xlsx`) down to 0% for packs that are pure judgement.

For the activation half, `capsule brief` emits an injectable block naming the
selected pack, its enforceable obligations, and the fact that the diff will be
checked — so activation does not depend on the host's matcher agreeing.

**What it does not reach:** taste, semantic compliance, and behavior. Code can
satisfy every token rule and still miss the point. And verification runs *after* a
change exists, so this is a quality gate, not a security control. See
`references/adherence.md`.

## 12. Repository config as an execution surface

**The problem.** Two disclosed Claude Code CVEs (CVSS 8.7 and 5.3) confirmed that
repository-controlled configuration files can execute shell commands and
exfiltrate API keys at project-open time, before any trust dialog.

**Capsule: designed around it.** This is why Capsule never auto-imports Python
rule files from disk. Declarative rules in `capsule.toml` are data — regexes and
field matchers, parsed with `tomllib` and `yaml.safe_load`, never `eval`.
Programmatic rules must be imported and registered by the caller in code. A
config file dropped into a repo cannot make Capsule execute anything.

Rules also escalate only: a rule can turn an allow into a deny, never the
reverse. A hostile config can make Capsule refuse to work. It cannot make Capsule
permissive.

---

## Summary

| # | Limitation | Capsule |
|---|---|---|
| 1 | Non-deterministic activation | mitigated — deterministic selection, not deterministic execution |
| 2 | Routing ambiguity | mitigated — overlap detection + local precedence |
| 3 | Silent description truncation | mitigated — corpus budget measurement |
| 4 | Context rot | partial — reduces what enters, does not manage what is there |
| 5 | Staleness / drift | mitigated — hashing detects movement, not wrongness |
| 6 | Malicious skills | mitigated at the gate — no runtime containment |
| 7 | Prompt injection via skill files | **barely** — patterns miss language-level attacks |
| 8 | Over-prescription for current models | mitigated — `doctor` measures it; advisory, not prescriptive |
| 9 | Permissions declared but never derived | mitigated — contracts emit deny rules and a blocking hook |
| 10 | No signing | partial — provenance recorded, **signatures not verified** |
| 11 | Adherence (agent ignores the skill) | mitigated — contracts verified against the diff, 16% coverage |
| 12 | Repo config as execution surface | designed around — data-only config, escalate-only rules |

**The honest shape of it:** Capsule is a governance and selection layer. It is
strongest on the problems that are fundamentally about *bookkeeping under
uncertainty* — which skill, which version, which license, which audit, what
changed, why was that chosen. It is weakest wherever the answer requires either
semantic understanding of natural language (7, and the unenforceable 84% of 11) or
OS-level isolation (6).

**Where the ground moved, twice.** Entry 11 was scored *not solved* until
contracts made the distinction visible: enforcement does not have to happen at the
prompt, it can happen at the artifact. Entry 9 then moved for a plainer reason —
I had the facts wrong. Skills *do* have a permission model; it is simply unused.
Both corrections point the same way: check whether the mechanism exists before
concluding the problem is unsolvable, and check whether enforcement can move to a
layer where it is mechanical. Several remaining "unenforceable" items in this list
may be neither.

**One tension worth naming.** Entries 1–7 argue for more governance; entry 8 argues
for less instruction. Those pull against each other, and the resolution is a
distinction rather than a compromise: *security policy stays absolute, behavioral
guidance relaxes.* Capsule's gates remain deny-by-default while its authoring advice
moved toward intent over enumeration. Running the new check against Capsule's own
pack is what forced this into the code — see `references/context-engineering.md`
for how a naive prescriptiveness metric ends up recommending you weaken your own
security policy.

The combination that actually works is Capsule for selection and governance,
plus a real sandbox for isolation, plus behavioral scanning for the language-level
attacks that patterns cannot see. Capsule replaces neither of the others, and
a deployment with only Capsule is a deployment with a good audit trail of
decisions it could not enforce.

---

## Sources

- Snyk, *ToxicSkills* (Feb 2026) — 3,984 skills scanned; 36.8% flawed, 13.4% critical
- Snyk, *Why Your Skill Scanner Is Just False Security* (Feb 2026) — pattern-matcher limits
- OWASP *Agentic Skills Top 10* (AST10 v1.0-2026) — risk taxonomy, Universal Skill Format
- Antiy CERT, *ClawHavoc Campaign Analysis* — 1,184 malicious skills
- Check Point Research — CVE-2025-59536, CVE-2026-21852
- Liu et al. (2023) — lost-in-the-middle / positional degradation
- *SkillInject*, *SkillJect*, *SkillScope* (arXiv, 2026) — skill-file injection and least-privilege
- Castillo, *The Downsides of Agentic Skills*; Kinney, *Agent Skills, Stripped of Hype*
- Aerospike, *Agent Skills Explained: When to Use Them and Why They Fail* — truncation, staleness
- agentskills/agentskills discussion #404 — skill precedence proposal
- Anthropic Engineering, *Effective context engineering for AI agents* (Sep 2025)
- *The new rules of context engineering for Claude 5 generation models* (Jul 2026)
- *Prompting Claude Fable 5* — `reasoning_extraction` refusals, classifier domains
- Claude Code *Tools reference*, *Commands*, *Plugins reference* — permission rules,
  `PreToolUse` hooks, `allowed-tools`, plugin manifests, checkpointing limits
- *Building verification loops in Claude Code with skills* (Jul 2026)
- Multi-agent code-assistant study (arXiv:2508.08322) — reviewer agents catch what implementers miss
