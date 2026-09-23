"""Structural claims in the READMEs must match the repository.

Nothing in this suite reads `README.md`, which is why its island list still said
"8 个业务岛（quality/knowledge/ticket/…）" after the domain migration renamed two
of them and the island count had grown to 19. A README claim is a claim like any
other; the ones that can be checked cheaply should be.

Scope is deliberately narrow. Only claims that are *structural facts about this
repo* are asserted — the set of islands. Numbers like "1959 passed" or "89%
coverage" are NOT asserted: they change on every commit, and a guard that has to
be updated by hand every time is noise, not protection. Those stay a
verification-step responsibility (re-measure, then state).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
ISLAND_LOADER = ROOT / "frontend" / "src" / "island-loader.js"


def _declared_islands() -> list[str]:
    """Island names from `island-loader.js`, in declaration order."""
    text = ISLAND_LOADER.read_text(encoding="utf-8")
    return re.findall(r'\bname:\s*"([^"]+)"', text)


class ReadmeIslandClaimTests(unittest.TestCase):
    def test_loader_still_declares_islands(self) -> None:
        """Guard against a silent change in the loader's shape.

        If the config ever moves off an inline `name: "…"` literal, the sweep
        below would find nothing and quietly pass — so pin a non-vacuous floor.
        """
        islands = _declared_islands()
        self.assertGreaterEqual(len(islands), 15, f"loader parse found only {islands}")

    def test_readme_states_the_real_island_count(self) -> None:
        count = len(_declared_islands())
        readme = README.read_text(encoding="utf-8")
        self.assertIn(
            f"{count} 个岛",
            readme,
            f"README must state the real island count ({count}); update the "
            "「React 渐进式岛」bullet in README.md 产品亮点",
        )

    def test_readme_lists_every_island(self) -> None:
        islands = _declared_islands()
        readme = README.read_text(encoding="utf-8")
        missing = [name for name in islands if name not in readme]
        self.assertEqual(missing, [], f"README's island list is missing {missing}")
        self.assertIn(
            " / ".join(islands),
            readme,
            "README's island list must match island-loader.js in declaration order, "
            "not merely mention every name",
        )


if __name__ == "__main__":
    unittest.main()
