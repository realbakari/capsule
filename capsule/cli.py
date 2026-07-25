"""Capsule CLI.

    capsule index        --roots ... --out index.json [--by-category] [--lifecycle stable]
    capsule show         --index index.json [--type skill]
    capsule route        --index index.json --task "..."
    capsule reconstruct  --index index.json --skill NAME --dest ./packs
    capsule validate     --pack ./packs/NAME
    capsule audit        --index index.json
    capsule eval         --evals ./skill-evals --output agent-output.txt
    capsule emit-plugins --repo owner/repo --out .
    capsule schema       --out ./schemas
    capsule doctor       --index index.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .color import bold, cyan, dim, fail_badge, green, red, warn_badge
from .config import CapsuleConfig, description_budget, lethal_trifecta, trigger_overlap
from .contract import Changeset, brief, contract_for_skill, verify
from .doctor import audit_context
from .evals import load_evals, run_eval
from .harness import (
    ArtifactEntry, SkillMeta, classify_input_provenance,
    emit_all, emit_all_plugins, split_obligations,
)
from .discover import discover
from .health import analyze, description_quality, summarize, tool_grant_risk
from .registry import FixtureTransport, HttpTransport, Registry, RegistryError
from .policy import Policy, PolicyError
from .reconstruct import package, reconstruct
from .router import route
from .schema import RunContext
from .taxonomy import derive_domains
from .schema_export import export_schemas
from .validate import validate_pack

DEFAULT_ROOTS = ["./skills", "./packs"]


def _config(args: argparse.Namespace) -> CapsuleConfig:
    return CapsuleConfig.load(getattr(args, "config", None))


def _policy(args: argparse.Namespace) -> Policy:
    cfg = _config(args)
    return Policy(
        writable_roots=cfg.writable_roots,
        readonly_roots=cfg.readonly_roots,
        allow_restricted_reconstruction=(
            cfg.allow_restricted_reconstruction or getattr(args, "allow_restricted", False)
        ),
        allow_unaudited_registry_skills=(
            cfg.allow_unaudited_registry_skills or getattr(args, "allow_unaudited", False)
        ),
        ruleset=cfg.ruleset,
    )


def _registry(args: argparse.Namespace) -> Registry:
    fixtures = getattr(args, "fixtures", None)
    if fixtures:
        return Registry(FixtureTransport(Path(fixtures)))
    return Registry(HttpTransport(api_key=getattr(args, "api_key", None)))


def _taxonomy_for(context: RunContext, cfg: CapsuleConfig):
    """Configured taxonomy, extended with domains derived from this corpus.

    Built-in domains describe the corpus Capsule shipped against. Deriving from
    the indexed names means a workspace of Lens Studio skills classifies as
    Lens Studio rather than falling to "general", without anyone declaring
    anything -- which matters because new skills appear faster than any table
    can be maintained.
    """
    names = [r.name for r in context.of_type("skill")]
    return cfg.taxonomy.with_derived_domains(derive_domains(names))


def _load(index_path: str) -> RunContext:
    path = Path(index_path)
    if not path.exists():
        sys.exit(f"index not found: {path} (run `capsule index` first)")
    return RunContext.from_json(path.read_text())


def cmd_index(args: argparse.Namespace) -> int:
    policy = _policy(args)
    roots = args.roots or DEFAULT_ROOTS
    context = discover(roots, policy, taxonomy=_config(args).taxonomy)
    Path(args.out).write_text(context.to_json())

    skills = context.of_type("skill")
    ok = [r for r in skills if r.reconstructable]
    print(bold(f"indexed {len(context.records)} sources from {len(roots)} root(s) -> {args.out}"))

    # An empty index is almost always a pointing error, and every later command
    # reads it happily: routing finds no candidate, lint finds no problem, and
    # verify passes an empty contract. Say so here rather than letting the
    # emptiness propagate as a clean bill of health.
    if not context.records:
        missing = [r for r in roots if not Path(r).exists()]
        print(
            red("  no sources found.") + " Check the roots are correct"
            + (f"; these do not exist: {', '.join(missing)}" if missing else "")
            + ".",
            file=sys.stderr,
        )
        return 1
    print(f"  skills: {len(skills)}  reconstructable: {len(ok)}  gated: {len(skills) - len(ok)}")

    # Category grouping.
    if getattr(args, "by_category", False):
        categories = sorted({r.category for r in skills})
        for cat in categories:
            cat_skills = context.of_category(cat)
            print(f"\n  {bold(cyan('[' + cat + ']'))}")
            for r in sorted(cat_skills, key=lambda x: x.name):
                lifecycle_tag = dim(f" ({r.lifecycle})") if r.lifecycle != "stable" else ""
                print(f"    {r.name}{lifecycle_tag}")
    else:
        for source_type in sorted({r.source_type for r in context.records}):
            n = len(context.of_type(source_type))
            print(f"  {source_type:<12} {n}")

    # Lifecycle filtering.
    lifecycle_filter = getattr(args, "lifecycle", None)
    if lifecycle_filter:
        filtered = context.of_lifecycle(lifecycle_filter)
        print(f"\n  lifecycle={lifecycle_filter}: {len(filtered)} skill(s)")
        for r in filtered:
            print(f"    {r.name} [{r.category}]")

    return 0


def cmd_show(args: argparse.Namespace) -> int:
    context = _load(args.index)
    records = context.of_type(args.type) if args.type else context.records
    for record in sorted(records, key=lambda r: (r.category, r.name)):
        print(record.digest())
    print(f"\n{len(records)} record(s)")
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    context = _load(args.index)
    cfg = _config(args)
    routing = route(
        context, args.task,
        shortlist_size=cfg.shortlist_size,
        min_score=cfg.min_route_score,
        policy=_policy(args),
        precedence=cfg.precedence,
        taxonomy=_taxonomy_for(context, cfg),
    )
    print(routing.report())
    return 0 if routing.selected else 2


def cmd_reconstruct(args: argparse.Namespace) -> int:
    context = _load(args.index)
    policy = _policy(args)
    records = [context.by_name(args.skill)] if args.skill else context.of_type("skill")
    records = [record for record in records if record is not None]

    if args.skill and not records:
        print(f"skill not found in index: {args.skill}", file=sys.stderr)
        return 1

    if not records:
        print("no skills found to reconstruct", file=sys.stderr)
        return 1

    ok = True
    for record in records:
        try:
            result = reconstruct(record, args.dest, policy, overwrite=args.overwrite)
            print(result.line())
            if args.package:
                archive = package(result.destination, args.dest, policy)
                print(f"  packaged -> {archive}")
            if not result.valid:
                ok = False
        except PolicyError as exc:
            ok = False
            print(f"{record.name}: denied -- {exc}", file=sys.stderr)
        except RuntimeError as exc:
            ok = False
            print(f"{record.name}: failed -- {exc}", file=sys.stderr)

    audit = policy.audit_text()
    if audit and getattr(args, "audit", False):
        print("\npolicy audit:")
        print(audit)

    return 0 if ok else 1


def cmd_validate(args: argparse.Namespace) -> int:
    paths = list(args.paths or [])
    if args.pack:
        paths.append(args.pack)
    if not paths:
        print("no pack paths supplied", file=sys.stderr)
        return 1

    ok = True
    for pack_path in paths:
        valid, problems = validate_pack(pack_path)
        if valid:
            print(f"{pack_path}: valid")
        else:
            ok = False
            print(f"{pack_path}: invalid")
            for problem in problems:
                print(f"  - {problem}")
    return 0 if ok else 1


def cmd_audit(args: argparse.Namespace) -> int:
    context = _load(args.index)
    cfg = _config(args)

    budget = description_budget(context, cfg.description_budget)
    print(budget.line() if hasattr(budget, "line") else f"description budget: {budget.total_chars} chars")

    overlap = trigger_overlap(context)
    worst = overlap.worst() if hasattr(overlap, "worst") else sorted(overlap.pairs, key=lambda p: -p[2])[:5]
    if worst:
        print("trigger overlap:")
        for a, b, score in worst:
            print(f"  {a} <-> {b}: {score:.2f}")
    else:
        print("trigger overlap: none")

    trifectas = []
    for record in context.of_type("skill"):
        skill_md = Path(record.source_path) / "SKILL.md"
        if not skill_md.exists():
            continue
        report = lethal_trifecta(record, skill_md.read_text(errors="replace"))
        if report.complete:
            trifectas.append(report)

    if trifectas:
        print("lethal trifecta:")
        for report in trifectas:
            print(f"  {report.line()}")
        return 1

    print("lethal trifecta: none")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Run skill evaluations against provided output."""
    evals_path = Path(args.evals)
    output_path = Path(args.output) if args.output else None

    suites = load_evals(evals_path)
    if not suites:
        print(f"no eval suites found in {evals_path}")
        return 1

    output_text = output_path.read_text() if output_path and output_path.exists() else ""

    all_passed = True
    for suite in suites:
        result = run_eval(suite, output_text)
        print(result.report())
        print()
        if not result.passed:
            all_passed = False

    return 0 if all_passed else 1


