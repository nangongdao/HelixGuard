"""Performance budget gate wired into pytest (ROADMAP section 43.6).

The static byte-budget layer runs in the default pass (no browser needed).
The real-browser layer (LCP/CLS/long tasks/10k queue render/heap) is a
nightly/CI job — it needs Playwright Chromium plus a live server, so here we
only assert the gate script itself stays importable and its budgets
internally consistent.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import performance_gate
from scripts.performance_gate import (
    BROWSER_BUDGETS,
    BUDGETS,
    HEAP_QUANTIZED_BYTES,
    check_static_budgets,
    heap_growth_mb,
    heap_growth_problem,
    leak_retention_problem,
)


class StaticBudgetTests(unittest.TestCase):
    def test_first_paint_payload_within_budgets(self) -> None:
        sizes, problems, _unenforced = check_static_budgets()
        self.assertEqual(problems, [], "\n".join(problems))
        # Sanity: the measured payload is non-trivial (guards against the
        # glob silently matching nothing after a layout change).
        self.assertGreater(sizes["operator_js_bytes"], 50_000)
        self.assertGreater(sizes["operator_css_bytes"], 10_000)
        self.assertGreater(sizes["widget_js_bytes"], 5_000)

    def test_budget_keys_are_complete(self) -> None:
        sizes, _problems, _unenforced = check_static_budgets()
        self.assertEqual(set(sizes), set(BUDGETS))

    def test_every_budget_is_either_measured_or_named_unenforced(self) -> None:
        """A budget must never drop out of the gate without saying so.

        ``app/static/dist`` is gitignored, so its bytes are simply not on disk in
        an unbuilt checkout. Subtracting it silently is how the operator ceiling
        ended up ~88% above the number CI compared against it.
        """
        sizes, _problems, unenforced = check_static_budgets()
        named = "\n".join(unenforced)
        for key in BUDGETS:
            self.assertTrue(
                sizes[key] > 0 or key in named,
                f"{key} is neither measured nor reported as unenforced",
            )

    def test_browser_budgets_cover_43_6_dimensions(self) -> None:
        for key in (
            "lcp_ms",
            "fcp_ms",
            "cls",
            "fid_ms",
            "tti_ms",
            "long_task_count_30s",
            "queue_10k_render_ms",
            "heap_growth_mb",
            # §43.6 residual closed: INP is asserted directly, not proxied
            # through long tasks.
            "inp_ms",
            "inp_desktop_ms",
            # Detail open/close leak probe — retention after close must stay
            # under budget (5 open/close cycles, sampled post-GC).
            "detail_leak_mb",
        ):
            self.assertIn(key, BROWSER_BUDGETS)

    def test_desktop_budgets_have_web_twins(self) -> None:
        """Every desktop paint budget must have a corresponding web twin.

        The browser layer reuses one metrics_script for both contexts and
        maps web keys to desktop keys via ``desktop_key_map``; a desktop
        budget without a web twin would silently stay unassigned (the
        fail-open shape that bit this gate repeatedly). This test pins the
        pairing so adding a new metric requires consciously adding both
        sides of the map.
        """
        desktop_twins = (
            ("fcp_ms", "fcp_desktop_ms"),
            ("lcp_ms", "lcp_desktop_ms"),
            ("cls", "cls_desktop"),
            ("fid_ms", "fid_desktop_ms"),
            ("tti_ms", "tti_desktop_ms"),
        )
        for web_key, desktop_key in desktop_twins:
            self.assertIn(web_key, BROWSER_BUDGETS)
            self.assertIn(desktop_key, BROWSER_BUDGETS)


class HeapBudgetTests(unittest.TestCase):
    """The heap budget must be measured, never assumed.

    These pin the fail-closed states without a browser: a quantized or absent
    ``performance.memory`` has to be *reported*, never read as "no growth".
    Both states were live in the nightly job — Chromium quantizes
    ``usedJSHeapSize`` to a fixed 10 MB unless launched with
    ``--enable-precise-memory-info``, so every sample was identical, the
    tail-minus-first growth was 0.0 MB, and a 15 MB budget that cannot fail
    reported green for four consecutive nights.
    """

    def test_quantized_heap_is_reported_not_read_as_zero(self) -> None:
        problem = heap_growth_problem([HEAP_QUANTIZED_BYTES] * 5)
        self.assertIsNotNone(problem)
        self.assertIn("cannot fail", problem)

    def test_missing_memory_is_reported(self) -> None:
        for samples in ([], [0, 0, 0]):
            with self.subTest(samples=samples):
                self.assertIsNotNone(heap_growth_problem(samples))

    def test_real_samples_are_measurable(self) -> None:
        self.assertIsNone(heap_growth_problem([1_000_000, 1_500_000, 1_200_000]))

    def test_growth_is_tail_versus_first_sample(self) -> None:
        # Budget is *retention*: a leak that climbs across every cycle has to
        # register even when each individual step is small, and a heap that
        # climbs then dips must not be netted out by the dip.
        mb = 1024 * 1024
        samples = [mb, 3 * mb, 5 * mb, 7 * mb, 9 * mb]
        self.assertEqual(heap_growth_mb(samples), 8.0)

    def test_growth_of_a_flat_heap_is_zero(self) -> None:
        mb = 1024 * 1024
        self.assertEqual(heap_growth_mb([mb, mb + 1024, mb, mb + 2048]), 0.0)


class LeakBudgetTests(unittest.TestCase):
    """The retention budget must fail closed for the same reason the heap does.

    The probe used to record a number only when every sample was positive, so
    an unreadable or quantized heap produced silence instead of a problem: the
    5 MB retention budget went unenforced with no signal. These pin the
    reporting so the second budget cannot become another that cannot fail.
    """

    def test_unreadable_heap_is_reported(self) -> None:
        for samples in ([], [0, 0, 0, 0, 0]):
            with self.subTest(samples=samples):
                problem = leak_retention_problem(samples)
                self.assertIsNotNone(problem)
                self.assertIn("unenforced", problem)

    def test_quantized_heap_is_reported_not_read_as_no_leak(self) -> None:
        problem = leak_retention_problem([HEAP_QUANTIZED_BYTES] * 5)
        self.assertIsNotNone(problem)
        self.assertIn("cannot fail", problem)

    def test_real_samples_are_measurable(self) -> None:
        self.assertIsNone(leak_retention_problem([900_000, 905_000, 910_000, 902_000, 908_000]))


class HeapSessionTests(unittest.TestCase):
    """The precise-memory session must be unconditional, and separate.

    ``PERF_PRECISE_MEMORY`` is how the heap budget silently went unenforced:
    the flag defaulted off, the nightly job never set it, and the budget that
    could not fail reported green. The precise session is now unconditional —
    reintroducing an environment switch that can turn the measurement off
    breaks ``test_precise_memory_is_not_opt_in`` on purpose. The *timing*
    session must stay flag-free: ``--enable-precise-memory-info`` disables some
    allocator optimizations, which measurably skews the wall-clock budgets
    measured in the same session.
    """

    PRECISE_FLAG = "--enable-precise-memory-info"

    def test_precise_memory_is_not_opt_in(self) -> None:
        self.assertFalse(
            hasattr(performance_gate, "PERF_PRECISE_MEMORY"),
            "an environment switch that can silently disable the heap budget is back",
        )

    def test_precise_sessions_carry_the_flag_and_the_timing_session_does_not(self) -> None:
        self.assertIn(self.PRECISE_FLAG, performance_gate.HEAP_SESSION_ARGS)
        self.assertIn(self.PRECISE_FLAG, performance_gate.LEAK_PROBE_ARGS)
        self.assertNotIn(self.PRECISE_FLAG, performance_gate.TIMING_SESSION_ARGS)


class StaticBudgetCoverageTests(unittest.TestCase):
    """The static budgets must measure everything and must still be able to fail.

    Three structural properties, all learned the hard way. The widget payload used
    to name its two files explicitly, so a second widget module would have escaped
    the budget silently. The operator payload used to fold the gitignored Vite
    build output into the same number as the tracked sources, so the ceiling was
    anchored to a dist-inclusive payload while CI measured a dist-exclusive one.
    And the measurement used raw working-tree bytes, so a CRLF checkout counted an
    extra byte per line and the *same commit* measured two different sizes. All
    three are the fail-open shape of a ceiling that sits too far above the number
    it is compared against — so all three are asserted against a synthetic tree
    rather than whichever tree happens to be on disk.
    """

    def _temp_tree(self, widget_module_bytes: int) -> tuple[tempfile.TemporaryDirectory, Path]:
        tmp = tempfile.TemporaryDirectory()
        static = Path(tmp.name) / "app" / "static"
        (static / "js").mkdir(parents=True)
        (static / "css").mkdir(parents=True)
        (static / "app.js").write_bytes(b"// entry\n")
        (static / "js" / "app-core.js").write_bytes(b"// core\n")
        (static / "styles.css").write_bytes(b"/* styles */\n")
        (static / "css" / "tokens.css").write_bytes(b"/* tokens */\n")
        (static / "submission-portal-app.js").write_bytes(b"// portal shell\n")
        (static / "js" / "submission-portal-core.js").write_bytes(b"// core\n")
        (static / "js" / "submission-portal-extra.js").write_bytes(
            b"//" + b"x" * widget_module_bytes + b"\n"
        )
        return tmp, static

    def test_portal_payload_includes_every_portal_module(self) -> None:
        tmp, static = self._temp_tree(400)
        self.addCleanup(tmp.cleanup)
        names = [path.name for path in performance_gate._widget_payload_files(static)]
        self.assertEqual(
            names,
            ["submission-portal-app.js", "submission-portal-core.js", "submission-portal-extra.js"],
        )

        with patch.object(performance_gate, "ROOT", Path(tmp.name)):
            sizes, _unenforced = performance_gate._static_payload()
        expected = performance_gate._lf_bytes(performance_gate._widget_payload_files(static))
        self.assertEqual(sizes["widget_js_bytes"], expected)

    def test_portal_budget_reports_a_problem_when_a_module_grows(self) -> None:
        tmp, _static = self._temp_tree(BUDGETS["widget_js_bytes"] + 1_000)
        self.addCleanup(tmp.cleanup)
        with patch.object(performance_gate, "ROOT", Path(tmp.name)):
            _sizes, problems, _unenforced = check_static_budgets()
        self.assertTrue(problems, "an oversized widget module must fail the gate")
        self.assertIn("widget_js_bytes", "\n".join(problems))

    def test_static_budgets_keep_bounded_headroom(self) -> None:
        sizes, _problems, unenforced = check_static_budgets()
        measured = {key: size for key, size in sizes.items() if size > 0}
        # Every budget is either measured here or named as unenforced — nothing
        # may leave the gate without a trace.
        self.assertEqual(len(measured) + len(unenforced), len(BUDGETS))
        for key, size in measured.items():
            ratio = BUDGETS[key] / size
            self.assertLessEqual(
                ratio,
                performance_gate.MAX_STATIC_HEADROOM,
                f"{key}: ceiling {BUDGETS[key]} sits {ratio:.1%} above the measured {size}",
            )


class StaticPayloadMeasurementTests(unittest.TestCase):
    """The measurement must be a property of the commit, not of the checkout.

    The first H01 revision failed in CI because the same commit measured two
    different operator payloads — 425,757 B in a CRLF working tree (one extra
    byte per line) and 415,634 B in CI — and because the gitignored Vite build
    output was folded into the tracked-source total. Ceilings are compared against
    these numbers, so both have to be settled before the ratio means anything.
    """

    def _temp_tree(self, eol: bytes) -> tuple[tempfile.TemporaryDirectory, Path]:
        tmp = tempfile.TemporaryDirectory()
        static = Path(tmp.name) / "app" / "static"
        (static / "js").mkdir(parents=True)
        (static / "css").mkdir(parents=True)
        body = eol.join([b"// entry", b"const a = 1;", b"// trailing"]) + eol
        for rel in (
            "app.js",
            "js/app-core.js",
            "styles.css",
            "css/tokens.css",
            "submission-portal-app.js",
            "js/submission-portal-core.js",
        ):
            (static / rel).write_bytes(body)
        return tmp, static

    def _measure(self, root: Path) -> tuple[dict[str, int], list[str]]:
        with patch.object(performance_gate, "ROOT", root):
            return performance_gate._static_payload()

    def test_eol_style_does_not_change_the_measured_payload(self) -> None:
        crlf_tmp, _ = self._temp_tree(b"\r\n")
        lf_tmp, _ = self._temp_tree(b"\n")
        self.addCleanup(crlf_tmp.cleanup)
        self.addCleanup(lf_tmp.cleanup)
        crlf_sizes, _ = self._measure(Path(crlf_tmp.name))
        lf_sizes, _ = self._measure(Path(lf_tmp.name))
        self.assertEqual(crlf_sizes, lf_sizes)

    def test_build_outputs_get_their_own_budget(self) -> None:
        tmp, static = self._temp_tree(b"\n")
        self.addCleanup(tmp.cleanup)
        before, unenforced = self._measure(Path(tmp.name))
        self.assertTrue(
            any("operator_dist_bytes" in notice for notice in unenforced),
            "an unbuilt checkout must report the React-runtime budget as unenforced",
        )

        assets = static / "dist" / "assets"
        assets.mkdir(parents=True)
        (assets / "index-abc123.js").write_bytes(b"x" * 5_000)
        (assets / "index-abc123.css").write_bytes(b"y" * 1_000)
        # On-demand chunk: excluded from the first-paint budget, by name.
        (assets / "terminal-deadbeef.js").write_bytes(b"z" * 9_000)
        after, unenforced_after = self._measure(Path(tmp.name))

        self.assertEqual(
            after["operator_js_bytes"],
            before["operator_js_bytes"],
            "a build output must not be folded into the tracked operator payload",
        )
        self.assertEqual(after["operator_dist_bytes"], 6_000)
        self.assertEqual(unenforced_after, [])


if __name__ == "__main__":
    unittest.main()
