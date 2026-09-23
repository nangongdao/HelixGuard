"""`docs/api/reference.md` is generated, so it must equal its generator output.

This guard exists because the drift it now prevents was real, large and silent.
At 2.30.0 the committed reference documented 141 of the snapshot's 181 paths
(215 operations): `scripts/api_docs.py` regenerates the file from
`api/openapi.json`, but **nothing ran it**, so the published API reference had
been wrong for several versions while every gate stayed green. Regenerating it
was registered in `docs/DOMAIN.md` §5.1 as an item decoupled from the domain
migration precisely because the diff is thousands of unrelated lines.

Line endings are normalised before comparing: this worktree is CRLF
(`core.autocrlf=true`) and CI checks out LF, so a byte-for-byte comparison would
pass locally and fail in CI (the same trap `performance_gate._lf_bytes` exists
to avoid).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.api_docs import SNAPSHOT, generate

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "docs" / "api" / "reference.md"


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def _snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


class ApiReferenceFreshnessTests(unittest.TestCase):
    def test_reference_is_exactly_the_generator_output(self) -> None:
        self.assertEqual(
            _normalized(REFERENCE.read_text(encoding="utf-8")),
            _normalized(generate()),
            "docs/api/reference.md is stale — run `python scripts/api_docs.py`",
        )

    def test_every_snapshot_path_is_documented(self) -> None:
        """Non-vacuous: assert the generator's reach, not just its self-agreement.

        `test_reference_is_exactly_the_generator_output` would also pass on a
        generator that silently dropped half the spec, so pin the coverage.
        """
        spec = _snapshot()
        doc = _normalized(REFERENCE.read_text(encoding="utf-8"))
        missing = [path for path in spec["paths"] if f"`{path}`" not in doc]
        self.assertEqual(
            missing,
            [],
            f"{len(missing)} of {len(spec['paths'])} snapshot paths are undocumented: {missing[:5]}",
        )

    def test_version_line_tracks_the_snapshot(self) -> None:
        version = _snapshot()["info"]["version"]
        self.assertIn(f"Version: `{version}`", _normalized(REFERENCE.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