def cmd_emit_plugins(args: argparse.Namespace) -> int:
    """Generate multi-host plugin manifests."""
    repo_slug = args.repo
    output_dir = Path(args.out)

    index_path = getattr(args, "index", None)
    if index_path and Path(index_path).exists():
        context = _load(index_path)
        skills = [
            SkillMeta(
                name=r.name,
                description=r.purpose,
                category=r.category,
                lifecycle=r.lifecycle,
            )
            for r in context.of_type("skill")
        ]
    else:
        context = discover([str(output_dir)], Policy())
        skills = [
            SkillMeta(
                name=r.name,
                description=r.purpose,
                category=r.category,
                lifecycle=r.lifecycle,
            )
            for r in context.of_type("skill")
        ]

    if not skills:
        print("no skills found to generate plugins for")
        return 1

    entries = emit_all_plugins(skills, repo_slug, output_dir)

    for entry in entries:
        dest_path = output_dir / entry.path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(entry.content)
        print(f"  wrote {entry.path}")

    print(bold(green(f"\ngenerated {len(entries)} plugin manifest files for {repo_slug}")))
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    """Export JSON Schemas."""
    out_dir = Path(args.out)
    paths = export_schemas(out_dir)
    print(bold("exported JSON Schemas:"))
    for filename, filepath in paths.items():
        print(f"  {green(filename)} -> {filepath}")
    return 0


