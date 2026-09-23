"""Wire-contract constants must survive a domain rename.

`docs/API_POLICY.md` says the response body only ever grows, and
`docs/DOMAIN.md` §3 draws the same line for protocol strings: permission
strings, webhook event names, request headers and DB enum values are *contract*,
not domain vocabulary. A migration that renames them changes behaviour without
changing any shape, so nothing structural catches it.

This guard exists because P6 hit exactly that. The DOM/identifier rename rewrote
`"knowledge:write"` to `"policy:write"` inside `app/static/js/policy-view.js`
while the server kept issuing `knowledge:write` (`app/security.py`
ROLE_PERMISSIONS). `canWriteKnowledge()` then returned false for every real
principal, so the writer-only branches went dead: the "新建草稿" button stayed
hidden, the editor never opened, and the published-only filter never relaxed.
Every Python test stayed green, the vitest fixture had been renamed in lockstep
(`tests/frontend/knowledge-view.test.js`), and the only symptom was **7.69%
pixel drift** on the `knowledge-view` surface of `scripts/visual_gate.py` —
which is how it was found.

The lesson is R8④ in `docs/DOMAIN_MIGRATION_PLAN.md`: a contract string renamed
on *both* sides of its own test is invisible. So this test reads the one
authoritative source (the server's role table) and checks every
permission-shaped literal the frontend compares against.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from app.db._util import _COLUMN_TO_WIRE, to_wire_row
from app.retention import PII_FIELDS, REDACTED, redact_pii
from app.security import ROLE_PERMISSIONS

ROOT = Path(__file__).resolve().parent.parent

# Only the actions the server actually mints. Deriving the alternation from
# ROLE_PERMISSIONS instead of hardcoding it keeps the pattern honest: add a
# permission server-side and the frontend literal for it is checked too.
_ACTIONS = sorted({perm.split(":", 1)[1] for perms in ROLE_PERMISSIONS.values() for perm in perms})
_PERMISSION = re.compile(r"""["']([a-z][a-z_]*:(?:""" + "|".join(_ACTIONS) + r"""))["']""")

FRONTEND_GLOBS = (
    "app/static/*.js",
    "app/static/js/*.js",
    "frontend/src/**/*.js",
    "frontend/src/**/*.jsx",
    "tests/frontend/*.js",
)


def _known_permissions() -> frozenset[str]:
    return frozenset(perm for perms in ROLE_PERMISSIONS.values() for perm in perms)


def _literals() -> dict[str, set[str]]:
    """Every permission-shaped literal in the frontend, mapped to its files."""
    found: dict[str, set[str]] = {}
    for pattern in FRONTEND_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in _PERMISSION.finditer(text):
                rel = path.relative_to(ROOT).as_posix()
                found.setdefault(match.group(1), set()).add(rel)
    return found


class PermissionStringTests(unittest.TestCase):
    def test_frontend_only_checks_permissions_the_server_issues(self) -> None:
        known = _known_permissions()
        unknown = {literal: files for literal, files in _literals().items() if literal not in known}
        self.assertEqual(
            unknown,
            {},
            "frontend compares against permission strings the server never issues "
            f"(ROLE_PERMISSIONS in app/security.py): {sorted(unknown)}",
        )

    def test_the_guard_can_actually_fail(self) -> None:
        """A guard that cannot fail is how the string slipped through.

        Assert the sweep is not vacuous (it must see the real literals) and that
        the one that regressed is not among them.
        """
        literals = _literals()
        self.assertGreater(len(literals), 5, f"sweep found too few permission literals: {literals}")
        for expected in ("knowledge:write", "conversation:read", "admin:manage", "operator:act"):
            self.assertIn(expected, literals)
        self.assertNotIn("policy:write", literals)

    def test_permission_strings_are_not_domain_renamed(self) -> None:
        """The legacy-worded permissions are protocol and must stay legacy-worded.

        This is the positive statement of the rule: `conversation:read` and
        `operator:act` are *supposed* to still read like the old domain, exactly
        like the `conversation.created` webhook event.
        """
        known = _known_permissions()
        for preserved in (
            "conversation:read",
            "conversation:write",
            "operator:act",
            "knowledge:write",
        ):
            self.assertIn(preserved, known)


class PiiFieldContractTests(unittest.TestCase):
    """`PII_FIELDS` names must survive the column rename.

    `docs/DOMAIN.md` §7.3 registered this as an unimplemented R8④ guard. The
    data layer renamed the columns (`submitter_name`, `assigned_reviewer`, ...)
    and `to_wire_row` maps them back at every row->dict boundary, so the
    redactor's input contract still reads in wire names. What was unguarded is
    the failure mode: drop one alias and the row keeps the *new* column name,
    `redact_pii` does not recognise it, and PII is exported in the clear — with
    no error anywhere.
    """

    def test_no_pii_field_is_a_column_that_gets_renamed_away(self) -> None:
        leaked = PII_FIELDS & set(_COLUMN_TO_WIRE)
        self.assertEqual(
            leaked,
            set(),
            f"PII_FIELDS lists {sorted(leaked)}, which _COLUMN_TO_WIRE renames to "
            "something else — to_wire_row can never emit that key, so redact_pii "
            "would silently miss it",
        )

    def test_renamed_pii_columns_keep_their_wire_name(self) -> None:
        wire_names = set(_COLUMN_TO_WIRE.values())
        for field in ("customer_name", "customer_ref", "assigned_agent"):
            self.assertIn(field, wire_names)
            self.assertIn(field, PII_FIELDS)

    def test_redaction_survives_the_rename_end_to_end(self) -> None:
        """The behavioural form: renamed columns must still come out redacted."""
        row = {
            "submitter_name": "林嘉",
            "submitter_ref": "CUST-1001",
            "assigned_reviewer": "demo.admin",
            "content": "敏感正文",
            "actor": "demo.admin",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        redacted = redact_pii(to_wire_row(row))
        self.assertIsInstance(redacted, dict)
        assert isinstance(redacted, dict)  # narrow for the type checker
        for field in ("customer_name", "customer_ref", "assigned_agent", "content", "actor"):
            self.assertEqual(
                redacted[field],
                REDACTED,
                f"{field} survived redaction — the wire alias for it is missing",
            )
        self.assertEqual(redacted["created_at"], row["created_at"])


if __name__ == "__main__":
    unittest.main()
