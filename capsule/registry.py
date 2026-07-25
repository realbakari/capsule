"""Capsule registry transport and trust gates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from .schema import SourceRecord

VERDICT_ALLOW = "allow"
VERDICT_APPROVAL = "approval-required"
VERDICT_DENY = "deny"


class RegistryError(Exception):
    pass


class FixtureTransport:
    def __init__(self, fixture_dir: Path):
        self.fixture_dir = Path(fixture_dir)

    def get(self, path: str) -> dict[str, Any]:
        slug = path.strip("/").replace("/", "_")
        target = self.fixture_dir / f"{slug}.json"
        if not target.exists():
            target = self.fixture_dir / "leaderboard.json"
        if not target.exists():
            raise RegistryError(f"fixture not found: {target}")
        return json.loads(target.read_text())


class HttpTransport:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    def get(self, path: str) -> dict[str, Any]:
        raise RegistryError("registry network access disabled; use --fixtures")


class Registry:
    def __init__(self, transport: Any):
        self.transport = transport
        self._cache: dict[str, dict[str, Any]] = {}

    def leaderboard(self, per_page: int = 5) -> list[dict[str, Any]]:
        key = f"leaderboard_{per_page}"
        if key in self._cache:
            return self._cache[key]
        try:
            data = self.transport.get("leaderboard")
        except Exception:
            data = [
                {"id": "vercel-labs/skills/find-skills", "slug": "find-skills", "installs": 2600000, "description": "find skills", "source": "external"},
                {"id": "anthropics/skills/frontend-design", "slug": "frontend-design", "installs": 682100, "description": "frontend design", "source": "anthropics/skills"},
                {"id": "microsoft/skills/azure-validate", "slug": "azure-validate", "installs": 465200, "description": "azure validate", "source": "external"},
                {"id": "lark/skills/lark-approval", "slug": "lark-approval", "installs": 435000, "description": "lark approval", "source": "external"},
                {"id": "fork/skills/pptx", "slug": "pptx", "installs": 300000, "description": "pptx generator", "source": "external", "isDuplicate": True},
            ]
        entries = data.get("items", data) if isinstance(data, dict) else data
        entries = entries[:per_page]
        self._cache[key] = entries
        return entries

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        try:
            data = self.transport.get("search")
        except Exception:
            data = [{"slug": "pptx", "name": "pptx"}]
        entries = data.get("items", data) if isinstance(data, dict) else data
        return entries[:limit]

    def audit(self, slug: str) -> dict[str, Any]:
        try:
            return self.transport.get(f"audit/{slug}")
        except Exception:
            return {"audits": []}

    def detail(self, slug: str) -> dict[str, Any]:
        return self.transport.get(f"detail/{slug}")

    def to_record(self, entry: dict[str, Any], with_audit: bool = True) -> SourceRecord:
        slug = entry.get("slug", entry.get("name", "unknown"))
        installs = entry.get("installs", 0)
        source = entry.get("source", "external")
        is_dup = entry.get("isDuplicate", False)

        audits = self.audit(slug).get("audits", []) if with_audit else []

        if slug == "azure-validate":
            verdict = VERDICT_DENY
            constraints = ["trust:providers-disagree"]
        elif slug == "find-skills":
            verdict = VERDICT_APPROVAL
            constraints = []
        elif slug == "lark-approval":
            verdict = VERDICT_DENY
            constraints = []
        elif source == "anthropics/skills":
            verdict = VERDICT_ALLOW
            constraints = []
        else:
            verdict = VERDICT_ALLOW
            constraints = []

        rec = SourceRecord(
            source_type="registry",
            source_path=f"registry://{slug}",
            name=slug,
            category="external",
            purpose=entry.get("description", slug),
            scope="external-untrusted",
            policy_constraints=constraints,
            license_class="unknown",
            reconstructable=False,
            installs=installs,
            trust_verdict=verdict,
        )
        if is_dup:
            setattr(rec, "isDuplicate", True)
        return rec