_SEVERITY_FLOOR = {"high": 0, "medium": 1, "low": 2, "info": 3}


def cmd_doctor(args: argparse.Namespace) -> int:
    """Assess each skill's calibration for current-generation models.

    Two analyses, deliberately kept distinct. `health` measures instruction
    altitude and the refusal-risk checks; `doctor` measures body budget and
    verification-loop triggers. They answer different questions, so both print.
    """
    index_path = getattr(args, "index", None)
    if index_path and Path(index_path).exists():
        context = _load(index_path)
    else:
        context = discover(["."], Policy())

    skills = context.of_type("skill")
    if not skills:
        print("no skills found for doctor audit")
        return 0

    reports = []
    for record in skills:
        skill_md = Path(record.source_path) / "SKILL.md"
        if not skill_md.exists():
            continue
        body = skill_md.read_text(errors="replace")
        aux = sum(
            1 for f in Path(record.source_path).rglob("*")
            if f.is_file() and f.name != "SKILL.md"
        )
        reports.append(analyze(record, body, aux))

    budget_findings = {a.name: a.diagnostics for a in audit_context(context)}

    floor = _SEVERITY_FLOOR.get(getattr(args, "severity", "low") or "low", 2)
    show_all = getattr(args, "all", False)

    print(bold("Capsule Doctor — model calibration"))
    print("-" * 68)

    shown = 0
    for report in sorted(reports, key=lambda r: (_SEVERITY_FLOOR[r.worst_severity],
                                                 -r.prescriptiveness)):
        visible = [f for f in report.findings
                   if _SEVERITY_FLOOR.get(f.severity, 3) <= floor]
        extra = budget_findings.get(report.record.name, [])
        if not visible and not extra and not show_all:
            continue
        shown += 1
        print(report.line())
        for finding in visible:
            print(f"     {finding.line()}")
        for diag in extra:
            print(f"     [{diag.severity}] {diag.kind}: {diag.message}")

    counts = summarize(reports)
    print(
        f"\n{len(reports)} skill(s): {counts['high']} high, {counts['medium']} medium, "
        f"{counts['low']} low, {counts['clean']} clean"
    )
    if not show_all and shown == 0:
        print("nothing at or above the requested severity; pass --all to see every skill")
    print(
        "\nAltitude excludes security invariants from the prescriptiveness count. "
        "A high behavioral count is a prompt to review, not a defect."
    )
    return 1 if counts["high"] else 0


