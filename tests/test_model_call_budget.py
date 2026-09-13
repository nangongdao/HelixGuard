"""H04 gap (2.16.0): every governed transport consumes the daily budget.

The Phase 19.4 daily budget is denominated in *turns* and must stay that way
-- ``tenant_usage_daily.turn_count`` feeds the billing export and the admin
display, so auxiliary model work must not pollute it. The audit record from
2.12.0 on ("辅助调用不消耗/不预留每日预算") is about the other half: a tenant
capping model spend with ``daily_turn_budget=50`` still gets detect + translate
+ summary + copilot transports *for free* on every one of those turns.

This slice gives the gate its own counter: ``daily_model_call_budget`` (per
tenant, None = unlimited) counts every governed call that actually transports
-- triage included -- and the unit is *reserved before* the transport and
*released* when the transport raises. A deployment that never configures the
limit sees zero behaviour change: no counter writes, no new refusals.

The reservation boundary is the transport attempt itself: a call whose
``complete`` returned is consumed even when its output is unusable (the
request reached the provider); a call whose ``complete`` raised is released
(the operator is not billed for an outage).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.agents import TriageAgent
from app.config import Settings
from app.control_plane import DataPlaneConfig, TenantPolicy
from app.copilot import CopilotService
from app.database import Database, utc_now
from app.language import LanguageService
from app.model_gateway import ModelCallDenied, ModelCallGate, reserved_transport
from app.model_provider import ModelProviderError, ModelResponse
from app.orchestrator import ConversationOrchestrator
from app.summaries import SummaryService

SECRET = "control-plane-signing-secret-0123456789abcdef"
TENANT = "acme"
DEFAULT_MODEL = "gpt-4.1-mini"

# Low rule confidence, so the triage model path is actually taken.
NEUTRAL_MESSAGE = "Hello there"
# High rule confidence (0.98), so the triage model path is skipped entirely.
RULE_MESSAGE = "我要投诉"


def _policy(**overrides: object) -> TenantPolicy:
    kwargs: dict = {"plan": "standard", "region": "local"}
    kwargs.update(overrides)
    return TenantPolicy(**kwargs)


class FakeModelProvider:
    """Deterministic provider stub that counts every transport attempt."""

    def __init__(
        self,
        *,
        detect: str = "ja",
        translation: str = "Translated reply.",
        rewritten: str = "Rewritten draft.",
        fail: bool = False,
        garbage: bool = False,
    ) -> None:
        self.detect = detect
        self.translation = translation
        self.rewritten = rewritten
        self.fail = fail
        self.garbage = garbage
        self.calls: list[tuple[str, str]] = []

    def complete(
        self, system_prompt: str, user_prompt: str, model_ref: str | None = None
    ) -> ModelResponse:
        self.calls.append((system_prompt, user_prompt))
        if self.fail:
            raise ModelProviderError("provider unavailable")
        if self.garbage:
            return ModelResponse(content="not json at all")
        if "language identification" in system_prompt:
            payload: dict = {"language": self.detect}
        elif "translator" in system_prompt:
            payload = {"translation": self.translation}
        elif "suggestions" in system_prompt:
            payload = {"suggestions": ["We can help with that."]}
        elif "rewritten" in system_prompt:
            payload = {"rewritten": self.rewritten}
        elif "Classify" in system_prompt:
            payload = {
                "route": "knowledge",
                "intent": "general_question",
                "confidence": 0.66,
                "urgency": "normal",
                "reasons": ["Model semantic routing"],
            }
        else:
            payload = {"summary": "Model summary of the conversation."}
        return ModelResponse(content=json.dumps(payload, ensure_ascii=False))


class BudgetFixture(unittest.TestCase):
    """Shared database + gate fixture with the model-call budget helper."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "budget.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.ensure_tenant(TENANT)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _gate(self) -> ModelCallGate:
        return ModelCallGate(self.db, default_model_ref=DEFAULT_MODEL)

    def _set_budget(self, limit: int | None) -> None:
        self.db.set_tenant_model_policy(TENANT, None, None, limit)

    def _model_calls(self) -> int:
        return self.db.get_tenant_model_call_usage(TENANT, utc_now()[:10])


