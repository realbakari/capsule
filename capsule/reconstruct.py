"""Reconstruction: rebuild a discovered skill as a portable pack.

Fidelity rules, in force for every reconstruction:
  - the SKILL.md body is preserved verbatim; Capsule does not paraphrase
    workflow logic, validation behavior, dependencies or failure conditions
  - supporting scripts/, references/, assets/, templates/, tests/ come along
  - license text and a PROVENANCE.md travel with the pack
  - the pack is validated before it is considered built
  - a source hash is recorded so drift is detectable on rebuild

Reconstruction is refused unless the policy license gate allows it.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .policy import Policy, PolicyError
from .schema import SourceRecord
from .validate import validate_pack

CARRY_DIRS = ("scripts", "references", "reference", "assets", "templates", "tests", "examples", "core")
EXCLUDE_DIRS = {"__pycache__", "node_modules", ".git"}
EXCLUDE_GLOBS = ("*.pyc", ".DS_Store")


@dataclass
class Reconstruction:
    name: str
    source: str
    destination: str
    files_copied: int
    valid: bool
    problems: list[str]
    source_hash: str

    def line(self) -> str:
        state = "valid" if self.valid else f"INVALID ({'; '.join(self.problems)})"
        return f"{self.name}: {self.files_copied} files -> {self.destination} [{state}]"


def _skip(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return True
    return any(path.match(g) for g in EXCLUDE_GLOBS)


def _hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and not _skip(path):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _provenance(record: SourceRecord, source_hash: str) -> str:
    return (
        f"# Provenance\n\n"
        f"- **Skill name**: {record.name}\n"
        f"- **Reconstructed from**: `{record.source_path}`\n"
        f"- **License class**: {record.license_class}\n"
        f"- **Source tree hash**: `{source_hash}`\n"
        f"- **SKILL.md hash**: `{record.content_hash}`\n"
        f"- **Category**: {record.category}\n"
        f"- **Scope**: {record.scope}\n\n"
        f"This pack was rebuilt by Capsule. The SKILL.md body is preserved verbatim from\n"
        f"the source; workflow logic, validation behavior, dependencies and failure\n"
        f"conditions are unmodified. Original license text is retained in this folder.\n\n"
        f"Rebuild is deterministic: re-running reconstruction against an unchanged source\n"
        f"reproduces the same tree hash. A changed hash means the upstream skill drifted\n"
        f"and the pack must be regenerated rather than hand-edited.\n"
    )


def reconstruct(
    record: SourceRecord,
    dest_root: str | Path,
    policy: Policy,
    overwrite: bool = False,
) -> Reconstruction:
    """Rebuild one skill as a portable pack under dest_root."""
    gate = policy.can_reconstruct(record)
    policy.enforce(gate)

    source = Path(record.source_path)
    dest = Path(dest_root) / record.name
    policy.enforce(policy.can_write(dest))

    if dest.exists():
        if not overwrite:
            raise PolicyError(f"{dest} already exists; refusing to overwrite without approval")
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    copied = 0

    skill_md = source / "SKILL.md"
    shutil.copy2(skill_md, dest / "SKILL.md")
    copied += 1

    license_file = source / "LICENSE.txt"
    if license_file.exists():
        shutil.copy2(license_file, dest / "LICENSE.txt")
        copied += 1

    for item in sorted(source.iterdir()):
        if item.name in ("SKILL.md", "LICENSE.txt") or item.name.startswith("."):
            continue
        if item.is_dir():
            if item.name not in CARRY_DIRS:
                continue
            for src_file in sorted(item.rglob("*")):
                if src_file.is_file() and not _skip(src_file):
                    rel = src_file.relative_to(source)
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, target)
                    copied += 1
        elif item.is_file() and not _skip(item):
            shutil.copy2(item, dest / item.name)
            copied += 1

    source_hash = _hash_tree(source)
    (dest / "PROVENANCE.md").write_text(_provenance(record, source_hash))
    copied += 1

    valid, problems = validate_pack(dest)
    return Reconstruction(
        name=record.name,
        source=str(source),
        destination=str(dest),
        files_copied=copied,
        valid=valid,
        problems=problems,
        source_hash=source_hash,
    )


def package(pack_dir: str | Path, out_dir: str | Path, policy: Policy) -> Path:
    """Zip a validated pack into a distributable .skill archive."""
    pack = Path(pack_dir)
    valid, problems = validate_pack(pack)
    if not valid:
        raise PolicyError(f"refusing to package invalid pack {pack}: {problems}")

    out = Path(out_dir)
    policy.enforce(policy.can_write(out))
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f"{pack.name}.skill"

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(pack.rglob("*")):
            if path.is_file() and not _skip(path):
                zf.write(path, Path(pack.name) / path.relative_to(pack))
    return archive
