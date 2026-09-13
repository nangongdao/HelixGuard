"""H04 invariant: a refusal must not be recorded as a failure (2.14.0).

2.12.0 stopped the auxiliary model surfaces from egressing under a denying
policy, but every deterministic fallback still recorded the *same* value. A
tenant refusal, a transport failure and the deployment's own deterministic
mode were all ``"rule"`` -- and that is the value the turn persists:
``language_source`` on the customer message, ``translation_source`` on the
assistant message, and ``source`` in the ``reply.translated`` audit payload.

So the audit trail could not answer the question a governance review actually
asks: did this reply go out untranslated because the *tenant's policy refused
the call*, or because the *provider was down*? H04 requires the four cases to
stay distinguishable -- global switch off, tenant refusal, upstream failure,
and the permitted deterministic path -- and requires a failure to be visible
rather than absorbed into the normal deterministic mode.

These tests pin the recorded outcome of every governed auxiliary surface, the
end-to-end record the turn writes, and the telemetry that keeps a failure
counted. The outcomes are persisted (message metadata, ``conversation_summaries
.source``, the copilot response contract), so the word list is closed and the
tokens are not free to be renamed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.control_plane import DataPlaneConfig, TenantControlPlane, TenantPolicy
from app.copilot import CopilotService
from app.database import Database, utc_now
from app.language import LanguageService
from app.model_gateway import (
    MODEL_CALL_OUTCOMES,
    OUTCOME_DENIED,
    OUTCOME_FAILED,
    OUTCOME_MODEL,
    OUTCOME_NONE,
    OUTCOME_REJECTED,
    OUTCOME_RULE,
    OUTCOME_UNCONFIGURED,
    ModelCallGate,
)
from app.model_provider import ModelResponse
from app.orchestrator import ConversationOrchestrator
from app.summaries import SummaryService

SECRET = "control-plane-signing-secret-0123456789abcdef"
TENANT = "acme"
DEFAULT_MODEL = "gpt-4.1-mini"


def _policy(**overrides: object) -> TenantPolicy:
    kwargs: dict = {"plan": "standard", "region": "local"}
    kwargs.update(overrides)
    return TenantPolicy(**kwargs)


class ScriptedProvider:
    """Deterministic provider stub; ``fail=True`` breaks every transport."""

    def __init__(
        self,
        *,
        detect: str = "ja",
        translation: str = "Translated reply.",
        fail: bool = False,
    ) -> None:
        self.detect = detect
        self.translation = translation
        self.fail = fail
        self.calls: list[str] = []

    def complete(
        self, system_prompt: str, user_prompt: str, model_ref: str | None = None
    ) -> ModelResponse:
        self.calls.append(system_prompt)
        if self.fail:
            raise RuntimeError("provider unavailable")
        if "language identification" in system_prompt:
            payload = {"language": self.detect}
        elif "translator" in system_prompt:
            payload = {"translation": self.translation}
        elif "suggestions" in system_prompt:
            payload = {"suggestions": ["We can help with that."]}
        elif "rewritten" in system_prompt:
            payload = {"rewritten": "Rewritten draft."}
        else:
            payload = {"summary": "Model summary of the conversation."}
        return ModelResponse(content=json.dumps(payload, ensure_ascii=False))


class OutcomeVocabularyTests(unittest.TestCase):
    """The four H04 cases must be four distinct tokens in one closed set."""

    def test_the_four_cases_are_pairwise_distinct(self) -> None:
        cases = {
            "global switch off": OUTCOME_UNCONFIGURED,
            "tenant refusal": OUTCOME_DENIED,
            "upstream failure": OUTCOME_FAILED,
            "permitted deterministic path": OUTCOME_RULE,
        }
        self.assertEqual(len(set(cases.values())), 4, cases)

    def test_every_outcome_belongs_to_the_closed_set(self) -> None:
        for outcome in (
            OUTCOME_MODEL,
            OUTCOME_RULE,
            OUTCOME_NONE,
            OUTCOME_UNCONFIGURED,
            OUTCOME_DENIED,
            OUTCOME_FAILED,
            OUTCOME_REJECTED,
        ):
            self.assertIn(outcome, MODEL_CALL_OUTCOMES)


class OutcomeFixture(unittest.TestCase):
    """Tenant + control plane fixture shared by the per-surface cases."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "outcomes.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.ensure_tenant(TENANT)
        self.cp = TenantControlPlane(self.db, SECRET)
        self.dp = DataPlaneConfig(self.db, SECRET)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _gate(self) -> ModelCallGate:
        return ModelCallGate(
            self.db, plane_resolver=lambda: self.dp, default_model_ref=DEFAULT_MODEL
        )

    def _deny_provider(self) -> None:
        """Refuse at the 43.5 disable surface (a governance refusal)."""
        self.cp.set_policy(TENANT, _policy(model_policy={"disabled_providers": ["openai"]}))
        self.dp.apply_snapshot(self.cp.issue_snapshot(TENANT))

    def _exhaust_budget(self) -> None:
        """Refuse at the budget facet -- a second, independent refusal path."""
        self.db.set_tenant_model_policy(TENANT, None, 1)
        self.db.increment_tenant_usage(TENANT, utc_now()[:10])


