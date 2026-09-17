"""ROADMAP H02 (2.19.0): server-side upload receipt for operator attachments.

The same failure shape the send receipt (v47) closes for messages applies one
step earlier in the same operator flow: the file is uploaded, the response is
lost to a network blip or a browser timeout, and the workspace retries the
*upload*. Without a receipt the retry stores the bytes a second time, charges
the tenant quota twice, and leaves a duplicate row that the next message can
bind to — so one attempt becomes two attachments.

This pins the receipt contract for ``POST /api/attachments``:

- an ``Idempotency-Key`` binds one upload attempt to the row it produced;
- replaying the key returns that same row with ``X-Idempotent-Replay`` and
  stores nothing (no second row, no second ``attachment.uploaded`` audit, no
  second quota charge);
- the key is scoped per conversation *and* per tenant, so the same string
  elsewhere is a different request and can never resolve to this row;
- a key replayed with a different file (name, size or digest) is a conflict
  (409), never a silent success — a corrected file must not be swallowed as a
  replay of the earlier bytes;
- no key at all keeps today's behaviour (every POST is its own attachment);
- a rejected upload leaves no receipt, so a retry after the caller fixes the
  problem still works;
- the receipt column never reaches the API projection (``AttachmentOut`` is a
  strict model and ``_out`` filters rather than whitelists).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ADMIN_KEY = "att-idem-admin-key-001"
OTHER_KEY = "att-idem-other-key-001"

KEY = "up-11111111-2222-3333-4444-555555555555"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 12


def _settings(db_path: Path, **overrides: Any) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"},
        OTHER_KEY: {"tenant_id": "demo", "actor_id": "other", "role": "admin"},
    }
    defaults: dict[str, Any] = {
        "database_path": db_path,
        "auth_mode": "api_key",
        "api_keys_json": json.dumps(principals),
        "rate_limit_per_minute": 10000,
        "docs_enabled": False,
        "turn_worker_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class AttachmentIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "att-idem.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.other = {"X-API-Key": OTHER_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------- helpers

    def _open_conversation(self) -> str:
        conv = self.client.post(
            "/api/conversations", json={"customer_name": "S"}, headers=self.admin
        ).json()
        self.client.post(f"/api/conversations/{conv['id']}/accept", headers=self.admin)
        return conv["id"]

    def _upload(
        self,
        conversation_id: str,
        *,
        filename: str = "a.png",
        payload: bytes = PNG,
        content_type: str = "image/png",
        key: str | None = KEY,
        headers: dict[str, str] | None = None,
    ):
        merged = dict(headers or self.admin)
        if key is not None:
            merged["Idempotency-Key"] = key
        return self.client.post(
            "/api/attachments",
            files={"file": (filename, payload, content_type)},
            data={"conversation_id": conversation_id},
            headers=merged,
        )

    def _stored(self, conversation_id: str) -> list[dict[str, Any]]:
        return self.services.attachments.list_for_conversation("demo", conversation_id)

    def _audit_events(self, conversation_id: str, event_type: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self.services.database.list_audit("demo", conversation_id)
            if row["event_type"] == event_type
        ]

    # ------------------------------------------------------------ replays

    def test_replay_returns_the_original_attachment_and_stores_one(self) -> None:
        conversation_id = self._open_conversation()
        first = self._upload(conversation_id)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertNotIn("X-Idempotent-Replay", first.headers)

        retry = self._upload(conversation_id)
        self.assertEqual(retry.status_code, 201, retry.text)
        self.assertEqual(retry.headers.get("X-Idempotent-Replay"), "true")
        self.assertEqual(retry.json()["id"], first.json()["id"])
        self.assertEqual(len(self._stored(conversation_id)), 1)

    def test_replay_neither_audits_nor_charges_the_quota_twice(self) -> None:
        conversation_id = self._open_conversation()
        first = self._upload(conversation_id)
        self._upload(conversation_id)

        self.assertEqual(len(self._audit_events(conversation_id, "attachment.uploaded")), 1)
        self.assertEqual(self.services.attachments.quota_used("demo"), len(PNG))
        self.assertEqual(first.json()["size_bytes"], len(PNG))

    def test_replay_survives_a_process_restart(self) -> None:
        conversation_id = self._open_conversation()
        first = self._upload(conversation_id)
        self.services.database.close()

        from app.database import Database

        reopened = Database(self.db_path)
        try:
            stored = reopened.get_attachment_by_operator_key("demo", conversation_id, KEY)
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored["id"], first.json()["id"])
        finally:
            reopened.close()

    def test_retry_after_a_lost_response_returns_the_same_attachment(self) -> None:
        """The failure shape: the server stored the bytes, the client never saw it."""
        conversation_id = self._open_conversation()
        committed = self._upload(conversation_id)
        lost = self._upload(conversation_id)
        self.assertEqual(lost.json()["id"], committed.json()["id"])
        self.assertEqual(len(self._stored(conversation_id)), 1)

    # ----------------------------------------------------------- conflicts

    def test_same_key_with_different_bytes_is_a_conflict(self) -> None:
        conversation_id = self._open_conversation()
        original = self._upload(conversation_id)
        conflict = self._upload(conversation_id, payload=PNG + b"\x01")

        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(len(self._stored(conversation_id)), 1)
        self.assertEqual(
            self.services.attachments.get("demo", original.json()["id"])["size_bytes"], len(PNG)
        )

    def test_same_key_with_a_different_filename_is_a_conflict(self) -> None:
        conversation_id = self._open_conversation()
        self._upload(conversation_id, filename="a.png")
        conflict = self._upload(conversation_id, filename="b.png")
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(len(self._stored(conversation_id)), 1)

    def test_same_key_from_another_operator_is_a_conflict(self) -> None:
        conversation_id = self._open_conversation()
        self._upload(conversation_id)
        conflict = self._upload(conversation_id, headers=self.other)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(len(self._stored(conversation_id)), 1)

    def test_a_key_is_scoped_to_its_conversation(self) -> None:
        first_conversation = self._open_conversation()
        second_conversation = self._open_conversation()
        original = self._upload(first_conversation)

        other = self._upload(second_conversation)
        self.assertEqual(other.status_code, 201, other.text)
        self.assertNotEqual(other.json()["id"], original.json()["id"])
        self.assertNotIn("X-Idempotent-Replay", other.headers)
        self.assertEqual(len(self._stored(first_conversation)), 1)
        self.assertEqual(len(self._stored(second_conversation)), 1)

    def test_a_key_is_scoped_to_its_tenant(self) -> None:
        conversation_id = self._open_conversation()
        original = self._upload(conversation_id)
        self.services.database.ensure_tenant("other", "Other")
        with self.services.database.connect() as connection:
            connection.execute(
                """INSERT INTO conversations
                (id, tenant_id, customer_name, channel, status, priority, created_at, updated_at)
                VALUES ('conv_other_att', 'other', 'T', 'web', 'open', 'normal', ?, ?)""",
                ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
        # The same key string in another tenant is a different request: it must
        # never resolve to (or collide with) this tenant's row.
        foreign = self.services.attachments.upload(
            "other", "conv_other_att", "admin", "a.png", "image/png", PNG, idempotency_key=KEY
        )
        self.assertNotEqual(foreign["id"], original.json()["id"])
        self.assertEqual(len(self._stored(conversation_id)), 1)
        self.assertEqual(
            len(self.services.attachments.list_for_conversation("other", "conv_other_att")), 1
        )

    # ------------------------------------------------- backward compatibility

    def test_without_a_key_every_upload_is_its_own_attachment(self) -> None:
        conversation_id = self._open_conversation()
        self._upload(conversation_id, key=None)
        self._upload(conversation_id, key=None)
        self.assertEqual(len(self._stored(conversation_id)), 2)

    def test_a_new_key_stores_a_second_attachment(self) -> None:
        conversation_id = self._open_conversation()
        first = self._upload(conversation_id)
        second = self._upload(conversation_id, key="up-another-key")
        self.assertEqual(second.status_code, 201, second.text)
        self.assertNotIn("X-Idempotent-Replay", second.headers)
        self.assertNotEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(len(self._stored(conversation_id)), 2)

    def test_a_rejected_upload_leaves_no_receipt_for_the_retry(self) -> None:
        conversation_id = self._open_conversation()
        rejected = self._upload(
            conversation_id,
            filename="a.exe",
            payload=b"MZ\x00",
            content_type="application/octet-stream",
        )
        self.assertEqual(rejected.status_code, 415, rejected.text)
        self.assertEqual(len(self._stored(conversation_id)), 0)

        accepted = self._upload(conversation_id)
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertNotIn("X-Idempotent-Replay", accepted.headers)
        self.assertEqual(len(self._stored(conversation_id)), 1)

    def test_an_overlong_key_is_rejected(self) -> None:
        conversation_id = self._open_conversation()
        response = self._upload(conversation_id, key="k" * 129)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(len(self._stored(conversation_id)), 0)

    # ------------------------------------------------------------ projection

    def test_the_receipt_column_never_reaches_the_api(self) -> None:
        conversation_id = self._open_conversation()
        created = self._upload(conversation_id)
        self.assertNotIn("operator_idempotency_key", created.json())

        listed = self.client.get(
            "/api/attachments", params={"conversation_id": conversation_id}, headers=self.admin
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        for item in listed.json():
            self.assertNotIn("operator_idempotency_key", item)
            self.assertNotIn("storage_key", item)

        fetched = self.client.get(f"/api/attachments/{created.json()['id']}", headers=self.admin)
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertNotIn("operator_idempotency_key", fetched.json())


if __name__ == "__main__":
    unittest.main()
