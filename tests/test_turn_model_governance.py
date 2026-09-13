"""H04 gap: the main chain's own model call clears the same tenant policy.

2.12.0 gave every *auxiliary* surface one governed entry
(:class:`~app.model_gateway.ModelCallGate`), and 2.14.0 made its outcomes
distinguishable. The main chain -- the single most consequential model call in
the system -- kept its own three hand-rolled checks, and it derived the model
ref from the *prompt version alone*::

    model_ref = prompt_version.model_ref if prompt_version else None

A deployment with no active prompt version therefore handed ``None`` to the
43.5 disable surface, and :func:`~app.model_provider.check_model_policy` treats
an unattributable ref as unblocked. So for a tenant that had disabled the
provider, or forbidden data egress, the main chain still egressed once per turn
-- while ``agents.py`` went on to call the provider with the deployment default
(``Settings.openai_model``), because it passes ``None`` as ``model_ref`` and the
provider resolves its own model.

The auxiliary surfaces never had this hole: the gate's ``evaluate`` falls back
to ``default_model_ref``, and ``tests/test_model_call_governance.py`` pins that
``disabled_providers``/``allowed_models`` deny them with **no prompt pinned at
all** (``test_disabled_provider_denies``, ``test_model_outside_allow_list_denies``).
The main chain was the *laxer* of the two, which is the H04/T02 invariant --
"one policy entry, the two sides cannot drift" -- read backwards.

These tests pin the main chain to that same evaluation: the ref the transport
will actually use, every facet, one refusal record, one telemetry count. A
deployment that configures no policy must keep using the model exactly as
before -- the fix only removes the bypass, it does not disable the model path.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.agents import TriageAgent
from app.config import Settings
from app.control_plane import DataPlaneConfig, TenantControlPlane, TenantPolicy
from app.database import Database, utc_now
from app.model_provider import ModelResponse
from app.orchestrator import ConversationOrchestrator
from app.prompts import PromptRegistry

SECRET = "control-plane-signing-secret-0123456789abcdef"
TENANT = "acme"
DEFAULT_MODEL = "gpt-4.1-mini"

# A message the deterministic rules route at low confidence (no intent signal),
# so the triage model path is actually taken when it is permitted.
NEUTRAL_MESSAGE = "Hello there"


def _policy(**overrides: object) -> TenantPolicy:
    kwargs: dict = {"plan": "standard", "region": "local"}
    kwargs.update(overrides)
    return TenantPolicy(**kwargs)


class ScriptedTriageProvider:
    """Deterministic provider stub returning a valid triage payload.

    ``calls`` records the ``model_ref`` argument of every transport, so a test
    can assert both *whether* the main chain egressed and *which* ref it used.
    """

    def __init__(self, *, route: str = "knowledge", confidence: float = 0.66) -> None:
        self.route = route
        self.confidence = confidence
        self.calls: list[str | None] = []

    def complete(
        self, system_prompt: str, user_prompt: str, model_ref: str | None = None
    ) -> ModelResponse:
        self.calls.append(model_ref)
        payload = {
            "route": self.route,
            "intent": "general_question",
            "confidence": self.confidence,
            "urgency": "normal",
            "reasons": ["Model semantic routing"],
        }
        return ModelResponse(content=json.dumps(payload, ensure_ascii=False))


class MainChainGovernanceTests(unittest.TestCase):
    """The triage call is a governed call, with or without a pinned prompt."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "turn-governance.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.ensure_tenant(TENANT)
        self.cp = TenantControlPlane(self.db, SECRET)
        self.dp = DataPlaneConfig(self.db, SECRET)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------- helpers

    def _settings(self) -> Settings:
        return Settings(database_path=self.db_path, auth_mode="demo", openai_model=DEFAULT_MODEL)

    def _orchestrator(self, provider: ScriptedTriageProvider) -> ConversationOrchestrator:
        """Wire the stub as the *only* model-backed surface in the turn.

        The orchestrator's own provider is left unset, so the auxiliary
        services (language, summaries) never transport; ``provider.calls``
        therefore measures the main chain exactly.
        """
        orchestrator = ConversationOrchestrator(self.db, self._settings(), None)
        orchestrator.data_plane_config = self.dp
        orchestrator.triage = TriageAgent(provider)
        return orchestrator

    def _turn(
        self, provider: ScriptedTriageProvider, *, message: str = NEUTRAL_MESSAGE, key: str
    ) -> tuple[dict, str]:
        orchestrator = self._orchestrator(provider)
        conv = self.db.create_conversation(TENANT, "Governed Turn", None, "web", "admin", 120)
        response = orchestrator.handle_customer_message(TENANT, conv["id"], message, "admin", key)
        return response, conv["id"]

    def _denied_events(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM audit_events WHERE tenant_id = ? "
                "AND event_type = 'turn.model_denied'",
                (TENANT,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def _audit_types(self) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT event_type FROM audit_events WHERE tenant_id = ?", (TENANT,)
            ).fetchall()
        return [row["event_type"] for row in rows]

    def _routed(self) -> dict:
        """The triage outcome: ``mode`` proves which path actually decided."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM audit_events WHERE tenant_id = ? "
                "AND event_type = 'agent.routed'",
                (TENANT,),
            ).fetchone()
        return json.loads(row["payload_json"])

    def _deny_provider(self) -> None:
        self.cp.set_policy(TENANT, _policy(model_policy={"disabled_providers": ["openai"]}))
        self.dp.apply_snapshot(self.cp.issue_snapshot(TENANT))

    def _pin_prompt(self, model_ref: str) -> None:
        registry = PromptRegistry(self.db)
        version = registry.create_version(None, "triage_prompt", "1.0", "body", model_ref, "admin")
        registry.activate(None, version.id, "admin")

    # ------------------------------------------------------ the disable surface

    def test_unpinned_prompt_still_honours_the_disabled_provider(self) -> None:
        """No active prompt version must not mean "no disable surface"."""
        self._deny_provider()
        provider = ScriptedTriageProvider()
        self._turn(provider, key="idem-turn-gov-1")
        # A refusal is a decision: nothing left the process.
        self.assertEqual(provider.calls, [])
        events = self._denied_events()
        self.assertEqual(len(events), 1)
        self.assertIn("provider", events[0].get("reason") or "")
        self.assertEqual(events[0]["model_ref"], DEFAULT_MODEL)
        # ...and the turn fell back to the deterministic path.
        self.assertEqual(self._routed()["mode"], "rules")

    def test_unpinned_prompt_still_honours_region_egress(self) -> None:
        """The provider processes in another region; egress must be refused."""
        self.cp.set_policy(TENANT, _policy(model_policy={"allow_data_egress": False}))
        self.dp.apply_snapshot(self.cp.issue_snapshot(TENANT))
        provider = ScriptedTriageProvider()
        self._turn(provider, key="idem-turn-gov-2")
        self.assertEqual(provider.calls, [])
        events = self._denied_events()
        self.assertEqual(len(events), 1)
        self.assertIn("region", events[0].get("reason") or "")

    def test_unpinned_prompt_still_honours_a_disabled_model(self) -> None:
        """``disabled_models`` must match the model the transport would use."""
        self.cp.set_policy(TENANT, _policy(model_policy={"disabled_models": [DEFAULT_MODEL]}))
        self.dp.apply_snapshot(self.cp.issue_snapshot(TENANT))
        provider = ScriptedTriageProvider()
        self._turn(provider, key="idem-turn-gov-3")
        self.assertEqual(provider.calls, [])
        events = self._denied_events()
        self.assertEqual(len(events), 1)
        self.assertIn("disabled", events[0].get("reason") or "")

    # ---------------------------------------------------------- the allow-list

    def test_unpinned_prompt_still_honours_the_allow_list(self) -> None:
        """The main chain must not be laxer than the auxiliary surfaces.

        ``tests/test_model_call_governance.test_model_outside_allow_list_denies``
        already denies an *auxiliary* call whose ref is outside the tenant's
        ``allowed_models``. The main chain routed the same turn through the
        model anyway, because an unpinned prompt produced ``None`` and the
        allow-list deliberately ignores a missing ref.
        """
        self.db.set_tenant_model_policy(TENANT, ["some-other-model"], None)
        provider = ScriptedTriageProvider()
        self._turn(provider, key="idem-turn-gov-4")
        self.assertEqual(provider.calls, [])
        events = self._denied_events()
        self.assertEqual(len(events), 1)
        self.assertIn("allow-list", events[0].get("reason") or "")

    def test_prompt_pinned_ref_is_still_evaluated(self) -> None:
        """A pinned prompt keeps its own ref as the one under evaluation."""
        self.db.set_tenant_model_policy(TENANT, ["openai/gpt-4.1-mini"], None)
        self._pin_prompt("anthropic/claude-3")
        provider = ScriptedTriageProvider()
        self._turn(provider, key="idem-turn-gov-5")
        self.assertEqual(provider.calls, [])
        events = self._denied_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["model_ref"], "anthropic/claude-3")

    # ------------------------------------------------------------- the happy path

    def test_permissive_tenant_still_routes_through_the_model(self) -> None:
        """Removing a bypass must not disable the model path itself."""
        provider = ScriptedTriageProvider()
        self._turn(provider, key="idem-turn-gov-6")
        self.assertTrue(provider.calls, "the permitted turn must still use the model")
        # The transport was offered no explicit ref, exactly as before: the
        # provider resolves the deployment default itself.
        self.assertIsNone(provider.calls[0])
        self.assertEqual(self._denied_events(), [])
        # The model path decided the route, not the deterministic rules.
        self.assertEqual(self._routed()["mode"], "model")

    def test_no_control_plane_keeps_the_model_path(self) -> None:
        """No policy document at all must stay fail-open, as before 43.5."""
        orchestrator = ConversationOrchestrator(self.db, self._settings(), None)
        provider = ScriptedTriageProvider()
        orchestrator.triage = TriageAgent(provider)
        conv = self.db.create_conversation(TENANT, "No Plane", None, "web", "admin", 120)
        orchestrator.handle_customer_message(TENANT, conv["id"], NEUTRAL_MESSAGE, "admin", "idem-7")
        self.assertTrue(provider.calls)
        self.assertEqual(self._denied_events(), [])

    # ------------------------------------------------------------------ budget

    def test_exhausted_budget_is_audited_as_budget_and_not_as_a_model_denial(self) -> None:
        """Budget stays a distinct event type, and it is still visible."""
        self.db.set_tenant_model_policy(TENANT, None, 1)
        self.db.increment_tenant_usage(TENANT, utc_now()[:10])
        provider = ScriptedTriageProvider()
        response, _ = self._turn(provider, key="idem-turn-gov-8")
        self.assertEqual(provider.calls, [])
        self.assertTrue(response["assistant_message"]["metadata"]["budget_exceeded"])
        types = self._audit_types()
        self.assertEqual(types.count("turn.budget_exceeded"), 1)
        self.assertEqual(types.count("turn.model_denied"), 0)


class MainChainRefusalTelemetryTests(MainChainGovernanceTests):
    """Every refused governed call is counted once, with its purpose."""

    @staticmethod
    def _keys(name: str) -> list[str]:
        from app import telemetry

        return [key for key in telemetry.metrics.snapshot()["counters"] if name in key]

    @staticmethod
    def _total(name: str) -> float:
        from app import telemetry

        counters = telemetry.metrics.snapshot()["counters"]
        return sum(value for key, value in counters.items() if name in key)

    def test_refusal_is_counted_as_a_denied_triage_call(self) -> None:
        """``PURPOSE_TRIAGE`` was in the closed catalogue but never counted."""
        self._deny_provider()
        denied_before = self._total("model.call_denied")
        failed_before = self._total("model.call_failed")
        self._turn(ScriptedTriageProvider(), key="idem-turn-tel-1")
        self.assertEqual(self._total("model.call_denied"), denied_before + 1)
        # A refusal is a decision, never an outage.
        self.assertEqual(self._total("model.call_failed"), failed_before)
        self.assertTrue(
            any("purpose=triage" in key for key in self._keys("model.call_denied")),
            self._keys("model.call_denied"),
        )

    def test_exhausted_budget_is_also_counted_with_its_purpose(self) -> None:
        """The budget facet is refused ahead of the gate; it still owes a count."""
        self.db.set_tenant_model_policy(TENANT, None, 1)
        self.db.increment_tenant_usage(TENANT, utc_now()[:10])
        denied_before = self._total("model.call_denied")
        self._turn(ScriptedTriageProvider(), key="idem-turn-tel-2")
        self.assertEqual(self._total("model.call_denied"), denied_before + 1)
        self.assertTrue(
            any("purpose=triage" in key for key in self._keys("model.call_denied")),
            self._keys("model.call_denied"),
        )

    def test_permitted_turn_counts_no_denial(self) -> None:
        denied_before = self._total("model.call_denied")
        self._turn(ScriptedTriageProvider(), key="idem-turn-tel-3")
        self.assertEqual(self._total("model.call_denied"), denied_before)


if __name__ == "__main__":
    unittest.main()
