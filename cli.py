"""Capsule CLI.

    capsule index      --roots ... --out index.json
    capsule show       --index index.json [--type skill]
    capsule route      --index index.json --task "..."
    capsule reconstruct --index index.json --skill NAME --dest ./packs
    capsule validate   --pack ./packs/NAME
    capsule audit      --index index.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import CapsuleConfig, description_budget, lethal_trifecta, trigger_overlap
from .contract import Changeset, brief, contract_for_skill, verify
from .harness import classify_input_provenance, emit_all, split_obligations
from .discover import discover
from .health import analyze, summarize
from .registry import FixtureTransport, HttpTransport, Registry, RegistryError
from .policy import Policy, PolicyError
from .reconstruct import package, reconstruct
from .router import route
from .schema import RunContext
from .validate import validate_pack

DEFAULT_ROOTS = ["/mnt/skills/public", "/mnt/skills/examples", "/mnt/user-data/uploads"]


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


def _load(index_path: str) -> RunContext:
    path = Path(index_path)
    if not path.exists():
        sys.exit(f"index not found: {path} (run `capsule index` first)")
    return RunContext.from_json(path.read_text())


def cmd_index(args: argparse.Namespace) -> int:
    policy = _policy(args)
    roots = args.roots or DEFAULT_ROOTS
    context = discover(roots, policy)
    Path(args.out).write_text(context.to_json())

    skills = context.of_type("skill")
    ok = [r for r in skills if r.reconstructable]
    print(f"indexed {len(context.records)} sources from {len(roots)} root(s) -> {args.out}")
    print(f"  skills: {len(skills)}  reconstructable: {len(ok)}  gated: {len(skills) - len(ok)}")
    for source_type in sorted({r.source_type for r in context.records}):
        n = len(context.of_type(source_type))
        print(f"  {source_type:<12} {n}")
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
    )
    print(routing.report())
    if not routing.confident:
        print("\nlow confidence: not proceeding, per policy")
        return 2
    return 0


def cmd_reconstruct(args: argparse.Namespace) -> int:
    context = _load(args.index)
    policy = _policy(args)

    if args.skill:
        targets = [context.by_name(args.skill)]
        if targets[0] is None:
            sys.exit(f"no skill named {args.skill!r} in the index")
    else:
        targets = context.reconstructable()
        if not targets:
            sys.exit("no reconstructable skills in the index")

    built, refused = 0, 0
    for record in targets:
        try:
            result = reconstruct(record, args.dest, policy, overwrite=args.overwrite)
            print(result.line())
            built += 1
            if args.package:
                archive = package(result.destination, args.dest, policy)
                print(f"  packaged -> {archive}")
        except PolicyError as exc:
            print(f"REFUSED {record.name}: {exc}")
            refused += 1

    print(f"\nbuilt {built}, refused {refused}")
    if args.audit:
        print("\n--- policy audit ---")
        print(policy.audit_text())
    return 0 if built else 1


def cmd_lint(args: argparse.Namespace) -> int:
    """Run custom rules, trifecta detection and corpus diagnostics."""
    context = _load(args.index)
    policy = _policy(args)
    cfg = _config(args)

    print("== custom rules ==")
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
            print(f"  {record.name}: {decision.reason[:160]}")

        trifecta = lethal_trifecta(record, body)
        if trifecta.complete:
            print(f"  {trifecta.line()}")
        provenance = classify_input_provenance(record.name, body)
        if provenance.tier == "live":
            print(f"  {provenance.line()}")
    print(f"  {flagged} skill(s) matched at least one rule\n")

    print("== corpus diagnostics ==")
    budget = description_budget(context, cfg.description_budget)
    print(f"  {budget.line()}")
    if budget.at_risk:
        print(f"  at risk of silent truncation: {', '.join(budget.at_risk[:6])}")

    overlap = trigger_overlap(context)
    if overlap.pairs:
        print("  trigger-phrase collisions (routing ambiguity risk):")
        for a, b, score in overlap.worst():
            print(f"    {a} <-> {b}  jaccard={score}")
    else:
        print("  no trigger-phrase collisions above threshold")
    return 0


def _select(args: argparse.Namespace):
    """Resolve a skill either by name or by routing the task."""
    context = _load(args.index)
    if getattr(args, "skill", None):
        record = context.by_name(args.skill)
        if record is None:
            sys.exit(f"no skill named {args.skill!r} in the index")
        return record, ""
    if not getattr(args, "task", None):
        sys.exit("pass either --skill NAME or --task \"...\"")
    cfg = _config(args)
    routing = route(context, args.task, shortlist_size=cfg.shortlist_size,
                    min_score=cfg.min_route_score, policy=_policy(args),
                    precedence=cfg.precedence)
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
        print(f"\nadvisory (not mechanically checkable):")
        for obligation in contract.advisory[:20]:
            print(f"  - {obligation.directive[:110]}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Check a change against the selected skill's contract.

    Exit code 5 on violation, so this works as a pre-commit hook or CI gate. That
    is the point: it makes adherence a property of the diff rather than of whether
    the agent read the pack.
    """
    record, _ = _select(args)
    contract = contract_for_skill(record)

    try:
        if args.paths:
            changeset = Changeset.from_paths(args.paths, root=args.repo)
        elif args.diff:
            changeset = Changeset.from_diff(Path(args.diff).read_text(), source=args.diff)
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
    """Generate harness artifacts that enforce a contract earlier than the diff."""
    record, _ = _select(args)
    contract = contract_for_skill(record)
    commands, contents = split_obligations(contract)

    emissions = emit_all(contract, args.dest)
    if args.dry_run:
        for emission in emissions:
            print(f"--- {emission.path}  [{emission.note}]")
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
        print(f"wrote {target}  [{emission.note}]")

    print(
        f"\n{len(commands)} prohibition(s) never run (deny rules); "
        f"{len(contents)} blocked before the write lands (PreToolUse hook)."
    )
    print(
        "Hook payload field names are harness-specific. Verify with "
        "CAPSULE_HOOK_DEBUG=1 before relying on the hook; it fails open when it "
        "recognises nothing."
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Assess skills for calibration against current-generation models."""
    context = _load(args.index)
    reports = []
    for record in context.of_type("skill"):
        skill_md = Path(record.source_path) / "SKILL.md"
        if not skill_md.exists():
            continue
        body = skill_md.read_text(errors="replace")
        aux = sum(
            1 for f in Path(record.source_path).rglob("*")
            if f.is_file() and f.name != "SKILL.md"
        )
        reports.append(analyze(record, body, aux))

    counts = summarize(reports)
    threshold = {"high": 0, "medium": 1, "low": 2, "info": 3}
    floor = threshold.get(args.severity, 2)

    shown = 0
    for report in sorted(reports, key=lambda r: (threshold[r.worst_severity], -r.prescriptiveness)):
        visible = [f for f in report.findings if threshold[f.severity] <= floor]
        if not visible and not args.all:
            continue
        shown += 1
        print(report.line())
        for finding in visible:
            print(f"     {finding.line()}")

    print(
        f"\n{len(reports)} skill(s): {counts['high']} high, {counts['medium']} medium, "
        f"{counts['low']} low, {counts['clean']} clean"
    )
    if not args.all and shown == 0:
        print("nothing at or above the requested severity; pass --all to see every skill")
    print(
        "\nAltitude excludes security invariants from the prescriptiveness count. "
        "A high behavioral count is a prompt to review, not a defect."
    )
    return 1 if counts["high"] else 0


def cmd_registry(args: argparse.Namespace) -> int:
    """Pull registry skills, apply the trust gate, optionally merge into the index."""
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
        mark = "LOAD  " if decision.allowed else "BLOCK "
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


def cmd_validate(args: argparse.Namespace) -> int:
    failures = 0
    for pack in args.pack:
        ok, problems = validate_pack(pack)
        name = Path(pack).name
        if ok:
            print(f"PASS {name}")
        else:
            failures += 1
            print(f"FAIL {name}")
            for problem in problems:
                print(f"     - {problem}")
    return 1 if failures else 0


def cmd_audit(args: argparse.Namespace) -> int:
    context = _load(args.index)
    policy = _policy(args)
    for record in context.of_type("skill"):
        policy.can_reconstruct(record)
    print(policy.audit_text())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="capsule", description="Governed, replayable agent control plane.")
    parser.add_argument("--config", default="capsule.toml",
                        help="path to capsule.toml (missing file = built-in defaults)")
    parser.add_argument("--allow-restricted", action="store_true",
                        help="assert rights over restricted-license sources (audited)")
    parser.add_argument("--allow-unaudited", action="store_true",
                        help="let warn/medium registry skills load (never fail/high/critical)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="discover sources and build the run context")
    p.add_argument("--roots", nargs="*", default=None)
    p.add_argument("--out", default="capsule-index.json")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("show", help="print the condensed index")
    p.add_argument("--index", default="capsule-index.json")
    p.add_argument("--type", default=None)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("route", help="select the best skill pack for a task")
    p.add_argument("--index", default="capsule-index.json")
    p.add_argument("--task", required=True)
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("reconstruct", help="rebuild skills as portable packs")
    p.add_argument("--index", default="capsule-index.json")
    p.add_argument("--skill", default=None)
    p.add_argument("--dest", default="./packs")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--package", action="store_true", help="also emit .skill archives")
    p.add_argument("--audit", action="store_true")
    p.set_defaults(func=cmd_reconstruct)

    p = sub.add_parser("validate", help="validate one or more packs")
    p.add_argument("pack", nargs="+")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("lint", help="run custom rules and corpus diagnostics")
    p.add_argument("--index", default="capsule-index.json")
    p.set_defaults(func=cmd_lint)

    for name, help_text, fn in (
        ("brief", "emit an injectable activation block for a task", cmd_brief),
        ("contract", "show obligations extracted from a skill", cmd_contract),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--index", default="capsule-index.json")
        p.add_argument("--skill", default=None)
        p.add_argument("--task", default=None)
        if name == "contract":
            p.add_argument("--advisory", action="store_true", help="also list unenforceable directives")
        p.set_defaults(func=fn)

    p = sub.add_parser("verify", help="check a diff against a skill's contract")
    p.add_argument("--index", default="capsule-index.json")
    p.add_argument("--skill", default=None)
    p.add_argument("--task", default=None)
    p.add_argument("--repo", default=".", help="git repo to diff (default: cwd)")
    p.add_argument("--ref", default=None, help="git diff ref, e.g. --cached or main")
    p.add_argument("--diff", default=None, help="read a unified diff from a file")
    p.add_argument("--paths", nargs="*", default=None, help="verify whole files instead of a diff")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("harness", help="generate deny rules + PreToolUse hook from a contract")
    p.add_argument("--index", default="capsule-index.json")
    p.add_argument("--skill", default=None)
    p.add_argument("--task", default=None)
    p.add_argument("--dest", default="./.claude")
    p.add_argument("--dry-run", action="store_true", help="print instead of writing")
    p.set_defaults(func=cmd_harness)

    p = sub.add_parser("doctor", help="assess skill calibration for current models")
    p.add_argument("--index", default="capsule-index.json")
    p.add_argument("--severity", default="low", choices=["high", "medium", "low", "info"],
                   help="minimum severity to display (default: low)")
    p.add_argument("--all", action="store_true", help="show every skill, including clean ones")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("registry", help="query skills.sh and apply the trust gate")
    p.add_argument("--query", default=None, help="search instead of leaderboard")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--fixtures", default=None, help="replay recorded responses offline")
    p.add_argument("--api-key", default=None)
    p.add_argument("--allow-unaudited", action="store_true",
                   help="let warn/medium registry skills load (never fail/high/critical)")
    p.add_argument("--merge", action="store_true", help="write records into the index")
    p.add_argument("--index", default="capsule-index.json")
    p.set_defaults(func=cmd_registry)

    p = sub.add_parser("audit", help="replay license decisions for every indexed skill")
    p.add_argument("--index", default="capsule-index.json")
    p.set_defaults(func=cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PolicyError as exc:
        print(f"policy refusal: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
