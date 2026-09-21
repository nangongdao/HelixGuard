"""ROADMAP H03 (2.17.0): order clarification and bounded multi-turn tasks.

The old policy escalated every order query that arrived without an order
number (``order_response_without_tool_record`` -> quality gate -> human). The
roadmap keeps the conservative evidence rule for *final answers* but adds an
explicit ``clarification`` result kind so "查来源 -> 请提供来源记录号 -> ORD-..."
can complete inside one conversation:

- a clarification is a designed outcome, not a quality failure and not a
  handoff -- the conversation stays open and a durable pending task records
  the slot, the original intent and the round/expiry bounds;
- a follow-up order number still goes through the same identity gate and the
  same ``orders.lookup`` tool evidence as a first-turn answer;
- topic switches, sensitive requests, human takeover, resolution and the
  round limit all cancel the pending task;
- pending state is durable (survives restart) and replay-safe (an idempotent
  replay must not bump the round counter twice).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.agents import OrderAgent
from app.config import Settings
from app.main import create_app

ADMIN_KEY = "clarify-admin-key-001"


def _settings(db_path: Path) -> Settings:
    principals = {ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"}}
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        turn_worker_enabled=False,
    )


class OrderAgentOutcomeTests(unittest.TestCase):
    """The order agent speaks the closed task-outcome vocabulary (H03)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        from app.database import Database
        from app.tools import ToolGateway

        database = Database(Path(self._tmp.name) / "outcomes.db")
        database.initialize()
        self.agent = OrderAgent(ToolGateway(database))

    def tearDown(self) -> None:
        self.agent.tools.database.close()
        self._tmp.cleanup()

    def test_no_order_number_is_a_clarification(self) -> None:
        from app.domain import TaskOutcome

        result = self.agent.respond("demo", "CUST-1001", "帮我查一下来源")
        self.assertEqual(result.task_outcome, TaskOutcome.CLARIFICATION)
        self.assertEqual(result.clarify_slot, "order_id")
        self.assertFalse(result.requires_human)
        self.assertIn("来源记录号", result.content)

    def test_successful_lookup_is_an_answer(self) -> None:
        from app.domain import TaskOutcome

        result = self.agent.respond("demo", "CUST-1001", "ORD-10482 的来源")
        self.assertEqual(result.task_outcome, TaskOutcome.ANSWER)
        self.assertTrue(any(call.get("tool") == "orders.lookup" for call in result.tool_calls))

    def test_unknown_order_is_still_an_answer(self) -> None:
        """A definitive not-found with tool evidence answers the question."""
        from app.domain import TaskOutcome

        result = self.agent.respond("demo", "CUST-1001", "ORD-99999 的来源")
        self.assertEqual(result.task_outcome, TaskOutcome.ANSWER)

    def test_identity_required_is_a_handoff(self) -> None:
        from app.domain import TaskOutcome

        result = self.agent.respond("demo", None, "ORD-10482 的来源")
        self.assertEqual(result.task_outcome, TaskOutcome.HANDOFF)
        self.assertTrue(result.requires_human)


class QualityGateClarificationTests(unittest.TestCase):
    """The quality gate exempts clarifications, not answers."""

    def _result(self, **kwargs: object) -> object:
        from app.agents import AgentName, AgentResult

        return AgentResult(
            agent=AgentName.ORDER, content="请提供来源记录号", confidence=0.9, **kwargs
        )

    def test_clarification_without_tool_record_is_approved(self) -> None:
        from app.agents import QualityAgent
        from app.domain import TaskOutcome

        result = self._result(task_outcome=TaskOutcome.CLARIFICATION, clarify_slot="order_id")
        assessment = QualityAgent().review(result, threshold=0.55)  # type: ignore[arg-type]
        self.assertTrue(assessment.approved, assessment.issues)

    def test_unmarked_order_result_without_tool_record_still_flagged(self) -> None:
        """Regression: legacy results (no outcome marker) keep the old rule."""
        from app.agents import QualityAgent

        assessment = QualityAgent().review(self._result(), threshold=0.55)  # type: ignore[arg-type]
        self.assertFalse(assessment.approved)
        self.assertIn("order_response_without_tool_record", assessment.issues)

    def test_answer_marked_result_without_tool_record_still_flagged(self) -> None:
        """The evidence rule for final answers is not relaxed by H03."""
        from app.agents import QualityAgent
        from app.domain import TaskOutcome

        result = self._result(task_outcome=TaskOutcome.ANSWER)
        assessment = QualityAgent().review(result, threshold=0.55)  # type: ignore[arg-type]
        self.assertIn("order_response_without_tool_record", assessment.issues)