class ModelCallBudgetGateTests(BudgetFixture):
    """The gate facet: check, refuse, and refuse with a nameable reason."""

    def test_no_limit_writes_no_counter(self) -> None:
        # Default policy: no ``daily_model_call_budget`` -> the deployment is
        # exactly as before, and nothing is written on the model-call counter.
        with reserved_transport(self._gate(), TENANT):
            pass
        self.assertEqual(self._model_calls(), 0)

    def test_exhausted_budget_denies_with_a_named_reason(self) -> None:
        self._set_budget(2)
        self.db.reserve_model_call(TENANT, utc_now()[:10])
        self.db.reserve_model_call(TENANT, utc_now()[:10])
        decision = self._gate().evaluate(TENANT, "language_detect")
        self.assertFalse(decision.allowed)
        self.assertIn("model call budget", decision.reason)

    def test_authorize_raises_model_call_denied(self) -> None:
        self._set_budget(1)
        self.db.reserve_model_call(TENANT, utc_now()[:10])
        with self.assertRaises(ModelCallDenied) as caught:
            self._gate().authorize(TENANT, "language_detect")
        self.assertIn("model call budget", str(caught.exception))

    def test_release_floors_at_zero(self) -> None:
        self._set_budget(5)
        self.db.release_model_call(TENANT, utc_now()[:10])
        self.assertEqual(self._model_calls(), 0)


class AuxiliaryTransportBudgetTests(BudgetFixture):
    """Auxiliary surfaces reserve around the transport and only then."""

    def _language(self, provider: FakeModelProvider | None) -> LanguageService:
        return LanguageService(provider, "zh", model_gate=self._gate())

    def test_detect_and_translate_reserve_one_unit_each(self) -> None:
        self._set_budget(5)
        provider = FakeModelProvider()
        service = self._language(provider)
        language, source = service.detect("こんにちは", TENANT)
        self.assertEqual(source, "model")
        self.assertEqual(self._model_calls(), 1)
        _, translated, t_source = service.translate("原始回复", "ja", TENANT)
        self.assertEqual(t_source, "model")
        self.assertEqual(self._model_calls(), 2)
        self.assertEqual(len(provider.calls), 2)

    def test_exhausted_budget_stops_the_transport_as_denied(self) -> None:
        self._set_budget(1)
        provider = FakeModelProvider()
        service = self._language(provider)
        _, source = service.detect("こんにちは", TENANT)
        self.assertEqual(source, "model")
        _, translated, t_source = service.translate("原始回复", "ja", TENANT)
        self.assertEqual(t_source, "denied")
        self.assertFalse(translated)
        self.assertEqual(len(provider.calls), 1, "the refused translate must not transport")
        self.assertEqual(self._model_calls(), 1)

    def test_failed_transport_releases_the_reservation(self) -> None:
        self._set_budget(1)
        provider = FakeModelProvider(fail=True)
        service = self._language(provider)
        _, source = service.detect("こんにちは", TENANT)
        self.assertEqual(source, "failed")
        self.assertEqual(self._model_calls(), 0, "an outage must not bill the tenant")
        # The unit is available again: the next call transports and consumes.
        service.model_provider = FakeModelProvider()
        _, source = service.detect("こんにちは", TENANT)
        self.assertEqual(source, "model")
        self.assertEqual(self._model_calls(), 1)

    def test_returned_but_unusable_output_is_still_consumed(self) -> None:
        # The transport happened and the provider did the work: only a
        # *raised* transport is released.
        self._set_budget(1)
        service = self._language(FakeModelProvider(garbage=True))
        _, source = service.detect("こんにちは", TENANT)
        self.assertEqual(source, "failed")
        self.assertEqual(self._model_calls(), 1)

    def test_paths_without_a_transport_consume_nothing(self) -> None:
        self._set_budget(5)
        # Already in the service language: translate returns ``none``.
        _, translated, t_source = self._language(FakeModelProvider()).translate(
            "原始回复", "zh", TENANT
        )
        self.assertEqual(t_source, "none")
        # No provider configured at all: ``unconfigured``.
        _, d_source = self._language(None).detect("こんにちは", TENANT)
        self.assertEqual(d_source, "unconfigured")
        self.assertEqual(self._model_calls(), 0)

    def test_summaries_reserve_when_the_limit_is_set(self) -> None:
        self._set_budget(5)
        conv = self.db.create_conversation(TENANT, "Summarised", None, "web", "admin", 120)
        self.db.add_message(TENANT, conv["id"], "customer", "customer", "需要帮助", None, None)
        service = SummaryService(self.db, FakeModelProvider(), model_gate=self._gate())
        row = service.generate(TENANT, conv["id"], "context")
        self.assertEqual(row["source"], "model")
        self.assertEqual(self._model_calls(), 1)

    def test_copilot_suggest_refusal_falls_back_to_canned(self) -> None:
        self._set_budget(1)
        conv = self.db.create_conversation(TENANT, "Copilot", None, "web", "admin", 120)
        self.db.add_message(TENANT, conv["id"], "customer", "customer", "需要帮助", None, None)
        self.db.create_canned_response(
            TENANT,
            title="问候",
            body="您好，很高兴为您服务。",
            shortcut=None,
            tags=[],
            actor_id="admin",
        )
        provider = FakeModelProvider()
        service = CopilotService(self.db, provider, model_gate=self._gate())
        first = service.suggest_reply(TENANT, conv["id"], None, 1)
        self.assertEqual(first[0]["source"], "model")
        self.assertEqual(self._model_calls(), 1)
        second = service.suggest_reply(TENANT, conv["id"], None, 1)
        self.assertEqual(second[0]["source"], "denied")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self._model_calls(), 1)

    def test_copilot_rewrite_releases_on_failure(self) -> None:
        self._set_budget(1)
        service = CopilotService(self.db, FakeModelProvider(fail=True), model_gate=self._gate())
        result = service.rewrite_tone("原始草稿", "friendly", TENANT)
        self.assertEqual(result["source"], "failed")
        self.assertEqual(self._model_calls(), 0)


