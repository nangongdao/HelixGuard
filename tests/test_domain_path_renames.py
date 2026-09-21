"""P3a: renamed API paths keep serving under their retired names.

``docs/API_POLICY.md`` §1 forbids changing an endpoint's path in place, and §2
requires the retired path to keep working through a deprecation window. Every
rename therefore ships four legs:

* a live route on the retired path, with the same handler *and the same verb*,
* ``Deprecation`` + ``Sunset`` headers on responses to it,
* ``deprecated: true`` plus a "Migrate to ..." note in ``api/openapi.json``,
* a registry entry whose successor is itself a live path.

Drop any leg and one of these tests fails.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.deprecation import (
    DEPRECATION_HEADER,
    SUNSET_HEADER,
    _DEPRECATIONS,
    active_deprecations,
    deprecation_for,
    validate_registry,
)
from app.main import create_app

# Operations that intentionally stay out of the document: the spot-check
# landing page renders HTML for a browser and is include_in_schema=False.
OFF_SCHEMA = {"GET /api/csat/{token}"}

VERBS = {"GET", "POST", "PATCH", "PUT", "DELETE"}

# Retired names the sweep below refuses to let survive outside the files whose
# job is to declare them.  Includes the distinctive *tails* on purpose: the
# rewriter only matched "/api/..." prefixes, so a prose mention or a
# \/-escaped JS regex literal was invisible to it.
P3A_RETIRED: tuple[str, ...] = (
    # retired path prefixes
    "/api/tickets",
    "/api/csat",
    "/api/widget",
    "/api/knowledge",
    "/api/canned-responses",
    "/api/admin/agent-groups",
    "/api/analytics/costs/by_agent",
    "/api/supervisor/knowledge-gaps",
    "/api/copilot/knowledge",
    # retired tails -- prose, bare fragments, escaped regex literals
    "canned-responses",
    "agent-groups",
    "knowledge-gaps",
    "copilot/knowledge",
    "widget/sessions",
    "csat-summary",
    "costs/by_agent",
    "knowledge/drafts",
    # retired module paths
    "routers/tickets",
    "routers/knowledge",
    "routers/csat",
    "widget_routes",
    "widget_token.py",
    "app.widget_token",
)

# P3b: the conversation -> review-case module and path rename (T5). The
# retired *paths* must not outlive the files that declare the aliases; the
# retired *module* name must not survive anywhere the .bak anchors need it.
P3B_RETIRED: tuple[str, ...] = (
    "/api/conversations",
    "/api/v2/conversations",
    "/api/conversation-labels",
    "v2/conversations",
    "conversation-labels",
    # the \/-escaped regex-literal shape the string sweep cannot see
    # (P3a's own lesson, repeated at P3b on five test assertions)
    "\\/api\\/conversations",
    "app.routers.conversations",
    "routers/conversations",
)

# Files that are *supposed* to contain retired names.
P3A_SWEEP_EXEMPT: frozenset[str] = frozenset(
    {
        "app/deprecation.py",  # the registry names every retired path
        "app/main.py",  # inline @legacy_route on the FastAPI instance
        "app/portal_routes.py",  # the submission-portal aliases
        "api/openapi.json",  # generated; must carry the deprecated operations
        "CHANGELOG.md",  # release notes spell retired -> current
        "docs/DOMAIN.md",  # the term contract itself
        "docs/DOMAIN_MIGRATION_PLAN.md",
        "docs/adr/0002-sse-seq-streaming.md",  # dated "2023 (Phase 23)" narrative
        "scripts/split_main.py",  # line anchors into the frozen .bak
        "scripts/rebuild_main.py",  # ditto -- regenerates the frozen snapshot
        "tests/test_deprecation.py",  # fixtures drive the registry via retired ops
        "app/main.py.bak",  # frozen 1.3.0 snapshot; rebuild_main.py reads it
        "app/database.py.bak",
        "tests/ui_csat.py",  # P5 renames this file and its artifact names
        "tests/test_domain_path_renames.py",  # this guard
    }
)

P3A_SWEEP_EXEMPT_PREFIX: tuple[str, ...] = (
    # Aliases are declared beside their primary route.  That the retired set is
    # exactly the registered set is asserted by RegistryCoverageTests.
    "app/routers/",
    # Dated factual archives (docs/DOMAIN.md 6).
    "docs/RELEASE_",
    "docs/PHASE_",
    "docs/RUNBOOK_",
    "docs/PROGRESS_REPORT_",
    "supplychain/",
)

# Generated / local-only trees.  These are only consulted by the ``rglob``
# fallback in ``_swept_files``: the primary path reads ``git ls-files`` and
# therefore never sees them at all.
P3A_SWEEP_SKIP_NAMES: tuple[str, ...] = (
    ".git",
    "node_modules",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".workbuddy",
    "artifacts",
    "build",
    "dist",
    "htmlcov",
    ".tox",
    ".mypy_cache",
)

P3A_SWEEP_SKIP_SUFFIXES: tuple[str, ...] = (".egg-info", ".dist-info")


class RegistryCoverageTests(unittest.TestCase):
    """Every rename is registered, and every registration points somewhere real."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            database_path=Path(self._tmp.name) / "rename.db",
            turn_worker_enabled=False,
        )
        self.app = create_app(self.settings)
        self.spec = self.app.openapi()

    def tearDown(self) -> None:
        self.app.state.services.database.close()
        self._tmp.cleanup()

    def test_registry_is_valid_inside_its_window_and_non_empty(self) -> None:
        self.assertTrue(_DEPRECATIONS, "the migration must register its aliases")
        self.assertEqual(validate_registry(), [])
        self.assertEqual(len(active_deprecations()), len(_DEPRECATIONS))

    def test_every_operation_key_is_method_space_path(self) -> None:
        for entry in _DEPRECATIONS:
            method, _, path = entry.operation.partition(" ")
            self.assertIn(method, VERBS, entry.operation)
            self.assertTrue(path.startswith("/api/"), entry.operation)
            self.assertLess(entry.deprecated_on, entry.sunset_on, entry.operation)
            self.assertTrue(entry.successor.startswith("/"), entry.operation)

    def test_successors_are_live_paths_in_the_spec(self) -> None:
        missing = sorted(
            entry.operation for entry in _DEPRECATIONS if entry.successor not in self.spec["paths"]
        )
        self.assertEqual(missing, [], f"successor path is not a live route: {missing}")

    def test_alias_operations_exist_and_are_flagged_deprecated(self) -> None:
        """The retired verb must actually be registered, not just the path.

        Registering an alias without ``methods`` makes FastAPI default it to
        GET, which silently drops every POST/PATCH/DELETE alias — the failure
        this test exists to catch.
        """
        paths = self.spec["paths"]
        problems: list[str] = []
        for entry in _DEPRECATIONS:
            method, _, path = entry.operation.partition(" ")
            operation = paths.get(path, {}).get(method.lower())
            if operation is None:
                if entry.operation not in OFF_SCHEMA:
                    problems.append(f"{entry.operation}: not registered")
                continue
            if not operation.get("deprecated"):
                problems.append(f"{entry.operation}: not marked deprecated")
            elif f"Migrate to {entry.successor}" not in str(operation.get("description")):
                problems.append(f"{entry.operation}: no migration note")
        self.assertEqual(problems, [], problems)

    def test_deprecated_operations_all_come_from_the_registry(self) -> None:
        stale = sorted(
            f"{method.upper()} {path}"
            for path, ops in self.spec["paths"].items()
            for method, op in ops.items()
            if isinstance(op, dict)
            and op.get("deprecated")
            and deprecation_for(f"{method.upper()} {path}") is None
        )
        self.assertEqual(stale, [], f"deprecated without a registry entry: {stale}")

    def test_alias_documents_itself_like_its_successor(self) -> None:
        """A retired path keeps the summary/tags of the route that replaced it."""
        paths = self.spec["paths"]
        for entry in _DEPRECATIONS:
            if entry.operation in OFF_SCHEMA:
                continue
            method, _, path = entry.operation.partition(" ")
            alias = paths[path][method.lower()]
            successor = paths[entry.successor][method.lower()]
            self.assertEqual(alias.get("summary"), successor.get("summary"), path)
            self.assertEqual(alias.get("tags"), successor.get("tags"), path)

    def test_path_placeholders_match_declared_path_parameters(self) -> None:
        """Renaming a placeholder means renaming the handler signature too.

        Rewriting ``/api/tickets/{ticket_id}`` to ``/api/appeals/{appeal_id}``
        without touching the handler demotes ``ticket_id`` from a *path*
        parameter to a *query* parameter. The URL still matches, so nothing
        raises — every real call just starts returning 422. Renaming the
        placeholder is only safe as a pair.
        """
        problems: list[str] = []
        for path, operations in sorted(self.spec["paths"].items()):
            expected = set(re.findall(r"\{([^{}]+)\}", path))
            if not expected:
                continue
            for method, operation in sorted(operations.items()):
                if not isinstance(operation, dict):
                    continue
                declared = {
                    parameter["name"]
                    for parameter in operation.get("parameters", [])
                    if parameter.get("in") == "path"
                }
                if declared != expected:
                    problems.append(
                        f"{method.upper()} {path}: template {sorted(expected)} "
                        f"declares {sorted(declared)}"
                    )
        self.assertEqual(problems, [], problems)


