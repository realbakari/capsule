"""Capsule skill reconstruction and packaging."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from .schema import RunContext, SourceRecord
from .policy import Policy, PolicyError
from .validate import validate_pack


def reconstruct(
    context: RunContext,
    dest: str | Path,
    skill_name: Optional[str] = None,
    policy: Optional[Policy] = None,
    overwrite: bool = False,
) -> list[Path]:
    policy = policy or Policy()
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    records = [context.by_name(skill_name)] if skill_name else context.records
    records = [r for r in records if r is not None]

    out_paths = []
    for r in records:
        dec = policy.can_reconstruct(r)
        if not dec.allowed:
            raise PolicyError(f"cannot reconstruct {r.name}: {dec.reason}")

        pack_dir = dest_path / r.name
        if pack_dir.exists() and not overwrite:
            raise RuntimeError(f"pack directory {pack_dir} exists (use --overwrite)")

        if pack_dir.exists():
            shutil.rmtree(pack_dir)

        pack_dir.mkdir(parents=True, exist_ok=True)
        skill_md = pack_dir / "SKILL.md"
        src_skill = Path(r.source_path) / "SKILL.md"

        if src_skill.exists():
            skill_md.write_text(src_skill.read_text())
        else:
            skill_md.write_text(f"---\nname: {r.name}\ndescription: {r.purpose}\n---\n# {r.name}\n")

        out_paths.append(pack_dir)

    return out_paths


def package(pack_dir: str | Path) -> Path:
    p = Path(pack_dir)
    archive = p.with_suffix(".skill")
    shutil.make_archive(str(p), "zip", p)
    zip_path = p.with_suffix(".zip")
    if zip_path.exists():
        zip_path.rename(archive)
    return archive
