"""Registry client for the skills.sh catalog.

Design constraint: the sandbox egress allowlist does not include skills.sh
(`x-deny-reason: host_not_allowed`), so this client must be usable and testable
with no network at all. The transport is therefore injectable:

  - `HttpTransport`  — real requests, honours rate limits and backoff
  - `FixtureTransport` — replays recorded JSON from a fixtures directory

Everything above the transport is identical either way, so the offline tests
exercise the same code path production would.

Endpoints used (see https://skills.sh/docs/api):
  GET /api/v1/skills                      leaderboard, paginated
  GET /api/v1/skills/search?q=            fuzzy (1 word) / semantic (n words)
  GET /api/v1/skills/curated              first-party curated set
  GET /api/v1/skills/{source}/{slug}      detail: hash + full file tree
  GET /api/v1/skills/audit/{source}/{slug}  multi-provider security audits
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .schema import SourceRecord
from .trust import VERDICT_ALLOW, VERDICT_APPROVAL, aggregate_api

BASE_URL = "https://skills.sh"
USER_AGENT = "capsule/0.1 (+https://github.com/capsule)"

# Cache-Control windows documented by the API, mirrored locally so Capsule is a
# well-behaved client rather than a polling hazard.
TTL_LISTING = 60
TTL_DETAIL = 300
TTL_CURATED = 300


class RegistryError(RuntimeError):
    pass


class RateLimited(RegistryError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited; retry after {retry_after}s")
        self.retry_after = retry_after


class Transport(Protocol):
    def get(self, path: str) -> dict: ...


@dataclass
class FixtureTransport:
    """Replays recorded API responses. Path is slugified to a filename."""

    fixtures_dir: Path

    def get(self, path: str) -> dict:
        name = path.strip("/").replace("/", "_").replace("?", "__").replace("&", "_")
        candidate = Path(self.fixtures_dir) / f"{name}.json"
        if not candidate.exists():
            raise RegistryError(f"no fixture for {path} (looked for {candidate.name})")
        return json.loads(candidate.read_text())


@dataclass
class HttpTransport:
    """Live transport. Retries 429/503 with backoff, caps total attempts."""

    api_key: str | None = None
    timeout: int = 15
    max_attempts: int = 3

    def get(self, path: str) -> dict:
        url = urllib.parse.urljoin(BASE_URL, path)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        delay = 1.0
        for attempt in range(1, self.max_attempts + 1):
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise RegistryError(f"not found: {path}") from exc
                if exc.code in (429, 503) and attempt < self.max_attempts:
                    retry_after = int(exc.headers.get("Retry-After", delay) or delay)
                    time.sleep(min(retry_after, 30))
                    delay *= 2
                    continue
                if exc.code == 429:
                    raise RateLimited(int(exc.headers.get("Retry-After", 60) or 60)) from exc
                raise RegistryError(f"HTTP {exc.code} for {path}") from exc
            except urllib.error.URLError as exc:
                # Egress denial looks like this inside a sandboxed container.
                raise RegistryError(
                    f"cannot reach {BASE_URL}: {exc.reason}. "
                    "If this is a sandbox, add skills.sh to the network allowlist."
                ) from exc
        raise RegistryError(f"exhausted {self.max_attempts} attempts for {path}")


class Registry:
    """Typed access to the catalog, with a small TTL cache."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self._cache: dict[str, tuple[float, dict]] = {}

    def _get(self, path: str, ttl: int) -> dict:
        now = time.time()
        hit = self._cache.get(path)
        if hit and now - hit[0] < ttl:
            return hit[1]
        payload = self.transport.get(path)
        self._cache[path] = (now, payload)
        return payload

    # -- endpoints -------------------------------------------------------------

    def leaderboard(self, view: str = "all-time", page: int = 0, per_page: int = 100) -> list[dict]:
        path = f"/api/v1/skills?view={view}&page={page}&per_page={per_page}"
        return self._get(path, TTL_LISTING).get("data", [])

    def search(self, query: str, limit: int = 50) -> list[dict]:
        path = f"/api/v1/skills/search?q={urllib.parse.quote(query)}&limit={limit}"
        return self._get(path, TTL_LISTING).get("data", [])

    def curated(self) -> list[dict]:
        return self._get("/api/v1/skills/curated", TTL_CURATED).get("data", [])

    def detail(self, skill_id: str) -> dict:
        return self._get(f"/api/v1/skills/{skill_id}", TTL_DETAIL)

    def audit(self, skill_id: str) -> dict:
        """Returns an empty audits list on 404 — un-audited, not audited-clean."""
        try:
            return self._get(f"/api/v1/skills/audit/{skill_id}", TTL_DETAIL)
        except RegistryError:
            return {"id": skill_id, "audits": []}

    # -- condensation ----------------------------------------------------------

    def to_record(self, skill: dict, with_audit: bool = True) -> SourceRecord:
        """Condense a catalog entry into a Capsule run-context record."""
        skill_id = skill.get("id", "")
        source = skill.get("source", "")
        slug = skill.get("slug") or skill_id.rsplit("/", 1)[-1]
        installs = int(skill.get("installs", 0) or 0)

        verdict = None
        if with_audit:
            verdict = aggregate_api(self.audit(skill_id))

        constraints = [f"registry:{source}"]
        if skill.get("isDuplicate"):
            constraints.append("duplicate:flagged-fork-or-copy")
        if verdict:
            constraints.append(f"trust:{verdict.verdict}")
            if verdict.dissenting:
                constraints.append("trust:providers-disagree")

        # Remote records are lower confidence than local ones by construction:
        # nothing has been read from disk and the body may not be fetched yet.
        confidence = 0.4
        if skill.get("name"):
            confidence += 0.1
        if verdict and verdict.verdict == VERDICT_ALLOW:
            confidence += 0.15
        if installs >= 1000:
            confidence += 0.05

        return SourceRecord(
            source_type="registry",
            source_path=skill.get("url") or f"{BASE_URL}/{skill_id}",
            name=slug,
            category="registry-skill",
            purpose=skill.get("name") or slug,
            trigger_phrases=[slug, slug.replace("-", " ")],
            shortcuts=[f"npx skills add {skill.get('installUrl', '')}".strip()],
            scope="external-untrusted",
            policy_constraints=constraints,
            reload_rules="on-hash-change; re-audit before every load",
            confidence=round(min(confidence, 1.0), 2),
            license_class="unknown",
            reconstructable=False,
            content_hash=str(skill.get("hash") or ""),
            installs=installs,
            is_duplicate=bool(skill.get("isDuplicate", False)),
            registry_id=skill_id,
            trust_verdict=verdict.verdict if verdict else "",
            trust_status=verdict.status if verdict else "",
            trust_risk=verdict.risk_level if verdict else "",
            trust_reason=verdict.reason if verdict else "",
        )

    def loadable(self, records: list[SourceRecord]) -> list[SourceRecord]:
        """Only records the trust gate clears outright."""
        return [r for r in records if r.trust_verdict == VERDICT_ALLOW]

    def needs_approval(self, records: list[SourceRecord]) -> list[SourceRecord]:
        return [r for r in records if r.trust_verdict == VERDICT_APPROVAL]