class LanguageDetectOutcomeTests(OutcomeFixture):
    def test_model_answer_is_recorded_as_model(self) -> None:
        provider = ScriptedProvider(detect="ja")
        service = LanguageService(provider, "zh", model_gate=self._gate())
        self.assertEqual(service.detect("Hello there", tenant_id=TENANT), ("ja", OUTCOME_MODEL))
        self.assertEqual(len(provider.calls), 1)

    def test_refusal_is_recorded_as_denied_and_never_transports(self) -> None:
        self._deny_provider()
        provider = ScriptedProvider(detect="ja")
        service = LanguageService(provider, "zh", model_gate=self._gate())
        self.assertEqual(service.detect("Hello there", tenant_id=TENANT), ("en", OUTCOME_DENIED))
        self.assertEqual(provider.calls, [])

    def test_budget_refusal_is_also_denied(self) -> None:
        self._exhaust_budget()
        provider = ScriptedProvider(detect="ja")
        service = LanguageService(provider, "zh", model_gate=self._gate())
        language, source = service.detect("Hello there", tenant_id=TENANT)
        self.assertEqual((language, source), ("en", OUTCOME_DENIED))
        self.assertEqual(provider.calls, [])

    def test_transport_failure_is_recorded_as_failed(self) -> None:
        provider = ScriptedProvider(fail=True)
        service = LanguageService(provider, "zh", model_gate=self._gate())
        self.assertEqual(service.detect("Hello there", tenant_id=TENANT), ("en", OUTCOME_FAILED))
        self.assertEqual(len(provider.calls), 1)

    def test_unusable_model_answer_is_recorded_as_failed(self) -> None:
        # The transport worked; the model just did not answer the question.
        # That is still not the deployment's own deterministic mode.
        provider = ScriptedProvider(detect="xx")
        service = LanguageService(provider, "zh", model_gate=self._gate())
        self.assertEqual(service.detect("Hello there", tenant_id=TENANT), ("en", OUTCOME_FAILED))

    def test_absent_provider_is_recorded_as_unconfigured(self) -> None:
        # ENABLE_LLM=false: the global switch is off, so the deterministic
        # detector *is* the configured mode -- never a refusal or a failure.
        service = LanguageService(None, "zh", model_gate=self._gate())
        self.assertEqual(
            service.detect("Hello there", tenant_id=TENANT), ("en", OUTCOME_UNCONFIGURED)
        )


class LanguageTranslateOutcomeTests(OutcomeFixture):
    def test_model_answer_is_recorded_as_model(self) -> None:
        provider = ScriptedProvider()
        service = LanguageService(provider, "zh", model_gate=self._gate())
        self.assertEqual(
            service.translate("退款已批准。", "en", tenant_id=TENANT),
            ("Translated reply.", True, OUTCOME_MODEL),
        )

    def test_refusal_is_recorded_as_denied_and_never_transports(self) -> None:
        self._deny_provider()
        provider = ScriptedProvider()
        service = LanguageService(provider, "zh", model_gate=self._gate())
        self.assertEqual(
            service.translate("退款已批准。", "en", tenant_id=TENANT),
            ("退款已批准。", False, OUTCOME_DENIED),
        )
        self.assertEqual(provider.calls, [])

    def test_transport_failure_is_recorded_as_failed(self) -> None:
        provider = ScriptedProvider(fail=True)
        service = LanguageService(provider, "zh", model_gate=self._gate())
        self.assertEqual(
            service.translate("退款已批准。", "en", tenant_id=TENANT),
            ("退款已批准。", False, OUTCOME_FAILED),
        )

    def test_absent_provider_is_recorded_as_unconfigured(self) -> None:
        service = LanguageService(None, "zh", model_gate=self._gate())
        self.assertEqual(
            service.translate("退款已批准。", "en", tenant_id=TENANT),
            ("退款已批准。", False, OUTCOME_UNCONFIGURED),
        )

    def test_same_language_needs_no_call_and_reports_none(self) -> None:
        provider = ScriptedProvider()
        service = LanguageService(provider, "zh", model_gate=self._gate())
        self.assertEqual(
            service.translate("退款已批准。", "zh", tenant_id=TENANT),
            ("退款已批准。", False, OUTCOME_NONE),
        )
        self.assertEqual(provider.calls, [])