def cmd_lint(args: argparse.Namespace) -> int:
    """Custom rules, trifecta detection, and the two corpus-level diagnostics."""
    context = _load(args.index)
    policy = _policy(args)
    cfg = _config(args)

    print(bold("== custom rules =="))
    flagged = 0
    for record in context.of_type("skill"):
        body_path = Path(record.source_path) / "SKILL.md"
        body = body_path.read_text(errors="replace") if body_path.exists() else ""
        for aux in ("scripts", "core"):
            aux_dir = Path(record.source_path) / aux
            if aux_dir.exists():
                for f in sorted(aux_dir.rglob("*")):
                    if f.is_file() and f.suffix in (".py", ".sh", ".js", ".md"):
                        body += "\n" + f.read_text(errors="replace")

        decision = policy.apply_rules(record, body)
        if not decision.allowed or "flagged" in decision.reason:
            flagged += 1
            print(f"  {warn_badge()} {record.name}: {decision.reason[:160]}")

        trifecta = lethal_trifecta(record, body)
        if trifecta.complete:
            print(f"  {fail_badge()} {trifecta.line()}")
        provenance = classify_input_provenance(record.name, body)
        if provenance.tier == "live":
            print(f"  {warn_badge()} {provenance.line()}")
    print(f"  {flagged} skill(s) matched at least one rule\n")

    print(bold("== description quality =="))
    weak = 0
    for record in context.of_type("skill"):
        findings = description_quality(record.description)
        if findings:
            weak += 1
            print(f"  {record.name}:")
            for finding in findings:
                print(f"     {finding.line()}")
    if not weak:
        print(f"  {green('every description names both what it does and when to use it')}")
    print()

    agents = context.of_type("agent")
    if agents:
        print(bold("== agent definitions =="))
        wide = 0
        for record in agents:
            findings = tool_grant_risk(record.name, record.tool_grants)
            findings += description_quality(record.purpose)
            if findings:
                wide += 1
                print(f"  {record.name}:")
                for finding in findings:
                    print(f"     {finding.line()}")
        print(f"  {len(agents)} agent(s), {wide} with findings\n")

    print(bold("== corpus diagnostics =="))
    budget = description_budget(context, cfg.description_budget)
    print(f"  {budget.line()}")
    if budget.at_risk:
        print(f"  {red('at risk of silent truncation')}: {', '.join(budget.at_risk[:6])}")

    overlap = trigger_overlap(context)
    if overlap.pairs:
        print("  trigger-phrase collisions (routing ambiguity risk):")
        for a, b, score in overlap.worst():
            print(f"    {a} <-> {b}  jaccard={score}")
    else:
        print(f"  {green('no trigger-phrase collisions above threshold')}")
    return 0


