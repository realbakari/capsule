# Context Engineering for Model Generations

## Overview

Context engineering in Capsule balances security invariants against behavioral instruction density. Over-prescribing rules in skill bodies creates instruction friction for frontier models (such as Claude 5 and GPT-5.6), while under-specifying rules leads to ambiguous execution.

## Key Principles

1. **Security Invariants vs. Behavioral Guidance**:
   - **Security Invariants** (`license:apache-2.0`, `deny` rules, `PreToolUse` hooks) must remain absolute and non-negotiable.
   - **Behavioral Guidance** (style, tone, formatting preferences) should remain concise, intent-driven, and high-altitude.

2. **Progressive Disclosure**:
   - Skills over 500 lines or 3,000 words should be broken into a core `SKILL.md` and supporting reference files in a `references/` directory.
   - The agent reads `SKILL.md` first, loading specific `references/*.md` on demand.

3. **Prescriptive Altitude Measurement**:
   - `capsule doctor` evaluates the ratio of imperative words (`MUST`, `NEVER`, `REQUIRED`) to explanatory prose.
   - Crucially, **security-related keywords are excluded from prescription penalties** so that security rules do not artificially degrade a skill's health score.
