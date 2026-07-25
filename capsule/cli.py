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

from .color import bold, cyan, dim, fail_badge, green, pass_badge, red, warn_badge, yellow
from .config import CapsuleConfig, description_budget, lethal_trifecta, trigger_overlap
from .contract import Changeset, brief, contract_for_skill, verify
from .doctor import SkillAudit, audit_context, audit_skill
from .evals import EvalSuite, load_evals, run_eval
from .harness import (
    ArtifactEntry, SkillMeta, classify_input_provenance,
    emit_all, emit_all_plugins, split_obligations,
)
from .discover import discover
from .health import analyze, summarize
from .registry import FixtureTransport, HttpTransport, Registry, RegistryError
from .policy import Policy, PolicyError
from .reconstruct import package, reconstruct
from .router import route
from .schema import RunContext
from .schema_export import export_schemas
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
    print(bold(f"indexed {len(context.records)} sources from {len(roots)} root(s) -> {args.out}"))
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
    )
    print(routing.report())
    return 0 if routing.selected else 2


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


def cmd_doctor(args: argparse.Namespace) -> int:
    """Model calibration doctor."""
    index_path = getattr(args, "index", None)
    if index_path and Path(index_path).exists():
        context = _load(index_path)
        audits = audit_context(context)
    else:
        # Audit skills/ in current dir
        context = discover(["."], Policy())
        audits = audit_context(context)

    if not audits:
        print("no skills found for doctor audit")
        return 0

    print(bold("Capsule Doctor — Model Calibration Audit:"))
    print("-" * 60)
    for a in audits:
        print(a.report())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capsule")
    subparsers = parser.add_subparsers(dest="subcommand")

    p_index = subparsers.add_parser("index")
    p_index.add_argument("--roots", nargs="*")
    p_index.add_argument("--out", default="capsule-index.json")
    p_index.add_argument("--by-category", action="store_true")
    p_index.add_argument("--lifecycle")

    p_show = subparsers.add_parser("show")
    p_show.add_argument("--index", default="capsule-index.json")
    p_show.add_argument("--type")

    p_route = subparsers.add_parser("route")
    p_route.add_argument("--index", default="capsule-index.json")
    p_route.add_argument("--task", required=True)

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

    args = parser.parse_args(argv)
    if args.subcommand == "index":
        return cmd_index(args)
    elif args.subcommand == "show":
        return cmd_show(args)
    elif args.subcommand == "route":
        return cmd_route(args)
    elif args.subcommand == "eval":
        return cmd_eval(args)
    elif args.subcommand == "emit-plugins":
        return cmd_emit_plugins(args)
    elif args.subcommand == "schema":
        return cmd_schema(args)
    elif args.subcommand == "doctor":
        return cmd_doctor(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())


