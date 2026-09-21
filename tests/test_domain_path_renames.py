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
