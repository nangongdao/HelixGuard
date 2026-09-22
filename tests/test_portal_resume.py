"""H01 (widget side): resumable message reads and the customer projection.

The widget has to keep the customer current while they sit idle — an operator
reply, a verdict and the CSAT link all arrive *without* the customer sending
anything.  The operator console already exposes opaque keyset cursors for that
job (``app/pagination.py`` + ``X-Next-Cursor``); the widget surface did not, so
the client refetched the whole transcript (``limit=200``) and had nowhere to
resume from after a dropped connection.

Two contracts are covered here:

1. ``GET /api/submission-portal/sessions/{id}/messages`` exposes the *same* cursor contract
   as the operator console, and the cursor advances over every row it scanned —
   including rows the customer may not see.  A page made entirely of internal
   notes must not stall the customer's cursor, or the poll loop can never reach
   the messages behind it.
2. The customer projection hides internal roles.  The widget used a local
   two-item list while ``copilot``/``summaries`` used a three-item one, so
   ``note`` was internal everywhere except in the customer's browser.
"""

from __future__ import annotations

import gc
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.portal_token import sign_token


class _PortalFixture(unittest.TestCase):
    """Shared app/database fixture for the widget read contract."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "widget-resume.db"
        self.portal_secret = "test-widget-secret-key"
        self.settings = Settings(
            database_path=self.db_path,
            auth_mode="demo",
            api_keys_json=json.dumps({}),
            docs_enabled=False,
            widget_secret=self.portal_secret,
            widget_frame_ancestors=("'self'",),
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.tenant_id = "tenant-widget-resume"

        from app.database import Database

        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.ensure_tenant(self.tenant_id, "Widget Resume Tenant")

    def tearDown(self) -> None:
        self.client.close()
        self.db.close()
        gc.collect()
        try:
            self.tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def _session(self, submitter_name: str = "Visitor") -> tuple[str, str]:
        """Open a widget session; return ``(review_case_id, session_token)``."""
        response = self.client.post(
            "/api/submission-portal/sessions",
            json={"customer_name": submitter_name, "channel": "web_chat"},
            headers={
                "X-Widget-Token": sign_token(
                    secret=self.portal_secret,
                    tenant_id=self.tenant_id,
                    ttl_seconds=3600,
                )
            },
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        return payload["conversation"]["id"], payload["widget_token"]

    def _add(self, review_case_id: str, role: str, content: str) -> str:
        message = self.db.add_message(self.tenant_id, review_case_id, role, "fixture", content)
        return str(message["id"])

    def _list(self, review_case_id: str, token: str, query: str = ""):
        """Return ``(status, headers, body)``; headers stay case-insensitive."""
        response = self.client.get(
            f"/api/submission-portal/sessions/{review_case_id}/messages{query}",
            headers={"X-Widget-Token": token},
        )
        body = response.json() if response.status_code == 200 else []
        return response.status_code, response.headers, body


class PortalMessageCursorTests(_PortalFixture):
    """The widget read must be resumable, or idle customers never see a reply."""

    def test_page_exposes_cursor_so_a_client_can_resume(self) -> None:
        review_case_id, token = self._session("Cursor")
        self._add(review_case_id, "customer", "first")

        status, headers, body = self._list(review_case_id, token)
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 1)
        self.assertIn("X-Next-Cursor", headers)
        self.assertEqual(headers.get("X-Has-More"), "false")

    def test_cursor_returns_only_the_messages_that_arrived_since(self) -> None:
        review_case_id, token = self._session("Delta")
        self._add(review_case_id, "customer", "first")
        _, headers, first_page = self._list(review_case_id, token)
        cursor = headers["X-Next-Cursor"]

        # The operator answers while the customer sits idle.
        reply_id = self._add(review_case_id, "operator", "人工答复")
        status, delta_headers, delta = self._list(review_case_id, token, f"?cursor={quote(cursor)}")

        self.assertEqual(status, 200)
        self.assertEqual([message["id"] for message in delta], [reply_id])
        # A poll with no new rows must still tell the client the status.
        self.assertIn("X-Conversation-Status", delta_headers)
        self.assertNotIn(reply_id, [message["id"] for message in first_page])

    def test_invalid_cursor_is_rejected_with_400(self) -> None:
        review_case_id, token = self._session("Bad")
        status, _headers, _body = self._list(review_case_id, token, "?cursor=not-a-real-cursor")
        self.assertEqual(status, 400)

    def test_a_page_of_internal_notes_does_not_stall_the_cursor(self) -> None:
        """Scanning must advance the cursor even when nothing is visible.

        Two notes fill the page (``limit=2``) and the customer sees an empty
        body — but the cursor has to move past them, otherwise the poll loop
        re-reads the same notes forever and never reaches the customer's own
        message.
        """
        review_case_id, token = self._session("Stall")
        self._add(review_case_id, "internal_note", "内部备注一")
        self._add(review_case_id, "internal_note", "内部备注二")
        visible_id = self._add(review_case_id, "operator", "提交方可见答复")

        status, headers, body = self._list(review_case_id, token, "?limit=2")
        self.assertEqual(status, 200)
        self.assertEqual(body, [])
        self.assertEqual(headers.get("X-Has-More"), "true")
        self.assertIn("X-Next-Cursor", headers)

        status, _headers, second = self._list(
            review_case_id, token, f"?limit=2&cursor={quote(headers['X-Next-Cursor'])}"
        )
        self.assertEqual(status, 200)
        self.assertEqual([message["id"] for message in second], [visible_id])

    def test_cursor_cannot_reach_another_review_case(self) -> None:
        first_id, first_token = self._session("One")
        second_id, second_token = self._session("Two")
        self._add(first_id, "customer", "conversation one")
        self._add(second_id, "customer", "conversation two")
        _, headers, _ = self._list(first_id, first_token)

        status, _headers, body = self._list(
            second_id, second_token, f"?cursor={quote(headers['X-Next-Cursor'])}"
        )
        self.assertEqual(status, 200)
        self.assertEqual([message["content"] for message in body], ["conversation two"])

    def test_idle_poll_carries_reply_and_verdict_without_a_submitter_send(self) -> None:
        """H01 headline: the customer sends nothing and still learns everything."""
        review_case_id, token = self._session("Idle")
        self._add(review_case_id, "customer", "我的订单到哪了")
        _status, headers, _body = self._list(review_case_id, token)
        cursor = headers["X-Next-Cursor"]

        # From here on the customer sends nothing at all.
        reply_id = self._add(review_case_id, "operator", "已为你加急处理")
        self.db.transition_review_case(self.tenant_id, review_case_id, ["open"], "resolved")
        survey_token = self.db.create_csat_survey(
            self.tenant_id, review_case_id, "2027-01-01T00:00:00+00:00"
        )

        status, poll_headers, delta = self._list(
            review_case_id, token, f"?limit=50&cursor={quote(cursor)}"
        )
        self.assertEqual(status, 200)
        self.assertEqual([message["id"] for message in delta], [reply_id])
        self.assertEqual(poll_headers.get("X-Conversation-Status"), "resolved")
        self.assertIn(survey_token, poll_headers.get("X-CSAT-Survey-URL") or "")

    def test_an_empty_delta_still_reports_the_review_case_status(self) -> None:
        """No new rows is not "no news": a resolve without a reply must show."""
        review_case_id, token = self._session("Quiet")
        self._add(review_case_id, "customer", "还有人在吗")
        _status, headers, _body = self._list(review_case_id, token)

        status, poll_headers, delta = self._list(
            review_case_id, token, f"?limit=50&cursor={quote(headers['X-Next-Cursor'])}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(delta, [])
        self.assertEqual(poll_headers.get("X-Conversation-Status"), "open")

    def test_limit_still_caps_the_visible_page(self) -> None:
        review_case_id, token = self._session("Limit")
        for index in range(5):
            self._add(review_case_id, "operator", f"reply {index}")

        status, headers, body = self._list(review_case_id, token, "?limit=3")
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 3)
        self.assertEqual(headers.get("X-Has-More"), "true")


class PortalInternalRoleProjectionTests(_PortalFixture):
    """Internal roles must not reach the customer's browser."""

    def test_every_internal_role_is_hidden_from_the_submitter(self) -> None:
        from app.domain import INTERNAL_MESSAGE_ROLES, is_internal_message_role

        self.assertTrue(INTERNAL_MESSAGE_ROLES)
        for role in sorted(INTERNAL_MESSAGE_ROLES):
            self.assertTrue(is_internal_message_role(role), role)

        review_case_id, token = self._session("Projection")
        for role in sorted(INTERNAL_MESSAGE_ROLES):
            self._add(review_case_id, role, f"{role} 内部内容")
        visible_id = self._add(review_case_id, "operator", "公开答复")

        status, _headers, body = self._list(review_case_id, token, "?limit=50")
        self.assertEqual(status, 200)
        self.assertEqual([message["id"] for message in body], [visible_id])

    def test_unknown_roles_stay_visible_to_the_submitter(self) -> None:
        # Fail-open on the audience, fail-closed on the roles we know are
        # internal: an unrecognised role is still customer-facing chatter.
        review_case_id, token = self._session("Unknown")
        unknown_id = self._add(review_case_id, "assistant_draft", "hello")

        status, _headers, body = self._list(review_case_id, token, "?limit=50")
        self.assertEqual(status, 200)
        self.assertEqual([message["id"] for message in body], [unknown_id])


if __name__ == "__main__":
    unittest.main()
