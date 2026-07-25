"""Declarative taxonomy: categories, intents, domains.

These three vocabularies decide how a corpus is labelled and how a task is
classified. They used to be hardcoded lists in `discover.py` and `router.py`,
tuned against one example corpus, and that is the single biggest reason Capsule
degrades on skills it has not seen: new skills appear daily, and a fixed
keyword table describes the corpus its author happened to own.

Two changes follow from that:

- **Everything here is data**, extendable from `capsule.toml` without editing
  Python. A developer working on a domain Capsule has never heard of declares
  it once.
- **Domains can be derived from the corpus itself** (`derive_domains`), so a
  workspace of 62 Lens Studio skills produces Lens Studio domains rather than
  the built-in "spreadsheet / presentation / commerce" set, which describes
  none of them.

The built-in tables remain as a starter set, not as the definition.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

KeywordRules = list[tuple[str, tuple[str, ...]]]

# Starter tables. Order matters: first match wins, so these read semantically
# from most specific to least.
DEFAULT_CATEGORY_RULES: KeywordRules = [
    ("document-io", ("docx", "pdf", "pptx", "xlsx", "spreadsheet", "word document", "slide")),
    ("file-routing", ("uploaded", "read from disk", "router", "extract content")),
    ("skill-infrastructure", ("skill", "mcp server", "eval", "benchmark")),
    ("visual-design", ("design", "art", "theme", "poster", "gif", "watercolor", "brand", "artifact")),
    ("writing", ("writing", "documentation", "proposal", "comms", "voice profile", "draft")),
    ("commerce", ("order", "grocery", "delivery", "refund", "return", "subscription", "cart")),
    ("admin-tasks", ("expense", "reimburs", "form", "prescription", "appointment", "booking")),
    ("analysis", ("calculation", "financial", "scenario", "projection")),
    ("product-knowledge", ("anthropic's products", "claude code", "pricing")),
]

DEFAULT_INTENT_RULES: KeywordRules = [
    ("create", ("create", "make", "build", "generate", "write", "draft", "design")),
    ("edit", ("edit", "fix", "update", "modify", "change", "revise", "clean")),
    ("read", ("read", "extract", "parse", "summarize", "inspect", "open", "analyze")),
    ("convert", ("convert", "transform", "export", "turn into")),
    ("execute", ("order", "book", "submit", "file", "cancel", "refill", "return", "call")),
]

DEFAULT_DOMAIN_RULES: KeywordRules = [
    ("document", ("docx", "word", "document", "report", "memo", "letter")),
    ("presentation", ("pptx", "slide", "deck", "presentation")),
    ("spreadsheet", ("xlsx", "csv", "spreadsheet", "excel", "formula")),
    ("pdf", ("pdf", "form fill", "watermark")),
    ("skill", ("skill", "skill.md", "mcp", "eval")),
    ("visual", ("art", "poster", "theme", "gif", "paint", "design", "ui", "frontend")),
    ("writing", ("blog", "comms", "voice", "style", "documentation")),
    ("commerce", ("grocery", "delivery", "refund", "subscription", "shopping")),
    ("admin", ("expense", "reimbursement", "prescription", "appointment", "jury")),
]

# Tokens that carry no domain signal on their own.
_STOP_TOKENS = {
    "the", "and", "for", "with", "use", "when", "this", "that", "from", "into",
    "your", "you", "are", "can", "will", "how", "get", "set", "run", "new",
    "all", "any", "not", "but", "its", "it's", "skill", "skills", "using",
    "user", "users", "file", "files", "data", "code", "type", "types",
}


def mentions(haystack: str, needle: str) -> bool:
    """Word-boundary containment, tolerating a simple plural.

    The single source of truth for keyword matching. Bare `in` checks are the
    documented trap: "form" is inside "performance" and "return" is inside
    "returns", which labelled a trace-analysis skill as admin-tasks and a
    debugger as commerce. Never match keywords with `in`.
    """
    if not needle:
        return False
    stem = (
        re.escape(needle[:-1]) + "(?:y|ies)"
        if needle.endswith("y")
        else re.escape(needle) + "s?"
    )
    return re.search(rf"(?<!\w){stem}(?!\w)", haystack, re.IGNORECASE) is not None


def _first_match(rules: KeywordRules, haystack: str, default: str) -> str:
    for label, keywords in rules:
        if any(mentions(haystack, k) for k in keywords):
            return label
    return default


@dataclass
class Taxonomy:
    """Category, intent and domain vocabularies. All data, all replaceable."""

    categories: KeywordRules = field(default_factory=lambda: list(DEFAULT_CATEGORY_RULES))
    intents: KeywordRules = field(default_factory=lambda: list(DEFAULT_INTENT_RULES))
    domains: KeywordRules = field(default_factory=lambda: list(DEFAULT_DOMAIN_RULES))

    @classmethod
    def from_dict(cls, data: dict) -> "Taxonomy":
        """Build from a parsed `[taxonomy]` config section.

        `extend_defaults = false` replaces the starter tables outright, which is
        the right choice for a workspace in one domain: the built-ins are then
        pure noise. Declared rules are prepended so they win first-match.
        """
        section = data.get("taxonomy", {}) or {}
        extend = section.get("extend_defaults", True)

        def build(key: str, defaults: KeywordRules) -> KeywordRules:
            declared: KeywordRules = [
                (str(entry["name"]), tuple(str(k).lower() for k in entry.get("keywords", [])))
                for entry in section.get(key, [])
                if entry.get("name") and entry.get("keywords")
            ]
            return declared + list(defaults) if extend else declared

        return cls(
            categories=build("category", DEFAULT_CATEGORY_RULES),
            intents=build("intent", DEFAULT_INTENT_RULES),
            domains=build("domain", DEFAULT_DOMAIN_RULES),
        )

    def category_for(self, name: str, description: str) -> str:
        """Label a skill, demanding corroboration before committing.

        A single incidental keyword is weak evidence: "Returns diagnostics"
        made a debugger `commerce`, and "documentation reference" made an API
        reference `writing`. Both are correct keyword hits and wrong labels.

        A hit in the *name* is strong -- a skill called `docx` really is
        document-io. A hit in the description counts one apiece and needs a
        second to carry. Below that the honest answer is `general`, which
        matches the rest of Capsule: refuse rather than guess.
        """
        best_label, best_score = "general", 0
        for label, keywords in self.categories:
            score = 0
            if any(mentions(name, k) for k in keywords):
                score += 2
            score += sum(1 for k in keywords if mentions(description, k))
            if score > best_score:
                best_label, best_score = label, score
        return best_label if best_score >= 2 else "general"

    def classify(self, task: str) -> tuple[str, str]:
        return (
            _first_match(self.intents, task, "unknown"),
            _first_match(self.domains, task, "general"),
        )

    def with_derived_domains(self, derived: KeywordRules) -> "Taxonomy":
        """Return a copy whose derived domains are consulted first."""
        if not derived:
            return self
        return Taxonomy(self.categories, self.intents, derived + self.domains)


def _name_tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[-_\s]+", name.lower()) if len(t) > 2 and t not in _STOP_TOKENS]


def derive_domains(names, min_shared: int = 2, limit: int = 12) -> KeywordRules:
    """Infer domain labels from the names a corpus actually uses.

    Naming conventions encode domain: a workspace with `specs-websocket`,
    `specs-depth` and `specs-asr` is telling you `specs` is a domain, and no
    built-in table will ever contain it. Any token shared by at least
    `min_shared` skills becomes a domain keyword.

    This is deliberately shallow. It reads names, not meaning, so it cannot
    tell that `lens` and `spectacles` are the same subject -- a developer who
    wants that says so in `capsule.toml`.
    """
    counts = Counter(token for name in names for token in set(_name_tokens(name)))
    shared = [token for token, n in counts.most_common(limit) if n >= min_shared]
    return [(token, (token,)) for token in shared]