class TriageTurnBudgetTests(BudgetFixture):
    """The main chain's triage call is a governed call like any other."""

    def _settings(self) -> Settings:
        return Settings(database_path=self.db_path, auth_mode="demo", openai_model=DEFAULT_MODEL)

    def _turn(self, provider: FakeModelProvider, *, message: str, key: str) -> dict:
        orchestrator = ConversationOrchestrator(self.db, self._settings(), None)
        orchestrator.data_plane_config = DataPlaneConfig(self.db, SECRET)
        orchestrator.triage = TriageAgent(provider, model_gate=orchestrator.model_gate)
        conv = self.db.create_conversation(TENANT, "Budget Turn", None, "web", "admin", 120)
        response = orchestrator.handle_customer_message(TENANT, conv["id"], message, "admin", key)
        return response

    def _denied_reasons(self) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM audit_events WHERE tenant_id = ? "
                "AND event_type = 'turn.model_denied'",
                (TENANT,),
            ).fetchall()
        return [str(row["payload_json"]) for row in rows]

    def test_model_turn_consumes_exactly_one_unit(self) -> None:
        self._set_budget(5)
        provider = FakeModelProvider()
        self._turn(provider, message=NEUTRAL_MESSAGE, key="budget-turn-1")
        self.assertEqual(len(provider.calls), 1, "the permitted turn transports once")
        self.assertEqual(self._model_calls(), 1)
        # The turn counter keeps its own denomination: turns, not model calls.
        self.assertEqual(self.db.get_tenant_daily_usage(TENANT, utc_now()[:10]), 1)

    def test_rule_path_turn_consumes_nothing(self) -> None:
        self._set_budget(5)
        provider = FakeModelProvider()
        self._turn(provider, message=RULE_MESSAGE, key="budget-turn-2")
        self.assertEqual(provider.calls, [], "a high-confidence rule decision never transports")
        self.assertEqual(self._model_calls(), 0)

    def test_failed_triage_releases_the_reservation(self) -> None:
        self._set_budget(5)
        provider = FakeModelProvider(fail=True)
        self._turn(provider, message=NEUTRAL_MESSAGE, key="budget-turn-3")
        self.assertEqual(len(provider.calls), 1, "the transport was attempted")
        self.assertEqual(self._model_calls(), 0, "and its failure released the unit")

    def test_exhausted_budget_denies_the_turn_before_transport(self) -> None:
        self._set_budget(1)
        self.db.reserve_model_call(TENANT, utc_now()[:10])
        provider = FakeModelProvider()
        self._turn(provider, message=NEUTRAL_MESSAGE, key="budget-turn-4")
        self.assertEqual(provider.calls, [], "a budget refusal must not egress")
        self.assertEqual(len(self._denied_reasons()), 1)
        self.assertIn("model call budget", self._denied_reasons()[0])

    def test_no_limit_means_the_turn_leaves_no_counter_trace(self) -> None:
        provider = FakeModelProvider()
        self._turn(provider, message=NEUTRAL_MESSAGE, key="budget-turn-5")
        self.assertEqual(len(provider.calls), 1, "the turn uses the model exactly as before")
        self.assertEqual(self._model_calls(), 0)


class ModelCallPolicyApiTests(BudgetFixture):
    """The policy API carries the new limit and the counter it enforces."""

    def test_roundtrip(self) -> None:
        self.db.set_tenant_model_policy(TENANT, ["gpt-4o"], 50, 25)
        policy = self.db.get_tenant_model_policy(TENANT)
        self.assertEqual(policy["daily_model_call_budget"], 25)
        self.db.set_tenant_model_policy(TENANT, ["gpt-4o"], 50)
        self.assertIsNone(self.db.get_tenant_model_policy(TENANT)["daily_model_call_budget"])
