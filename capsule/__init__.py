"""Capsule: an agent control plane for governed, replayable execution."""

__version__ = "0.1.0"

from .schema import RunContext, SourceRecord, TOOLS_INHERIT_ALL
from .policy import Policy, PolicyError, Decision
from .discover import discover, discover_agent, parse_frontmatter
from .router import route, classify, Routing
from .reconstruct import reconstruct, package, Reconstruction
from .validate import validate_pack
from .registry import Registry, HttpTransport, FixtureTransport, RegistryError
from .trust import aggregate, aggregate_api, ProviderAudit, TrustVerdict
from .rules import Rule, RuleSet, RuleHit, default_ruleset
from .config import CapsuleConfig, Precedence, description_budget, trigger_overlap, lethal_trifecta
from .health import (
    analyze, conservative_reporting_risk, description_quality, self_verification_risk,
    summarize, thinking_suppression_risk, tool_grant_risk,
    HealthReport, HealthFinding, Finding,
)
from .doctor import audit_skill, audit_context, SkillAudit, Diagnostic
from .evals import (
    Assertion, AssertionResult, CaseResult, EvalCase, EvalReport, EvalSuite,
    load_evals, run_case, run_eval, run_eval_per_case,
)
from .schema_export import export_schemas
from .taxonomy import Taxonomy, derive_domains, mentions
from .harness import (
    ArtifactEntry, Provenance, SkillMeta, classify_input_provenance, emit_all,
    emit_all_plugins, hook_config, hook_script, is_command_token,
    permission_rules, plugin_manifest, prompt_router_hook, split_obligations,
)
from .contract import (
    Changeset, Contract, Obligation, VerificationReport, brief,
    contract_for_skill, extract_contract, verify,
)

__all__ = [
    "RunContext", "SourceRecord", "TOOLS_INHERIT_ALL",
    "Policy", "PolicyError", "Decision",
    "discover", "discover_agent", "parse_frontmatter", "route", "classify", "Routing",
    "reconstruct", "package", "Reconstruction", "validate_pack",
    "Registry", "HttpTransport", "FixtureTransport", "RegistryError",
    "aggregate", "aggregate_api", "ProviderAudit", "TrustVerdict",
    "Rule", "RuleSet", "RuleHit", "default_ruleset",
    "CapsuleConfig", "Precedence", "description_budget", "trigger_overlap", "lethal_trifecta",
    "analyze", "conservative_reporting_risk", "description_quality",
    "self_verification_risk", "summarize", "thinking_suppression_risk", "tool_grant_risk",
    "HealthReport", "HealthFinding", "Finding",
    "audit_skill", "audit_context", "SkillAudit", "Diagnostic",
    "Assertion", "AssertionResult", "CaseResult", "EvalCase", "EvalReport", "EvalSuite",
    "load_evals", "run_case", "run_eval", "run_eval_per_case",
    "export_schemas",
    "Taxonomy", "derive_domains", "mentions",
    "Changeset", "Contract", "Obligation", "VerificationReport", "brief",
    "contract_for_skill", "extract_contract", "verify",
    "ArtifactEntry", "Provenance", "SkillMeta", "classify_input_provenance",
    "emit_all", "emit_all_plugins", "hook_config", "hook_script",
    "is_command_token", "permission_rules", "plugin_manifest", "split_obligations",
]