class SummaryOutcomeTests(OutcomeFixture):
    def _conversation(self) -> str:
        conv = self.db.create_conversation(TENANT, "Summary", None, "web", "admin", 120)
        self.db.add_message(
            TENANT,
            conv["id"],
            "customer",
            "Summary",
            "我的订单到哪里了？",
            {"source_actor": "admin"},
            "turn-1",
        )
        return conv["id"]

    def test_model_answer_is_recorded_as_model(self) -> None:
        provider = ScriptedProvider()
        service = SummaryService(self.db, provider, model_gate=self._gate())
        row = service.generate(TENANT, self._conversation(), "context")
        self.assertEqual(row["source"], OUTCOME_MODEL)

    def test_refusal_is_recorded_as_denied(self) -> None:
        self._deny_provider()
        provider = ScriptedProvider()
        service = SummaryService(self.db, provider, model_gate=self._gate())
        row = service.generate(TENANT, self._conversation(), "context")
        self.assertEqual(row["source"], OUTCOME_DENIED)
        self.assertEqual(provider.calls, [])

    def test_transport_failure_is_recorded_as_failed(self) -> None:
        provider = ScriptedProvider(fail=True)
        service = SummaryService(self.db, provider, model_gate=self._gate())
        row = service.generate(TENANT, self._conversation(), "context")
        self.assertEqual(row["source"], OUTCOME_FAILED)

    def test_absent_provider_is_recorded_as_unconfigured(self) -> None:
        service = SummaryService(self.db, None, model_gate=self._gate())
        row = service.generate(TENANT, self._conversation(), "context")
        self.assertEqual(row["source"], OUTCOME_UNCONFIGURED)


class CopilotOutcomeTests(OutcomeFixture):
    def _conversation_with_canned_reply(self) -> str:
        conv = self.db.create_conversation(TENANT, "Copilot", None, "web", "admin", 120)
        self.db.add_message(
            TENANT,
            conv["id"],
            "customer",
            "Copilot",
            "我的订单到哪里了？",
            {"source_actor": "admin"},
            "turn-1",
        )
        self.db.create_canned_response(
            TENANT,
            title="Order status",
            body="正在为您查询订单状态。",
            shortcut="order",
            tags=["order"],
            actor_id="admin",
        )
        return conv["id"]

    def test_refusal_is_recorded_on_every_suggestion(self) -> None:
        conv_id = self._conversation_with_canned_reply()
        self._deny_provider()
        provider = ScriptedProvider()
        service = CopilotService(self.db, provider, model_gate=self._gate())
        suggestions = service.suggest_reply(TENANT, conv_id)
        self.assertTrue(suggestions)
        self.assertTrue(all(item["source"] == OUTCOME_DENIED for item in suggestions))
        self.assertEqual(provider.calls, [])

    def test_transport_failure_is_recorded_on_every_suggestion(self) -> None:
        conv_id = self._conversation_with_canned_reply()
        provider = ScriptedProvider(fail=True)
        service = CopilotService(self.db, provider, model_gate=self._gate())
        suggestions = service.suggest_reply(TENANT, conv_id)
        self.assertTrue(suggestions)
        self.assertTrue(all(item["source"] == OUTCOME_FAILED for item in suggestions))

    def test_refusal_is_recorded_on_the_rewrite(self) -> None:
        self._deny_provider()
        provider = ScriptedProvider()
        service = CopilotService(self.db, provider, model_gate=self._gate())
        result = service.rewrite_tone("请稍等。", "friendly", tenant_id=TENANT)
        self.assertEqual(result["source"], OUTCOME_DENIED)
        self.assertEqual(provider.calls, [])

    def test_transport_failure_is_recorded_on_the_rewrite(self) -> None:
        provider = ScriptedProvider(fail=True)
        service = CopilotService(self.db, provider, model_gate=self._gate())
        result = service.rewrite_tone("请稍等。", "friendly", tenant_id=TENANT)
        self.assertEqual(result["source"], OUTCOME_FAILED)


