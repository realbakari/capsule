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
    """Live transport. Retries 429/503 with backoff, caps total attempts.

    **There is no unauthenticated tier.** Every endpoint answers 401 without a
    credential, so `api_key` is required in practice rather than optional.

    The credential is a Vercel OIDC token, not a long-lived API key: it is
    minted per request, scoped to a (team, project), and rotates roughly every
    12 hours. Capsule takes it as a string and sends it as a bearer token,
    which is the documented form -- but a token captured into a config file
    will stop working within the day. Read it fresh from the environment.
    """

    api_key: str | None = None
    timeout: int = 15
    max_attempts: int = 3
    # Populated from the response headers on every call, so a caller can back
    # off before being told to rather than after.
    rate_limit_remaining: int | None = None
    rate_limit_reset: int | None = None

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
                    self._read_rate_limit(response.headers)
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                self._read_rate_limit(exc.headers)
                if exc.code == 401:
                    raise RegistryError(
                        "401 unauthorized: skills.sh has no unauthenticated tier. "
                        "Pass --api-key with a Vercel OIDC token "
                        "(VERCEL_OIDC_TOKEN), or use --fixtures offline."
                    ) from exc
                if exc.code == 404:
                    raise RegistryError(f"not found: {path}") from exc
                if exc.code in (429, 503) and attempt < self.max_attempts:
                    retry_after = int(exc.headers.get("Retry-After", delay) or delay)
                    time.sleep(min(retry_after, 30))
                    delay *= 2
                    continue
                if exc.code == 429:
                    raise RateLimited(int(exc.headers.get("Retry-After", 60) or 60)) from exc
                raise RegistryError(
                    f"HTTP {exc.code} for {path}: {self._error_detail(exc)}"
                ) from exc
            except urllib.error.URLError as exc:
                # Egress denial looks like this inside a sandboxed container.
                raise RegistryError(
                    f"cannot reach {BASE_URL}: {exc.reason}. "
                    "If this is a sandbox, add skills.sh to the network allowlist."
                ) from exc
        raise RegistryError(f"exhausted {self.max_attempts} attempts for {path}")

    def _read_rate_limit(self, headers) -> None:
        """Record the documented rate-limit headers, present on every response."""
        for attr, key in (
            ("rate_limit_remaining", "X-RateLimit-Remaining"),
            ("rate_limit_reset", "X-RateLimit-Reset"),
        ):
            try:
                raw = headers.get(key)
                if raw is not None:
                    setattr(self, attr, int(raw))
            except (TypeError, ValueError, AttributeError):
                continue

    @staticmethod
    def _error_detail(exc) -> str:
        """Pull `message` out of the documented error envelope.

        Errors are `{"error": code, "message": text}`. Surfacing the message
        turns "HTTP 400" into something a caller can act on.
        """
        try:
            body = json.loads(exc.read().decode())
        except Exception:
            return "no detail"
        return str(body.get("message") or body.get("error") or "no detail")


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

    def search(self, query: str, limit: int = 50, owner: str | None = None) -> list[dict]:
        """Fuzzy for one word, semantic for several. `owner` scopes to a GitHub
        owner across all of its repositories."""
        path = f"/api/v1/skills/search?q={urllib.parse.quote(query)}&limit={limit}"
        if owner:
            path += f"&owner={urllib.parse.quote(owner)}"
        return self._get(path, TTL_LISTING).get("data", [])

    def curated(self) -> list[dict]:
        """Flatten the curated set to skills.

        This endpoint does not return skills. It returns **owner groups** --
        `{owner, totalInstalls, featuredRepo, featuredSkill, skills: [...]}` --
        and the skills are nested one level down. Returning `data` directly
        handed owner dicts to `to_record`, which raised "name is required"
        because an owner group has no `name`. The endpoint was unusable.
        """
        payload = self._get("/api/v1/skills/curated", TTL_CURATED)
        skills: list[dict] = []
        for group in payload.get("data", []):
            if not isinstance(group, dict):
                continue
            nested = group.get("skills")
            if isinstance(nested, list):
                skills.extend(s for s in nested if isinstance(s, dict))
            elif group.get("id"):
                # Tolerate a flat list, in case the shape is ever simplified.
                skills.append(group)
        return skills

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