class PendingTaskStoreTests(unittest.TestCase):
    """The pending-task store is durable, single-slot per conversation and expiry-aware."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        from app.database import Database

        self.database = Database(Path(self._tmp.name) / "pending.db")
        self.database.initialize()

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_bump_inserts_then_increments_rounds(self) -> None:
        first = self.database.bump_pending_task(
            "demo", "conv-1", kind="order_clarification", slot="order_id", intent="order_status"
        )
        self.assertEqual(first["rounds"], 1)
        self.assertEqual(first["intent"], "order_status")
        second = self.database.bump_pending_task(
            "demo", "conv-1", kind="order_clarification", slot="order_id", intent="order_status"
        )
        self.assertEqual(second["rounds"], 2)

    def test_one_slot_per_conversation_and_tenant_scoped(self) -> None:
        # The table's tenant FK follows the baseline schema convention, so the
        # second tenant is a real row (as in the other tenant-scoped suites).
        self.database.ensure_tenant("other")
        self.database.bump_pending_task(
            "demo", "conv-1", kind="order_clarification", slot="order_id", intent="order_status"
        )
        self.database.bump_pending_task(
            "demo", "conv-2", kind="order_clarification", slot="order_id", intent="order_status"
        )
        self.database.bump_pending_task(
            "other", "conv-1", kind="order_clarification", slot="order_id", intent="order_status"
        )
        self.assertEqual(self.database.get_pending_task("demo", "conv-1")["rounds"], 1)
        self.assertEqual(self.database.get_pending_task("demo", "conv-2")["rounds"], 1)
        self.assertEqual(self.database.get_pending_task("other", "conv-1")["rounds"], 1)

    def test_clear_removes_and_reports(self) -> None:
        self.database.bump_pending_task(
            "demo", "conv-1", kind="order_clarification", slot="order_id", intent="order_status"
        )
        self.assertTrue(self.database.clear_pending_task("demo", "conv-1"))
        self.assertFalse(self.database.clear_pending_task("demo", "conv-1"))
        self.assertIsNone(self.database.get_pending_task("demo", "conv-1"))

    def test_expired_task_is_lazily_dropped(self) -> None:
        self.database.bump_pending_task(
            "demo", "conv-1", kind="order_clarification", slot="order_id", intent="order_status"
        )
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE conversation_pending_tasks SET expires_at = ?",
                ("2000-01-01T00:00:00+00:00",),
            )
        self.assertIsNone(self.database.get_pending_task("demo", "conv-1"))
        # The lazy drop also removed the row, so a fresh bump starts at 1.
        fresh = self.database.bump_pending_task(
            "demo", "conv-1", kind="order_clarification", slot="order_id", intent="order_status"
        )
        self.assertEqual(fresh["rounds"], 1)


class ClarificationTurnTests(unittest.TestCase):
    """End-to-end: clarify -> supply the number -> answer, plus every cancel path."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "clarify.db"
        self.settings = _settings(self.db_path)
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.headers = {"X-API-Key": ADMIN_KEY}

    def tearDown(self) -> None:
        self._database().close()
        self.client.close()
        self._tmp.cleanup()

    def _database(self) -> object:
        return self.app.state.services.database

    def _create_conversation(self, customer_ref: str | None = "CUST-1001") -> str:
        payload: dict[str, str] = {"customer_name": "clarify-customer", "channel": "web"}
        if customer_ref:
            payload["customer_ref"] = customer_ref
        created = self.client.post("/api/review-cases", json=payload, headers=self.headers)
        self.assertEqual(created.status_code, 201, created.text)
        return str(created.json()["id"])

    def _send(self, conversation_id: str, content: str) -> dict:
        response = self.client.post(
            f"/api/review-cases/{conversation_id}/messages",
            headers=dict(self.headers, **{"Idempotency-Key": uuid4().hex}),
            json={"content": content},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return dict(response.json())

    def _detail(self, conversation_id: str) -> dict:
        response = self.client.get(f"/api/review-cases/{conversation_id}", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return dict(response.json())

    def test_clarify_turn_stays_open_and_records_pending_task(self) -> None:
        from app.domain import TaskOutcome

        conversation_id = self._create_conversation()
        turn = self._send(conversation_id, "帮我查一下来源")
        assistant = turn["assistant_message"]
        self.assertIsNotNone(assistant)
        self.assertEqual(turn["conversation"]["status"], "open")
        self.assertEqual(assistant["metadata"]["task_outcome"], TaskOutcome.CLARIFICATION)
        self.assertEqual(assistant["metadata"]["clarify_slot"], "order_id")
        self.assertEqual(assistant["metadata"]["pending_rounds"], 1)
        self.assertIn("pending_expires_at", assistant["metadata"])
        # Durable row: intent recorded, one round consumed, expiry set.
        pending = self._database().get_pending_task("demo", conversation_id)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["kind"], "order_clarification")
        self.assertEqual(pending["slot"], "order_id")
        self.assertEqual(pending["intent"], "order_status")
        self.assertEqual(pending["rounds"], 1)
        # The operator-visible detail carries the pending task.
        detail = self._detail(conversation_id)
        self.assertEqual(detail["pending_task"]["slot"], "order_id")
        self.assertEqual(detail["pending_task"]["rounds"], 1)
        # Audited so governance can trace the clarify.
        events = self._database().list_audit("demo", conversation_id)
        self.assertTrue(any(e["event_type"] == "order.clarification_requested" for e in events))

    def test_follow_up_order_number_completes_with_tool_evidence(self) -> None:
        from app.domain import TaskOutcome

        conversation_id = self._create_conversation()
        self._send(conversation_id, "帮我查一下来源")
        turn = self._send(conversation_id, "记录号是 ORD-10482")
        assistant = turn["assistant_message"]
        self.assertEqual(assistant["metadata"]["task_outcome"], TaskOutcome.ANSWER)
        self.assertIn("已核验", assistant["content"])
        lookup = next(
            call
            for call in assistant["metadata"]["tool_calls"]
            if call.get("tool") == "orders.lookup"
        )
        self.assertEqual(lookup["code"], "ok")
        self.assertEqual(turn["conversation"]["status"], "open")
        self.assertIsNone(self._database().get_pending_task("demo", conversation_id))
        detail = self._detail(conversation_id)
        self.assertIsNone(detail["pending_task"])
        events = self._database().list_audit("demo", conversation_id)
        cleared = [e for e in events if e["event_type"] == "order.pending_cleared"]
        self.assertTrue(cleared and cleared[-1]["payload"]["reason"] == "answered")

    def test_unverified_customer_supplying_number_still_hits_identity_gate(self) -> None:
        conversation_id = self._create_conversation(customer_ref=None)
        clarify = self._send(conversation_id, "帮我查一下来源")
        self.assertEqual(clarify["conversation"]["status"], "open")
        turn = self._send(conversation_id, "ORD-10482 的来源")
        self.assertEqual(turn["conversation"]["status"], "waiting_human")
        assistant = turn["assistant_message"]
        self.assertNotIn("已核验", assistant["content"])
        lookup = next(
            call
            for call in assistant["metadata"]["tool_calls"]
            if call.get("tool") == "orders.lookup"
        )
        self.assertEqual(lookup["code"], "identity_required")
        self.assertIsNone(self._database().get_pending_task("demo", conversation_id))

    def test_round_limit_escalates_clearly_and_cancels_task(self) -> None:
        conversation_id = self._create_conversation()
        first = self._send(conversation_id, "帮我查一下来源")
        self.assertEqual(first["conversation"]["status"], "open")
        second = self._send(conversation_id, "帮我查一下来源")
        self.assertEqual(second["conversation"]["status"], "open")
        third = self._send(conversation_id, "帮我查一下来源")
        self.assertEqual(third["conversation"]["status"], "waiting_human")
        assistant = third["assistant_message"]
        self.assertEqual(assistant["metadata"]["agent"], "escalation")
        self.assertIn("round limit", assistant["metadata"]["handoff_reason"])
        self.assertIsNone(self._database().get_pending_task("demo", conversation_id))

    def test_topic_switch_cancels_pending_task(self) -> None:
        conversation_id = self._create_conversation()
        self._send(conversation_id, "帮我查一下来源")
        turn = self._send(conversation_id, "申诉时限是多久")
        self.assertEqual(turn["conversation"]["status"], "open")
        self.assertEqual(turn["assistant_message"]["metadata"]["agent"], "knowledge")
        self.assertIsNone(self._database().get_pending_task("demo", conversation_id))
        events = self._database().list_audit("demo", conversation_id)
        cleared = [e for e in events if e["event_type"] == "order.pending_cleared"]
        self.assertTrue(cleared and cleared[-1]["payload"]["reason"] == "topic_switch")

    def test_sensitive_request_cancels_pending_task(self) -> None:
        conversation_id = self._create_conversation()
        self._send(conversation_id, "帮我查一下来源")
        turn = self._send(conversation_id, "我要投诉")
        self.assertEqual(turn["conversation"]["status"], "waiting_human")
        self.assertIsNone(self._database().get_pending_task("demo", conversation_id))
        events = self._database().list_audit("demo", conversation_id)
        cleared = [e for e in events if e["event_type"] == "order.pending_cleared"]
        self.assertTrue(cleared and cleared[-1]["payload"]["reason"] == "handoff")

    def test_credential_material_never_enters_the_clarification_loop(self) -> None:
        """A credential-bearing order message keeps the adversarial floor.

        Asking for the order number would invite the customer to paste more
        secrets, so the turn escalates to a human -- the outcome this input had
        before H03 -- instead of opening a clarification (and a pending task).
        """
        conversation_id = self._create_conversation()
        turn = self._send(conversation_id, "我收到的验证码是 873296，帮我处理来源记录")
        self.assertEqual(turn["conversation"]["status"], "waiting_human")
        assistant = turn["assistant_message"]
        self.assertEqual(assistant["metadata"]["agent"], "escalation")
        self.assertNotIn("873296", assistant["content"])
        self.assertNotIn("请提供来源记录号", assistant["content"])
        self.assertIsNone(self._database().get_pending_task("demo", conversation_id))

    def test_expired_pending_starts_a_fresh_task(self) -> None:
        conversation_id = self._create_conversation()
        self._send(conversation_id, "帮我查一下来源")
        with self._database().connect() as connection:
            connection.execute(
                "UPDATE conversation_pending_tasks SET expires_at = ?",
                ("2000-01-01T00:00:00+00:00",),
            )
        turn = self._send(conversation_id, "帮我查一下来源")
        self.assertEqual(turn["conversation"]["status"], "open")
        self.assertEqual(turn["assistant_message"]["metadata"]["pending_rounds"], 1)

    def test_idempotent_replay_does_not_double_bump_rounds(self) -> None:
        conversation_id = self._create_conversation()
        key = uuid4().hex
        first = self.client.post(
            f"/api/review-cases/{conversation_id}/messages",
            headers=dict(self.headers, **{"Idempotency-Key": key}),
            json={"content": "帮我查一下来源"},
        )
        self.assertEqual(first.status_code, 200)
        replay = self.client.post(
            f"/api/review-cases/{conversation_id}/messages",
            headers=dict(self.headers, **{"Idempotency-Key": key}),
            json={"content": "帮我查一下来源"},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["idempotent_replay"])
        pending = self._database().get_pending_task("demo", conversation_id)
        self.assertEqual(pending["rounds"], 1)

    def test_pending_task_survives_process_restart(self) -> None:
        conversation_id = self._create_conversation()
        self._send(conversation_id, "帮我查一下来源")
        # A fresh Database instance over the same file (the restart shape).
        from app.database import Database

        reopened = Database(self.db_path)
        try:
            pending = reopened.get_pending_task("demo", conversation_id)
            self.assertIsNotNone(pending)
            self.assertEqual(pending["rounds"], 1)
        finally:
            # Close what we opened: a leaked handle keeps the SQLite file
            # locked and breaks the temp-dir cleanup on Windows.
            reopened.close()

    def test_human_takeover_cancels_pending_task(self) -> None:
        conversation_id = self._create_conversation()
        self._send(conversation_id, "帮我查一下来源")
        accepted = self.client.post(
            f"/api/review-cases/{conversation_id}/accept", headers=self.headers
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertIsNone(self._database().get_pending_task("demo", conversation_id))

    def test_resolve_cancels_pending_task(self) -> None:
        conversation_id = self._create_conversation()
        self._send(conversation_id, "帮我查一下来源")
        resolved = self.client.post(
            f"/api/review-cases/{conversation_id}/resolve", headers=self.headers
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertIsNone(self._database().get_pending_task("demo", conversation_id))


if __name__ == "__main__":
    unittest.main()
