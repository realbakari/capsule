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
    return 0 if routing.selected else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capsule")
    subparsers = parser.add_subparsers(dest="subcommand")

    p_index = subparsers.add_parser("index")
    p_index.add_argument("--roots", nargs="*")
    p_index.add_argument("--out", default="capsule-index.json")

    p_show = subparsers.add_parser("show")
    p_show.add_argument("--index", default="capsule-index.json")
    p_show.add_argument("--type")

    p_route = subparsers.add_parser("route")
    p_route.add_argument("--index", default="capsule-index.json")
    p_route.add_argument("--task", required=True)

    args = parser.parse_args(argv)
    if args.subcommand == "index":
        return cmd_index(args)
    elif args.subcommand == "show":
        return cmd_show(args)
    elif args.subcommand == "route":
        return cmd_route(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