class RetiredPathServingTests(unittest.TestCase):
    """The retired path must answer, with the contract headers, end to end."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            database_path=Path(self._tmp.name) / "serve.db",
            turn_worker_enabled=False,
            auth_mode="api_key",
            api_keys_json=_keys_json(),
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.headers = _auth()

    def tearDown(self) -> None:
        self.app.state.services.database.close()
        self._tmp.cleanup()

    def test_retired_path_serves_and_carries_deprecation_headers(self) -> None:
        legacy = self.client.get("/api/tickets", headers=self.headers)
        current = self.client.get("/api/appeals", headers=self.headers)
        self.assertEqual(legacy.status_code, 200, legacy.text)
        self.assertEqual(current.status_code, 200, current.text)
        self.assertIn(DEPRECATION_HEADER, legacy.headers)
        self.assertIn(SUNSET_HEADER, legacy.headers)
        self.assertNotIn(DEPRECATION_HEADER, current.headers)
        self.assertNotIn(SUNSET_HEADER, current.headers)

    def test_retired_and_current_paths_agree(self) -> None:
        legacy = self.client.get("/api/canned-responses", headers=self.headers)
        current = self.client.get("/api/canned-verdicts", headers=self.headers)
        self.assertEqual(legacy.status_code, 200, legacy.text)
        self.assertEqual(legacy.json(), current.json())

    def test_templated_retired_path_resolves_to_the_legacy_operation(self) -> None:
        conversation_id = self.client.post(
            "/api/conversations",
            json={"customer_name": "Rename probe"},
            headers=self.headers,
        ).json()["id"]
        created = self.client.post(
            "/api/appeals",
            json={"conversation_id": conversation_id, "subject": "Rename probe"},
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        appeal_id = created.json()["id"]
        detail = self.client.get(f"/api/tickets/{appeal_id}", headers=self.headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertIn(DEPRECATION_HEADER, detail.headers)

    def test_health_and_current_paths_are_never_deprecated(self) -> None:
        for url in ("/health/ready", "/api/appeals", "/api/policy"):
            response = self.client.get(url, headers=self.headers)
            self.assertNotIn(DEPRECATION_HEADER, response.headers, url)


class RetiredIdentifierSweepTests(unittest.TestCase):
    """No retired name may survive outside a file whose job is to name it.

    The rewriter walked a fixed ``INCLUDE_DIRS`` list (``app tests scripts
    clients frontend/src docs``), so its "converged to 0" check was a statement
    about that subset, not about the tree. Two live verifiers under ``desktop/``
    kept posting to ``/api/canned-responses`` and ``/api/knowledge/drafts``, and
    the rewriter's own report could never say so -- a partial scan that reports
    a whole is worse than no scan, because it reads as coverage.

    Drop an entry from the exempt sets and this test fails; retire a name and
    forget one directory and it fails too.
    """

    def test_retired_identifiers_stay_inside_the_files_that_declare_them(self) -> None:
        root = Path(__file__).resolve().parents[1]
        universe = _swept_files(root)
        # A sweep over an empty universe passes vacuously -- which is exactly the
        # "scanned a subset, reported the whole" failure this test exists for.
        self.assertGreater(len(universe), 200, f"only {len(universe)} files to sweep")
        offenders: list[str] = []
        for path in universe:
            relative = path.relative_to(root).as_posix()
            if _sweep_exempt(relative):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            hits = sorted({segment for segment in P3A_RETIRED + P3B_RETIRED if segment in text})
            if hits:
                offenders.append(f"{relative}: {hits}")
        self.assertEqual(offenders, [], offenders)


def _swept_files(root: Path) -> list[Path]:
    """The universe for the sweep: what ships, not whatever happens to exist.

    ``git ls-files`` is authoritative, and the difference is not academic: the
    first CI run of this guard failed on ``helix_guard.egg-info/SOURCES.txt`` --
    a file ``pip install -e .`` generates in CI (from a warm pip cache) that
    never exists in a local checkout. A guard that disagrees with itself between
    local and CI is one nobody will trust, and a filesystem walk invites exactly
    that class of surprise. The fallback keeps the guard usable without git.
    """
    tracked = _git_tracked(root)
    if tracked is not None:
        return tracked
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and not _generated(path.relative_to(root).as_posix())
    ]


def _git_tracked(root: Path) -> list[Path] | None:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    names = completed.stdout.decode("utf-8", errors="replace").split("\0")
    return [root / name for name in names if name]


def _generated(relative: str) -> bool:
    parts = relative.split("/")
    if any(part in P3A_SWEEP_SKIP_NAMES for part in parts):
        return True
    return any(part.endswith(P3A_SWEEP_SKIP_SUFFIXES) for part in parts)


def _sweep_exempt(relative: str) -> bool:
    if relative in P3A_SWEEP_EXEMPT:
        return True
    return any(
        relative == prefix.rstrip("/") or relative.startswith(prefix)
        for prefix in P3A_SWEEP_EXEMPT_PREFIX
    )


def _auth() -> dict[str, str]:
    keys = json.loads(_keys_json())
    key = next(iter(keys))
    return {"X-API-Key": key, "X-Tenant-Id": keys[key]["tenant_id"]}


def _keys_json() -> str:
    return json.dumps(
        {
            "rename-admin-key-00000001": {
                "tenant_id": "demo",
                "actor_id": "admin",
                "role": "admin",
            }
        }
    )


if __name__ == "__main__":
    unittest.main()
