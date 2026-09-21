"""Canary sentinel inertness: the test marker must not change the turn it observes.

``make_canary()`` plants a unique marker inside a message and then asserts the
marker never reaches a sink.  That measurement is only about the sink if the
marker itself is *inert*: a marker whose shape trips a risk detector rewrites
the outcome the test set out to observe.

Concretely, ``canary-<uuid4().hex>`` carries 32 random hex characters, so its
body can hold a run of 15 or more consecutive decimal digits.  ``PolicyAgent._CARD``
reads a 15-18 digit run as a payment card and ``_PHONE`` reads 11 digits starting
with ``1[3-9]`` as a mobile number; either category sets ``requires_human=True``.
The adversarial case ``adv-secret-canary-sentinel`` therefore routed to
``escalation`` instead of ``knowledge`` every so often -- an intermittent CI
failure whose cause was the fixture, not the product.

Measured over 200 000 draws of the old generator: 0.38% produced a payment-card
match and 0.15% a phone match, so roughly 1% of adversarial runs had at least one
of the two sentinel cases escalate.

The fix belongs in the generator, not the detector.  Over-redacting a digit run
is the deliberate conservative stance of the policy agent (ADR-013), and
loosening a PII detector to make a test deterministic would trade a safety
control for a green build.  These tests pin both halves of that contract:

- the generated marker cannot contain a decimal digit at all, so no digit-run
  pattern can match it by construction, and
- the detector still flags a bare digit run -- the sentinel changed, not the
  control.
"""

from __future__ import annotations

import unittest

from app.agents import PolicyAgent
from app.redaction import make_canary, scan_for_canary

#: Enough draws that a ~0.5%-per-draw collision cannot pass by luck
#: (``1 - 0.995**5000`` is indistinguishable from 1) while staying fast.
SAMPLE = 5000


class CanarySentinelInertnessTests(unittest.TestCase):
    """A freshly generated sentinel is invisible to the risk detectors."""

    def test_generated_canary_never_trips_a_risk_category(self) -> None:
        policy = PolicyAgent()
        for _ in range(SAMPLE):
            canary = make_canary()
            risk = policy.inspect(f"违规内容怎么分级 {canary}")
            self.assertEqual(risk.categories, [], f"{canary} -> {risk.categories}")
            self.assertFalse(risk.requires_human, f"{canary} escalated the turn")

    def test_canary_body_carries_no_decimal_digit(self) -> None:
        for _ in range(SAMPLE):
            self.assertRegex(make_canary(), r"^canary-[a-f]{32}$")

    def test_generated_canaries_are_unique_and_scannable(self) -> None:
        canaries = {make_canary() for _ in range(SAMPLE)}
        self.assertEqual(len(canaries), SAMPLE, "sentinel must be unique per draw")
        for canary in canaries:
            self.assertEqual(scan_for_canary(f"前缀 {canary} 后缀"), [canary])

    def test_payment_card_detector_still_flags_a_bare_digit_run(self) -> None:
        risk = PolicyAgent().inspect("我的卡号是 4111111111111111")
        self.assertIn("payment_card", risk.categories)
        self.assertTrue(risk.requires_human)