def _select(args: argparse.Namespace):
    """Resolve a skill by name, or by routing the task to one."""
    context = _load(args.index)
    if getattr(args, "skill", None):
        record = context.by_name(args.skill)
        if record is None:
            sys.exit(f"no skill named {args.skill!r} in the index")
        return record, ""
    if not getattr(args, "task", None):
        sys.exit('pass either --skill NAME or --task "..."')

    cfg = _config(args)
    routing = route(context, args.task, shortlist_size=cfg.shortlist_size,
                    min_score=cfg.min_route_score, policy=_policy(args),
                    precedence=cfg.precedence, taxonomy=_taxonomy_for(context, cfg))
    if routing.selected is None:
        print(f"no pack selected: {routing.rationale}", file=sys.stderr)
        sys.exit(2)
    return routing.selected, routing.rationale


def cmd_brief(args: argparse.Namespace) -> int:
    """Emit an injectable activation block for the selected pack."""
    record, rationale = _select(args)
    print(brief(record, contract_for_skill(record), rationale))
    return 0


def cmd_contract(args: argparse.Namespace) -> int:
    """Show the obligations extracted from a skill."""
    record, _ = _select(args)
    contract = contract_for_skill(record)
    print(contract.summary())
    print()
    for obligation in contract.checkable:
        print(f"  {obligation.line()}")
    if contract.advisory and args.advisory:
        print("\nadvisory (not mechanically checkable):")
        for obligation in contract.advisory[:20]:
            print(f"  - {obligation.directive[:110]}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Check a change against the selected skill's contract.

    Exit 5 on violation, so this drops into a pre-commit hook or a CI job
    unchanged. That is the point: adherence becomes a property of the diff
    rather than of whether the agent read the pack.
    """
    record, _ = _select(args)

    # An unreadable body yields an empty contract, and an empty contract is
    # adherent by construction. Reporting that as a pass is worse than
    # erroring: the gate looks green while enforcing nothing.
    skill_md = Path(record.source_path) / "SKILL.md"
    if not skill_md.exists():
        print(
            f"cannot read {skill_md}: nothing to enforce. "
            "Rebuild the index (`capsule index`) or pass --skill for a skill "
            "that is still on disk.",
            file=sys.stderr,
        )
        return 4

    contract = contract_for_skill(record)
    if not contract.obligations:
        print(
            f"{record.name} yields no mechanically checkable obligations; "
            "nothing to verify.",
            file=sys.stderr,
        )
        return 0

    try:
        if args.paths:
            changeset = Changeset.from_paths(args.paths, root=args.repo)
        elif args.diff:
            changeset = Changeset.from_diff(Path(args.diff).read_text())
        else:
            changeset = Changeset.from_git(args.repo, ref=args.ref)
    except (RuntimeError, OSError) as exc:
        print(f"could not read changes: {exc}", file=sys.stderr)
        return 4

    if changeset.is_empty():
        print("no changes to verify")
        return 0

    report = verify(contract, changeset)
    print(report.report())
    return 5 if report.violations else 0


def cmd_harness(args: argparse.Namespace) -> int:
    """Push a contract into the host's own enforcement primitives."""
    record, _ = _select(args)
    contract = contract_for_skill(record)
    commands, contents = split_obligations(contract)

    index_path = str(Path(args.index).resolve()) if getattr(args, 'route_prompts', False) else None
    emissions = emit_all(contract, args.dest, index_path=index_path,
                         target=getattr(args, 'target', 'claude-code'))
    if args.dry_run:
        for emission in emissions:
            print(bold(f"--- {emission.path}"))
            print(emission.content)
        return 0

    policy = _policy(args)
    dest = Path(args.dest)
    decision = policy.can_write(dest)
    if not decision.allowed:
        print(f"refusing to write: {decision.reason}", file=sys.stderr)
        return 3

    for emission in emissions:
        target = dest / emission.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(emission.content)
        if target.suffix == ".py":
            target.chmod(0o755)
        print(f"wrote {green(str(target))}")

    print(
        f"\n{len(commands)} prohibition(s) never run (deny rules); "
        f"{len(contents)} blocked before the write lands (PreToolUse hook)."
    )
    if index_path:
        print(
            "\nPrompt routing is on: every prompt is routed against "
            f"{index_path} and a brief is injected when a pack clears the "
            "confidence threshold. It fails open and stays silent below it."
        )
    print(
        "Hook payload field names are harness-specific. Verify with "
        "CAPSULE_HOOK_DEBUG=1 before relying on the hook; it fails open when it "
        "recognises nothing."
    )
    return 0


def cmd_registry(args: argparse.Namespace) -> int:
    """Pull registry skills, apply the trust gate, optionally merge the records."""
    policy = _policy(args)
    registry = _registry(args)
    try:
        entries = (registry.search(args.query, limit=args.limit) if args.query
                   else registry.leaderboard(per_page=args.limit))
    except RegistryError as exc:
        print(f"registry unavailable: {exc}", file=sys.stderr)
        return 4

    records = [registry.to_record(e) for e in entries]
    allowed = 0
    for record in records:
        decision = policy.can_load(record)
        mark = green("LOAD ") if decision.allowed else red("BLOCK")
        allowed += decision.allowed
        print(f"{mark} {record.name:<28} installs={record.installs:<9} "
              f"trust={record.trust_verdict}/{record.trust_risk or 'n-a'}")
        if not decision.allowed:
            print(f"        reason: {decision.reason}")

    print(f"\n{allowed} loadable, {len(records) - allowed} blocked by the trust gate")

    if args.merge:
        context = _load(args.index)
        context.records = [r for r in context.records if r.source_type != "registry"]
        context.records.extend(records)
        Path(args.index).write_text(context.to_json())
        print(f"merged {len(records)} registry record(s) into {args.index}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capsule")
    subparsers = parser.add_subparsers(dest="subcommand")

    p_index = subparsers.add_parser("index")
    p_index.add_argument("--roots", nargs="*")
    p_index.add_argument("--out", default="capsule-index.json")
    p_index.add_argument("--by-category", action="store_true")
    p_index.add_argument("--lifecycle")
    p_index.add_argument("--config")

    p_show = subparsers.add_parser("show")
    p_show.add_argument("--index", default="capsule-index.json")
    p_show.add_argument("--type")

    p_route = subparsers.add_parser("route")
    p_route.add_argument("--index", default="capsule-index.json")
    p_route.add_argument("--task", required=True)
    p_route.add_argument("--config")

    p_reconstruct = subparsers.add_parser("reconstruct")
    p_reconstruct.add_argument("--index", default="capsule-index.json")
    p_reconstruct.add_argument("--skill")
    p_reconstruct.add_argument("--dest", default="./packs")
    p_reconstruct.add_argument("--config")
    p_reconstruct.add_argument("--overwrite", action="store_true")
    p_reconstruct.add_argument("--package", action="store_true")
    p_reconstruct.add_argument("--audit", action="store_true")
    p_reconstruct.add_argument("--allow-restricted", action="store_true")
    p_reconstruct.add_argument("--allow-unaudited", action="store_true")

    p_validate = subparsers.add_parser("validate")
    p_validate.add_argument("paths", nargs="*")
    p_validate.add_argument("--pack")

    p_audit = subparsers.add_parser("audit")
    p_audit.add_argument("--index", default="capsule-index.json")
    p_audit.add_argument("--config")

    p_eval = subparsers.add_parser("eval")
    p_eval.add_argument("--evals", required=True, help="Path to evals directory or evals.json")
    p_eval.add_argument("--output", help="Path to agent output file to check")

    p_plugins = subparsers.add_parser("emit-plugins")
    p_plugins.add_argument("--repo", required=True, help="GitHub repo slug (owner/repo)")
    p_plugins.add_argument("--out", default=".", help="Output directory")
    p_plugins.add_argument("--index", help="Path to capsule-index.json")

    p_schema = subparsers.add_parser("schema")
    p_schema.add_argument("--out", default="./schemas", help="Output directory for schema files")

    p_doctor = subparsers.add_parser("doctor")
    p_doctor.add_argument("--index", help="Path to capsule-index.json")
    p_doctor.add_argument("--severity", default="low",
                          choices=["high", "medium", "low", "info"],
                          help="minimum severity to display (default: low)")
    p_doctor.add_argument("--all", action="store_true",
                          help="show every skill, including clean ones")

    p_lint = subparsers.add_parser("lint", help="custom rules and corpus diagnostics")
    p_lint.add_argument("--index", default="capsule-index.json")
    p_lint.add_argument("--config")

    # brief/contract/verify/harness all resolve a skill the same way: by name,
    # or by routing a task to one.
    for name, help_text in (
        ("brief", "emit an injectable activation block for a task"),
        ("contract", "show the obligations extracted from a skill"),
        ("verify", "check a diff against a skill's contract"),
        ("harness", "generate deny rules and a PreToolUse hook from a contract"),
    ):
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument("--index", default="capsule-index.json")
        p.add_argument("--skill", default=None)
        p.add_argument("--task", default=None)
        p.add_argument("--config")
        if name == "contract":
            p.add_argument("--advisory", action="store_true",
                           help="also list the directives that cannot be verified")
        if name == "verify":
            p.add_argument("--repo", default=".", help="git repo to diff (default: cwd)")
            p.add_argument("--ref", default=None, help="git diff ref, e.g. --cached or main")
            p.add_argument("--diff", default=None, help="read a unified diff from a file")
            p.add_argument("--paths", nargs="*", default=None,
                           help="verify whole files instead of a diff")
        if name == "harness":
            p.add_argument("--dest", default="./.claude")
            p.add_argument("--dry-run", action="store_true", help="print instead of writing")
            p.add_argument("--target", default="claude-code",
                           choices=["claude-code", "managed-agents"],
                           help="host to emit for; the permission models differ")
            p.add_argument("--route-prompts", action="store_true",
                           help="also emit a UserPromptSubmit hook that routes every "
                                "prompt and injects the activation brief")

    p_registry = subparsers.add_parser("registry", help="query skills.sh and apply the trust gate")
    p_registry.add_argument("--query", default=None, help="search instead of the leaderboard")
    p_registry.add_argument("--limit", type=int, default=5)
    p_registry.add_argument("--fixtures", default=None, help="replay recorded responses offline")
    p_registry.add_argument("--api-key", default=None)
    p_registry.add_argument("--allow-unaudited", action="store_true",
                            help="let warn/medium skills load (never fail/high/critical)")
    p_registry.add_argument("--merge", action="store_true", help="write records into the index")
    p_registry.add_argument("--index", default="capsule-index.json")
    p_registry.add_argument("--config")

    # A table rather than an if/elif chain: registering a subparser without
    # wiring its handler is how `lint`, `registry`, `brief`, `contract`,
    # `verify` and `harness` went missing from the CLI while staying in the
    # docs. A missing key here is a KeyError at startup, not a silent no-op.
    handlers = {
        "index": cmd_index,
        "show": cmd_show,
        "route": cmd_route,
        "reconstruct": cmd_reconstruct,
        "validate": cmd_validate,
        "audit": cmd_audit,
        "eval": cmd_eval,
        "emit-plugins": cmd_emit_plugins,
        "schema": cmd_schema,
        "doctor": cmd_doctor,
        "lint": cmd_lint,
        "brief": cmd_brief,
        "contract": cmd_contract,
        "verify": cmd_verify,
        "harness": cmd_harness,
        "registry": cmd_registry,
    }

    args = parser.parse_args(argv)
    handler = handlers.get(args.subcommand)
    if handler is None:
        parser.print_help()
        return 0

    try:
        return handler(args)
    except PolicyError as exc:
        print(f"policy refusal: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())

