"""ROADMAP H02: server-side send receipt for operator replies.

The operator workspace sends a reply, the network blips after the server
committed it, and the client retries the *same* send attempt. Today that
produces a second customer-visible message and a second ``operator.replied``
audit event — the button state is not a guarantee, the server is.

This pins the receipt contract:

- an ``Idempotency-Key`` binds one send attempt to the message it produced;
- replaying the key returns that same message with ``X-Idempotent-Replay`` and
  creates nothing (no second message, no second audit, no second handoff);
- the key is scoped per conversation, so the same string in another
  conversation is a *different* request and can never resolve to this
  conversation's message;
- a key replayed with a different payload, author or attachment set is a
  conflict (409), never a silent success — an edited draft must not be
  swallowed as a replay of the earlier text;
- no key at all keeps today's behaviour (every POST is its own message);
- the receipt survives a restart, so an offline operator can retry after the
  process bounced.
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

ADMIN_KEY = "idem-admin-key-001"
OTHER_KEY = "idem-other-key-001"

KEY = "ui-11111111-2222-3333-4444-555555555555"


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


class ReviewerMessageIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "idem.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.other = {"X-API-Key": OTHER_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------- helpers

    def _open_review_case(self) -> str:
        conv = self.client.post(
            "/api/review-cases", json={"customer_name": "S"}, headers=self.admin
        ).json()
        self.client.post(f"/api/review-cases/{conv['id']}/accept", headers=self.admin)
        return conv["id"]

    def _reply(
        self,
        review_case_id: str,
        content: str = "已为您加急处理",
        *,
        key: str | None = KEY,
        headers: dict[str, str] | None = None,
        attachment_ids: list[str] | None = None,
    ):
        merged = dict(headers or self.admin)
        if key is not None:
            merged["Idempotency-Key"] = key
        body: dict[str, Any] = {"content": content}
        if attachment_ids:
            body["attachment_ids"] = attachment_ids
        return self.client.post(
            f"/api/review-cases/{review_case_id}/operator-messages",
            json=body,
            headers=merged,
        )

    def _messages(self, review_case_id: str) -> list[dict[str, Any]]:
        return self.services.database.list_messages("demo", review_case_id)

    def _reviewer_messages(self, review_case_id: str) -> list[dict[str, Any]]:
        return [m for m in self._messages(review_case_id) if m["role"] == "operator"]

    def _audit_events(self, review_case_id: str, event_type: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self.services.database.list_audit("demo", review_case_id)
            if row["event_type"] == event_type
        ]

    def _upload(self, review_case_id: str, filename: str = "a.png"):
        response = self.client.post(
            "/api/attachments",
            files={"file": (filename, b"\x89PNG\r\n\x1a\n" + b"\x00" * 12, "image/png")},
            data={"conversation_id": review_case_id},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    # ------------------------------------------------------------ replays

    def test_replay_returns_the_original_message_and_creates_no_second(self) -> None:
        review_case_id = self._open_review_case()
        first = self._reply(review_case_id)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertNotIn("X-Idempotent-Replay", first.headers)

        retry = self._reply(review_case_id)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.headers.get("X-Idempotent-Replay"), "true")
        self.assertEqual(retry.json()["id"], first.json()["id"])

        self.assertEqual(len(self._reviewer_messages(review_case_id)), 1)

    def test_replay_neither_audits_nor_hands_off_a_second_time(self) -> None:
        review_case_id = self._open_review_case()
        self._reply(review_case_id)
        self._reply(review_case_id)

        self.assertEqual(len(self._audit_events(review_case_id, "operator.replied")), 1)
        # The handoff side effect (takeover on the first send) must not run
        # again for the replayed attempt.
        self.assertEqual(len(self._audit_events(review_case_id, "conversation.human_accepted")), 1)

    def test_replay_survives_a_process_restart(self) -> None:
        review_case_id = self._open_review_case()
        first = self._reply(review_case_id)
        self.services.database.close()

        from app.database import Database

        reopened = Database(self.db_path)
        try:
            stored = reopened.get_message_by_operator_key("demo", review_case_id, KEY)
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored["id"], first.json()["id"])
        finally:
            reopened.close()

    def test_retry_after_a_lost_response_returns_the_same_message(self) -> None:
        """The failure shape: the server committed, the client never saw it."""
        review_case_id = self._open_review_case()
        committed = self._reply(review_case_id)
        # The client only learns of the outcome on the retry.
        lost = self._reply(review_case_id)
        self.assertEqual(lost.json()["id"], committed.json()["id"])
        self.assertEqual(len(self._reviewer_messages(review_case_id)), 1)

    # ----------------------------------------------------------- conflicts

    def test_same_key_with_edited_content_is_a_conflict(self) -> None:
        review_case_id = self._open_review_case()
        original = self._reply(review_case_id, "第一版")
        conflict = self._reply(review_case_id, "改过的第二版")

        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(len(self._reviewer_messages(review_case_id)), 1)
        self.assertEqual(
            self.services.database.get_message("demo", review_case_id, original.json()["id"])[
                "content"
            ],
            "第一版",
        )

    def test_same_key_with_a_different_attachment_set_is_a_conflict(self) -> None:
        review_case_id = self._open_review_case()
        attachment = self._upload(review_case_id)
        self._reply(review_case_id, "带附件", attachment_ids=[attachment["id"]])
        conflict = self._reply(review_case_id, "带附件")
        self.assertEqual(conflict.status_code, 409, conflict.text)

    def test_same_key_from_another_reviewer_is_a_conflict(self) -> None:
        review_case_id = self._open_review_case()
        self._reply(review_case_id)
        conflict = self._reply(review_case_id, headers=self.other)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(len(self._reviewer_messages(review_case_id)), 1)

    def test_a_key_is_scoped_to_its_review_case(self) -> None:
        first_conversation = self._open_review_case()
        second_conversation = self._open_review_case()
        original = self._reply(first_conversation)

        # The same string in another conversation is a different request and
        # must never resolve to the first conversation's message.
        other = self._reply(second_conversation)
        self.assertEqual(other.status_code, 200, other.text)
        self.assertNotEqual(other.json()["id"], original.json()["id"])
        self.assertNotIn("X-Idempotent-Replay", other.headers)
        self.assertEqual(len(self._reviewer_messages(first_conversation)), 1)
        self.assertEqual(len(self._reviewer_messages(second_conversation)), 1)

    def test_a_key_is_scoped_to_its_tenant(self) -> None:
        review_case_id = self._open_review_case()
        first = self._reply(review_case_id)
        self.services.database.ensure_tenant("other", "Other")
        with self.services.database.connect() as connection:
            connection.execute(
                """INSERT INTO review_cases
                (id, tenant_id, submitter_name, channel, status, priority, created_at, updated_at)
                VALUES ('conv_other', 'other', 'T', 'web', 'open', 'normal', ?, ?)""",
                ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
        foreign = self.client.post(
            "/api/review-cases/conv_other/operator-messages",
            json={"content": first.json()["content"]},
            headers={
                "X-API-Key": ADMIN_KEY,
                "X-Tenant-Id": "other",
                "Idempotency-Key": KEY,
            },
        )
        self.assertNotEqual(foreign.status_code, 200)
        self.assertEqual(len(self._reviewer_messages(review_case_id)), 1)

    # ------------------------------------------------- backward compatibility

    def test_without_a_key_every_post_is_its_own_message(self) -> None:
        review_case_id = self._open_review_case()
        self._reply(review_case_id, "第一条", key=None)
        self._reply(review_case_id, "第二条", key=None)
        self.assertEqual(len(self._reviewer_messages(review_case_id)), 2)

    def test_blank_content_is_still_rejected(self) -> None:
        review_case_id = self._open_review_case()
        response = self.client.post(
            f"/api/review-cases/{review_case_id}/operator-messages",
            json={"content": "   "},
            headers={**self.admin, "Idempotency-Key": KEY},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(len(self._reviewer_messages(review_case_id)), 0)

    def test_an_overlong_key_is_rejected(self) -> None:
        review_case_id = self._open_review_case()
        response = self._reply(review_case_id, key="k" * 129)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(len(self._reviewer_messages(review_case_id)), 0)

    def test_a_new_key_creates_a_second_message(self) -> None:
        review_case_id = self._open_review_case()
        self._reply(review_case_id, "第一条")
        second = self._reply(review_case_id, "第一条", key="ui-another-key")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertNotIn("X-Idempotent-Replay", second.headers)
        self.assertEqual(len(self._reviewer_messages(review_case_id)), 2)


if __name__ == "__main__":
    unittest.main()