class TurnOutcomeRecordTests(OutcomeFixture):
    """The turn must persist the distinction, not just the fallback."""

    def _turn_with_language_provider(
        self, language_provider: ScriptedProvider | None
    ) -> ConversationOrchestrator:
        """Build a turn whose *only* model-backed surface is the language one.

        The orchestrator's own provider stays unset, so the transport counter
        on ``language_provider`` measures exactly the auxiliary surface under
        test. The triage path is a separate concern: H04's acceptance names the
        five auxiliary calls (detection, translation, summary, and the two
        copilot paths), and the main chain resolves its own model ref.
        """
        orchestrator = ConversationOrchestrator(
            self.db, Settings(database_path=self.db_path, auth_mode="demo"), None
        )
        orchestrator.data_plane_config = self.dp
        orchestrator.languages = LanguageService(
            language_provider, "zh", model_gate=orchestrator.model_gate
        )
        return orchestrator

    def _metadata(self, conversation_id: str, role: str) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM messages WHERE tenant_id = ? "
                "AND conversation_id = ? AND role = ? ORDER BY seq LIMIT 1",
                (TENANT, conversation_id, role),
            ).fetchone()
        return json.loads(row["metadata_json"]) if row else {}

    def test_refused_turn_records_the_refusal_end_to_end(self) -> None:
        self._deny_provider()
        provider = ScriptedProvider(detect="en", translation="How may I help you today?")
        orchestrator = self._turn_with_language_provider(provider)
        conv = self.db.create_conversation(TENANT, "Refused Turn", None, "web", "admin", 120)
        orchestrator.handle_customer_message(
            TENANT, conv["id"], "Hello there", "admin", "idem-outcome-1"
        )
        # A refusal is a decision, so nothing left the process...
        self.assertEqual(provider.calls, [])
        # ...and both records say *why*, instead of "the deterministic path".
        self.assertEqual(self._metadata(conv["id"], "customer")["language_source"], OUTCOME_DENIED)
        assistant = self._metadata(conv["id"], "assistant")
        self.assertEqual(assistant["translation_source"], OUTCOME_DENIED)
        self.assertFalse(assistant["translated"])
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM audit_events WHERE event_type = 'reply.translated'"
            ).fetchone()
        self.assertEqual(json.loads(row["payload_json"])["source"], OUTCOME_DENIED)

    def test_failed_turn_records_the_failure_end_to_end(self) -> None:
        provider = ScriptedProvider(fail=True)
        orchestrator = self._turn_with_language_provider(provider)
        conv = self.db.create_conversation(TENANT, "Failed Turn", None, "web", "admin", 120)
        orchestrator.handle_customer_message(
            TENANT, conv["id"], "Hello there", "admin", "idem-outcome-2"
        )
        self.assertTrue(provider.calls)
        self.assertEqual(self._metadata(conv["id"], "customer")["language_source"], OUTCOME_FAILED)
        self.assertEqual(
            self._metadata(conv["id"], "assistant")["translation_source"], OUTCOME_FAILED
        )


class OutcomeTelemetryTests(OutcomeFixture):
    """A failure must stay visible in telemetry, and never as a refusal.

    The metric registry is process-global, so every case compares deltas.
    """

    @staticmethod
    def _total(name: str) -> float:
        from app import telemetry

        counters = telemetry.metrics.snapshot()["counters"]
        return sum(value for key, value in counters.items() if name in key)

    def test_transport_failure_is_counted_and_refusal_is_not(self) -> None:
        service = LanguageService(ScriptedProvider(fail=True), "zh", model_gate=self._gate())
        denied_before = self._total("model.call_denied")
        failed_before = self._total("model.call_failed")
        service.detect("Hello there", tenant_id=TENANT)
        self.assertEqual(self._total("model.call_failed"), failed_before + 1)
        self.assertEqual(self._total("model.call_denied"), denied_before)

        self._deny_provider()
        denied_before = self._total("model.call_denied")
        failed_before = self._total("model.call_failed")
        LanguageService(ScriptedProvider(), "zh", model_gate=self._gate()).detect(
            "Hello there", tenant_id=TENANT
        )
        self.assertEqual(self._total("model.call_denied"), denied_before + 1)
        self.assertEqual(self._total("model.call_failed"), failed_before)
