"""Frontend performance budget gate (ROADMAP section 43.6).

Two layers, both fail-closed on budget breach:

1. Static byte budgets (no browser needed) — the shipped first-paint payload
   (index.html + styles.css + tokens.css + app.js + every operator ES module)
   and the widget payload are measured against ``BUDGETS``. These run in the
   default pytest pass via :mod:`tests.test_performance_gate`.
2. Real-browser budgets (Playwright + Chromium, same harness as
   tests/ui_smoke.py) — a live server is measured twice: once as a plain web
   load (LCP / CLS / long tasks, queue render time with 10k synthetic rows,
   JS heap growth) and once with the Tauri shell preconditions set, which
   gates the desktop bundle's own LCP / CLS / React-island 10k render at the
   tighter §D5 budgets. Run via::

       python scripts/performance_gate.py --base-url http://127.0.0.1:8765

   CI wires this as a nightly job (see .github/workflows/ci.yml); it is not
   part of the per-PR gate because wall-clock timings vary by runner.

Usage:
    python scripts/performance_gate.py                 # static budgets only
    python scripts/performance_gate.py --base-url URL  # + browser metrics
    python scripts/performance_gate.py --update        # rewrite baseline JSON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))
from scripts._console import use_utf8_console

BASELINE = ROOT / "artifacts" / "performance-baseline.json"

# Byte budgets: raw (uncompressed) source sizes of the first-paint payload.
# The operator entry loads app.js (deferred legacy controller), main.js and
# its module graph; styles.css pulls tokens.css via @import. The widget ships
# its own shell. Budgets leave ~25% headroom over the 1.3.x measured baseline
# (operator JS 277 KB / CSS 86 KB, widget JS 20 KB at §43.6 delivery) so
# normal feature work lands without churn, but a runaway dependency or an
# unminified vendored blob fails the gate.
#
# D2 (DESKTOP_TAURI_PLAN.md §6.3): the budgets are widened to include the
# Vite-produced React runtime under app/static/dist/assets/. React 19 +
# ReactDOM ≈ 140 KB raw; TanStack Query + Zustand + per-island chunks keep
# the operator JS total under the new ceiling.
#
# D3 (app.js <500 campaign): the ceiling is raised 700 KB -> 715 KB. Moving a
# legacy domain out of app.js into an ES module costs ~0.9-1.5 KB per slice of
# pure boilerplate that the monolith never paid (import/export statements,
# module JSDoc, the thin wrappers app.js keeps for its own callers) — the
# moved logic itself is byte-neutral. Measured on slice 23 (palette + saved
# views, ~110 lines): +880 bytes net.
#
# == 2026-08-31 reconcile (app.js 477 lines < 500, campaign closed) ==
# The campaign ran 17 slices past the ^00e60c5 allowance. operator_js grew from
# 699,837 B (last green) to 725,218 B: +25.4 KB of pure module boilerplate
# across 12 new modules (js/*.js 36 -> 48 files), plus the unchanged React dist.
# app.js itself SHRANK 75.7 KB -> 23.1 KB, so the monolith is down while the
# module layer grew by exactly the cost model the note predicted. The D3 note
# said to "revisit (and re-tighten) once app.js is under 500 lines" — this is
# that revisit: the ceiling is re-anchored to the realized baseline with ~8%
# headroom so a runaway dependency or an unminified vendored blob (> 10% jump)
# still fails the gate.
# == 2026-09-17 re-anchor (H01: widget resumable send + idle polling) ==
# The widget payload grew 20,614 -> 26,218 B (LF, as shipped) because the customer
# shell gained the parts that must not be wrong offline: a send attempt that
# survives a failure (so a lost confirmation replays instead of double-posting),
# and a cursor-based idle poll so an operator reply and the CSAT link reach a
# customer who sends nothing. Unlike the operator side this is raw zero-build
# source: no Vite step, so comments and formatting ship as-is. The ceiling is
# re-anchored to the realized baseline with ~8% headroom — still far below the
# >10% jump a runaway dependency or an unminified vendored blob would produce.
#
# == 2026-09-17 measurement fix (found by CI run 35241301022, PR #36) ==
# The first version of the headroom assertion below failed in CI on
# operator_js_bytes: "ceiling 780000 sits 187.7% above the measured 415634" —
# while the same commit measured 746,821 B locally. Two environment dependencies
# were hiding inside one number:
#   1. EOL. The gate summed raw working-tree bytes, so a CRLF checkout counted one
#      extra byte per line: 425,757 B locally vs 415,634 B in CI for the *same*
#      tracked files. Git stores LF, so LF is what gets counted now.
#   2. Build outputs. app/static/dist is gitignored and exists only where the
#      frontend has been built; the CI `quality` job does not build it (only the
#      `browser` job does). Folding dist into operator_js_bytes anchored the
#      ceiling to a dist-inclusive payload while the environment doing the
#      asserting measured a dist-exclusive one — so in CI the ceiling sat ~88%
#      above the number compared against it and could not fail. That is the same
#      fail-open shape as the quantized heap budget this file already guards
#      against, so build outputs now carry their own ceiling and their absence is
#      reported rather than absorbed (see _build_output_files / _static_payload).
BUDGETS = {
    # Tracked zero-build sources. LF-normalised, so identical in every checkout
    # and the ceiling means the same thing locally and in CI.
    "operator_js_bytes": 449_000,  # app.js + js/*.js — measured 415,634 B
    "operator_css_bytes": 107_000,  # styles.css + css/tokens.css — measured 98,260 B
    "widget_js_bytes": 28_500,  # widget-app.js + js/widget-*.js — measured 26,218 B
    # Build output: the Vite React runtime under app/static/dist/assets/, minus
    # the on-demand terminal chunk. Only measurable where the frontend was built.
    "operator_dist_bytes": 400_000,  # dist/assets/*.{js,css} — measured 343,450 B
}

# A static budget may not drift far above the payload it measures: a ceiling that
# sits well clear of the current bytes can no longer fail, which is the shape that
# let a 15 MB heap budget report green for four nights (see HEAP_SESSION_ARGS).
# Raising a ceiling has to stay a bounded, deliberate act rather than a reflexive
# one; §43.6 anchored the original budgets at ~25% headroom over the baseline, so
# that is the widest gap allowed here too. Only *measured* components are checked:
# a component this environment could not measure reports itself as unenforced
# instead of passing on a size that is small for the wrong reason.
MAX_STATIC_HEADROOM = 1.25

# Browser budgets (§43.6: LCP/INP/CLS、长任务、内存和 10k 队列渲染).
BROWSER_BUDGETS = {
    "lcp_ms": 2500,  # Largest Contentful Paint on a clean load (web)
    "fcp_ms": 1800,  # First Contentful Paint — first pixel rendered (web)
    "cls": 0.10,  # Cumulative Layout Shift (web)
    "fid_ms": 100,  # First Input Delay — latency of first user interaction (web)
    "tti_ms": 3800,  # Time to Interactive — page fully interactive (web)
    "long_task_count_30s": 50,  # tasks > 50 ms in the first 30 s idle window
    "queue_10k_render_ms": 2000,  # first windowed render of 10k rows (legacy)
    "queue_10k_island_render_ms": 500,  # React island windowed render (§D3/§124)
    "heap_growth_mb": 15.0,  # JS heap growth across 20 refresh cycles
    # INP (Interaction-to-Next-Paint) measured directly, not proxied through
    # long tasks (§43.6 residual: "INP 直接归因列为后续增强"). The probe
    # clicks a real queue row and records the event handler's processing
    # duration from PerformanceObserver('event'); the worst single
    # interaction of the load is asserted. P95-style tails are risk_categoryionally
    # out of scope for a synthetic probe — a single late click on a settled
    # page already indicates a main-thread regression.
    "inp_ms": 300,  # web: worst single row-click processing duration
    "inp_desktop_ms": 300,  # desktop: worst single island row-click (§D5, same envelope)
    "detail_leak_mb": 5.0,  # heap retained after 5 open/close detail cycles (§43.6)
    # Desktop (Tauri shell) budgets — §D5. Strictly tighter than the web
    # numbers: assets come from the local bundle, so the only variable is
    # our own render cost, not the network.
    "lcp_desktop_ms": 1000,  # §D5: desktop LCP, 1000 ms vs the web's 2500 ms
    "fcp_desktop_ms": 800,  # §D5: desktop FCP, tighter than web's 1800 ms
    "cls_desktop": 0.10,  # splash hand-off must not shift the workspace
    "fid_desktop_ms": 50,  # §D5: desktop FID, tighter than web's 100 ms
    "tti_desktop_ms": 2000,  # §D5: desktop TTI, tighter than web's 3800 ms
}

# The heap_growth_mb budget above says "20 refresh cycles"; the loop ran 5.
HEAP_REFRESH_CYCLES = 20
# Detail open/close cycles for the leak probe: each cycle opens the first
# queue row (already seeded for INP) and clears the selection; retained heap
# after the cycle should return to the same level. A render path that leaks
# (detached DOM nodes, orphaned listeners) shows up as a monotonic climb.
DETAIL_LEAK_CYCLES = 5

# The event-observer probe is installed in the measured page *before* any
# interaction so every handler on the click path is captured. The click is
# delivered through Playwright's trusted input pipeline (locator.click),
# which is what the Interaction Timings spec requires for an interaction to
# get an interactionId — programmatic element.click() carries no ID and
# would silently record nothing. Worst single interaction of the load is
# what INP reports at the 75th percentile in production, so the gate
# asserting it is stricter by construction; a mid-load spike will surface
# as a budget breach but never as a false pass.
_INP_PROBE_SCRIPT = """
() => {
  window.__inpProbe = [];
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      if (entry.interactionId > 0 && entry.processingEnd) {
        window.__inpProbe.push({ id: entry.interactionId, dur: Math.round(entry.processingEnd - entry.startTime) });
      }
    }
  }).observe({ type: 'event', durationThreshold: 0 });
}
"""

# Create a conversation through the app's own API so both measured contexts
# (web and desktop shell) start with a real, clickable queue row. The demo
# auth mode used by CI and local dev accepts keyless requests.
_SEED_SCRIPT = """
async (customerName) => {
  try {
    const response = await fetch('/api/review-cases', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        submitter_name: customerName,
        submitter_ref: 'PERF-SEED',
        channel: 'web',
      }),
    });
    return response.ok;
  } catch {
    return false;
  }
}
"""


def _ensure_queue_row(page: Any, submitter_name: str) -> None:
    """Guarantee a clickable queue row: seed via the API, then wait for a
    poll cycle to surface it. Both measured tracks render the same row
    classes (legacy queueRowHtml and the island's QueueRow), so one selector
    covers web and desktop shell."""
    page.evaluate(f"() => Promise.resolve(({_SEED_SCRIPT})({submitter_name!r}))")
    # The seeded conversation is visible only after a poll cycle (SSE push or
    # ~15 s fallback); asking for an explicit refresh makes it deterministic
    # and fast instead of racing the next cycle.
    page.evaluate("() => refreshAll({ silent: true, background: true, refreshDetail: false })")
    # Wait for a clickable row instead of a fixed settle so slow CI runners
    # do not race the first render.
    page.wait_for_selector(
        ".conversation-row button.conversation-item",
        timeout=30000,
    )


def _measure_interaction_inp(page: Any) -> float:
    """Click the first queue row via Playwright's trusted input; return the
    worst interaction's processing duration (INP's core signal).

    Returns 0.0 when the page exposes no queue row to click into — an empty
    queue leaves INP unmeasurable, which is exactly the situation the old
    axe/gate suite hit before the shell pass seeded data.
    """
    try:
        page.evaluate(_INP_PROBE_SCRIPT)
        with page.expect_response(
            lambda response: "/api/review-cases/" in response.url,
            timeout=10000,
        ):
            row = page.locator(".conversation-row button.conversation-item").first
            if row.count() == 0:
                return 0.0
            row.click()
        # The expect_response context manager already waited for the detail
        # fetch the click triggered; the handlers ran within the interaction
        # window, so the probe array is complete now.
        page.wait_for_timeout(200)
        entries = page.evaluate("() => window.__inpProbe || []")
        if not entries:
            return 0.0
        # The click emits pointerdown/pointerup/click events that share one
        # interactionId; INP defines the interaction's latency as its longest
        # event processing duration, so group and keep the max per ID.
        worst: dict[int, int] = {}
        for entry in entries:
            duration = int(entry["dur"] or 0)
            worst[entry["id"]] = max(worst.get(entry["id"], 0), duration)
        return float(max(worst.values()))
    except Exception as exc:
        print(f"inp probe warning: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0.0


# What Chromium reports from performance.memory.usedJSHeapSize when precise
# memory info is disabled — a fixed 10 MB, identical on every sample. Measured,
# not assumed: with --enable-precise-memory-info the same page reports ~940 KB.
HEAP_QUANTIZED_BYTES = 10_000_000
# The precise-memory flag disables some allocator optimizations, which
# measurably skews the wall-clock budgets (desktop LCP) measured alongside it,
# so it must not ride along in the timing session.
TIMING_SESSION_ARGS: list[str] = []
# Real heap numbers, measured in a session of their own. Unconditional on
# purpose: gating this behind an opt-in environment variable is exactly how the
# budget went unenforced — the flag defaulted off, the nightly job never set
# it, and a 15 MB budget that can never fail reported green for four nights.
HEAP_SESSION_ARGS = ["--enable-precise-memory-info"]
# The leak probe additionally forces GC between cycles, so a sample reflects
# live objects rather than V8's lazy idle collection.
LEAK_PROBE_ARGS = ["--enable-precise-memory-info", "--js-flags=--expose-gc"]


def _widget_payload_files(static_dir: Path) -> list[Path]:
    """Every file in the widget's shipped payload.

    The widget ships its own shell plus whatever it imports from ``js/``. The
    payload used to name ``widget-core.js`` explicitly, so a second widget module
    would have escaped the budget silently — the same fail-open shape as a budget
    that cannot fail. Glob the widget-side modules instead of listing them.
    """
    files = [static_dir / "widget-app.js"]
    files.extend(sorted(static_dir.glob("js/widget-*.js")))
    return files


def _lf_bytes(paths: list[Path]) -> int:
    """Bytes as shipped: EOL-normalised to LF.

    Git stores LF. Summing raw working-tree bytes made the same commit measure
    425,757 B in a CRLF checkout and 415,634 B in CI — a budget whose *measured*
    value moves with the checkout cannot be compared against a fixed ceiling. The
    normalisation lives here and only here, so every payload is counted one way.
    """
    total = 0
    for path in paths:
        total += len(path.read_bytes().replace(b"\r\n", b"\n"))
    return total


def _build_output_files(static_dir: Path) -> list[Path]:
    """Vite build outputs that ship on first paint (terminal island excluded).

    ``app/static/dist`` is gitignored, so it is absent wherever the frontend has
    not been built — which includes the CI ``quality`` job (only the ``browser``
    job builds it). Return ``[]`` there so the caller can *report* the unmeasured
    budget instead of quietly measuring a smaller payload.
    """
    dist_assets = static_dir / "dist" / "assets"
    if not dist_assets.is_dir():
        return []
    return [
        path
        for path in sorted(dist_assets.iterdir())
        if path.suffix in {".js", ".css"} and not path.stem.startswith("terminal")
    ]


def _static_payload() -> tuple[dict[str, int], list[str]]:
    """Measure the shipped first-paint byte sizes.

    Returns ``(sizes, unenforced)``. ``unenforced`` names the budgets whose
    payload could not be measured in this environment — the point is never to
    fold an unmeasured component into a size that makes a ceiling look enforced
    when it is not.

    D2 (§6.3) / D3: the Vite-produced React runtime plus the island chunks are
    first paint; the terminal island chunk (xterm.js, ~400 KB) is loaded only
    when the diagnostic drawer opens (Ctrl+`), so it is excluded — on-demand,
    not initial render.
    """
    static_dir = ROOT / "app" / "static"
    build_outputs = _build_output_files(static_dir)
    sizes = {
        "operator_js_bytes": _lf_bytes(
            [static_dir / "app.js", *sorted(static_dir.glob("js/*.js"))]
        ),
        "operator_css_bytes": _lf_bytes(
            [static_dir / "styles.css", static_dir / "css" / "tokens.css"]
        ),
        "widget_js_bytes": _lf_bytes(_widget_payload_files(static_dir)),
        "operator_dist_bytes": _lf_bytes(build_outputs),
    }
    unenforced: list[str] = []
    if not build_outputs:
        unenforced.append(
            "operator_dist_bytes: app/static/dist/assets is absent (the frontend has "
            f"not been built here), so the {BUDGETS['operator_dist_bytes']} byte "
            "React-runtime budget went unenforced"
        )
    return sizes, unenforced


def check_static_budgets() -> tuple[dict[str, int], list[str], list[str]]:
    """Static byte budgets, as ``(sizes, problems, unenforced)``.

    A budget whose payload this environment could not measure is returned in
    ``unenforced`` rather than compared against: comparing 0 bytes to a ceiling
    would report success for the wrong reason, which is how a budget stops
    carrying a signal.
    """
    sizes, unenforced = _static_payload()
    problems = []
    for key, limit in BUDGETS.items():
        if sizes[key] > limit:
            problems.append(
                f"{key}: {sizes[key]} bytes exceeds budget {limit} (over by {sizes[key] - limit})"
            )
    return sizes, problems, unenforced


def _launch_browser(playwright: Any, chrome_binary: str | None, args: list[str]) -> Any:
    """Launch headless Chromium with ``args``.

    Falls back to a local Chrome (``CHROME_EXECUTABLE``) and then to the msedge
    channel, for machines with no Playwright-managed browsers. Kept in one
    place because three sessions now launch with different argument sets —
    duplicating the fallback is how one of them silently loses it.
    """
    from playwright.sync_api import Error as PlaywrightError

    try:
        return playwright.chromium.launch(headless=True, args=args)
    except PlaywrightError:
        if chrome_binary:
            return playwright.chromium.launch(
                executable_path=chrome_binary, headless=True, args=args
            )
        return playwright.chromium.launch(channel="msedge", headless=True, args=args)


def heap_growth_problem(samples: list[int]) -> str | None:
    """Why the heap budget is unenforced, or ``None`` when it is measurable.

    Split out from the browser harness so the fail-closed states are testable
    without Chromium. A quantized or absent ``performance.memory`` must be
    *reported*, never silently read as "no growth" — that reading is what let a
    budget that cannot fail pass for four consecutive nightly runs.
    """
    budget = BROWSER_BUDGETS["heap_growth_mb"]
    if not samples or not all(s > 0 for s in samples):
        return (
            "heap growth unmeasurable: performance.memory is unavailable, so the "
            f"{budget} MB budget went unenforced"
        )
    if len(set(samples)) == 1 and samples[0] == HEAP_QUANTIZED_BYTES:
        return (
            "heap growth unmeasurable: every sample is the quantized constant "
            f"{HEAP_QUANTIZED_BYTES} — the heap session must be launched with "
            f"--enable-precise-memory-info or the {budget} MB budget cannot fail"
        )
    return None


def heap_growth_mb(samples: list[int]) -> float:
    """Growth across the refresh cycles: the settled tail against the first.

    Not ``max - min``: that measures jitter, and stays small for a heap that
    climbs monotonically and never comes back down.
    """
    return round((max(samples[-3:]) - samples[0]) / (1024 * 1024), 2)


def leak_retention_problem(samples: list[int]) -> str | None:
    """Why the detail-leak budget is unenforced, or ``None`` when measurable.

    Same fail-closed rule as :func:`heap_growth_problem`, and for the same
    reason: the probe used to record a number only when every sample was
    positive, so an unreadable (or quantized) heap produced silence rather than
    a problem — the 5 MB retention budget went unenforced with no signal. The
    quantized check matters too: every sample equal to the 10 MB constant would
    otherwise read as a clean 0 MB leak, i.e. a second budget that cannot fail.
    """
    budget = BROWSER_BUDGETS["detail_leak_mb"]
    if not samples or not all(s > 0 for s in samples):
        return (
            "detail leak probe: performance.memory was unreadable across the cycles, "
            f"so the {budget} MB budget went unenforced"
        )
    if len(set(samples)) == 1 and samples[0] == HEAP_QUANTIZED_BYTES:
        return (
            "detail leak probe: every sample is the quantized constant "
            f"{HEAP_QUANTIZED_BYTES}, so the {budget} MB budget cannot fail"
        )
    return None


def check_browser_budgets(base_url: str) -> tuple[dict[str, float | None], list[str]]:
    """Run the Playwright measurements against a live server."""
    from playwright.sync_api import sync_playwright

    metrics_script = """
    async () => {
      // FCP: First Contentful Paint from paint timing
      const fcpEntry = performance.getEntriesByType('paint')
        .find(entry => entry.name === 'first-contentful-paint');
      const fcpMs = fcpEntry ? Math.round(fcpEntry.startTime) : 0;

      // LCP: Largest Contentful Paint
      const lcpPromise = new Promise((resolve) => {
        let value = 0;
        new PerformanceObserver((entries) => {
          for (const entry of entries.getEntries()) {
            if (entry.startTime > value) value = entry.startTime;
          }
          resolve(value);
        }).observe({ type: 'largest-contentful-paint', buffered: true });
      });

      // CLS: Cumulative Layout Shift
      const clsValue = await new Promise((resolve) => {
        let cls = 0;
        new PerformanceObserver((entries) => {
          for (const entry of entries.getEntries()) {
            if (!entry.hadRecentInput) cls += entry.value;
          }
          resolve(cls);
        }).observe({ type: 'layout-shift', buffered: true });
        setTimeout(() => resolve(cls), 1500);
      });

      // FID: First Input Delay (will be 0 if no interaction yet)
      const fidEntry = performance.getEntriesByType('first-input')[0];
      const fidMs = fidEntry ? Math.round(fidEntry.processingStart - fidEntry.startTime) : 0;

      // TTI: Time to Interactive approximation
      // Use the time when there are no long tasks for 5 seconds after FCP
      const ttiMs = await new Promise((resolve) => {
        const longTasks = [];
        const observer = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            longTasks.push(entry.startTime + entry.duration);
          }
        });
        observer.observe({ type: 'longtask', buffered: true });

        setTimeout(() => {
          observer.disconnect();
          // TTI is the end of the last long task, or FCP if no long tasks
          const lastTaskEnd = longTasks.length > 0 ? Math.max(...longTasks) : fcpMs;
          resolve(Math.round(Math.max(lastTaskEnd, fcpMs)));
        }, 5000);
      });

      const lcpMs = await Promise.race([lcpPromise, new Promise((r) => setTimeout(() => r(0), 3000))]);
      return {
        fcp_ms: fcpMs,
        lcp_ms: Math.round(lcpMs),
        cls: Number(clsValue.toFixed(4)),
        fid_ms: fidMs,
        tti_ms: ttiMs
      };
    }
    """

    long_tasks_script = """
    () => new Promise((resolve) => {
      const durations = [];
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) durations.push(entry.duration);
      });
      observer.observe({ type: 'longtask', buffered: true });
      setTimeout(() => { observer.disconnect(); resolve(durations.length); }, 5000);
    })
    """

    problems: list[str] = []
    metrics: dict[str, float | None] = {}
    # The timing session carries no flags: --enable-precise-memory-info and
    # --expose-gc both perturb what is measured here (--expose-gc measurably
    # raises desktop LCP ~50 ms), so the heap measurements do not share it.
    with sync_playwright() as playwright:
        chrome_binary = os.environ.get("CHROME_EXECUTABLE")
        browser = _launch_browser(playwright, chrome_binary, TIMING_SESSION_ARGS)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # networkidle never fires on the operator console: the queue SSE
        # stream (/api/events/queue, 45 s hold) keeps a connection open by
        # design. domcontentloaded + a fixed settle window matches what
        # tests/ui_smoke.py does.
        page.goto(base_url, wait_until="domcontentloaded")
        page.wait_for_selector("#conversationList[aria-busy='false']", timeout=30000)
        page.wait_for_timeout(1500)
        paint = page.evaluate(metrics_script)
        metrics.update(paint)

        long_tasks = page.evaluate(long_tasks_script)
        metrics["long_task_count_30s"] = long_tasks

        # --- 10k-row queue render -------------------------------------
        # Inject synthetic review_cases straight into the legacy state and
        # time one full windowed render (the virtualized path above
        # VIRTUAL_THRESHOLD=200 rows).
        render_ms = page.evaluate(
            """
            () => {
              const total = 10000;
              const now = new Date().toISOString();
              state.review_cases = Array.from({ length: total }, (_, i) => ({
                id: `perf_${i}`,
                submitter_name: `压测提交方 ${i}`,
                status: i % 4 === 0 ? 'waiting_human' : 'open',
                channel: ['web', 'email', 'chat'][i % 3],
                preview: '预算门合成审核单，仅用于渲染测量。',
                labels: [],
                updated_at: now,
                version: 1,
                sla_due_at: null,
              }));
              state.queueHasMore = false;
              const start = performance.now();
              renderQueue();
              const elapsed = performance.now() - start;
              // Restore real data on the next poll; keep the DOM honest.
              state.review_cases = [];
              // Re-render the emptied state: without it the synthetic rows
              // stay in the DOM and the subsequent INP probe would click a
              // fake `perf_*` id and 404 on the detail fetch.
              renderQueue();
              return Math.round(elapsed);
            }
            """
        )
        metrics["queue_10k_render_ms"] = render_ms
        # Paint and the 10k render are measured first: seeding a conversation
        # after the measurement keeps cls/lcp on the empty-queue load, while
        # the INP probe needs a real, clickable row to interact with.
        _ensure_queue_row(page, "INP 探针提交方")
        metrics["inp_ms"] = _measure_interaction_inp(page)

        # --- desktop shell (Tauri) measurements -------------------------
        # The desktop shell mounts the React islands and skips the legacy
        # renderers, so its paint path is a separate code path from the web
        # measurement above — measuring only the web context would leave the
        # shipped desktop experience ungated. Spin up a second context with
        # the shell preconditions (__TAURI_INTERNALS__ + backend-ready).
        #
        # §D5 budgets are more aggressive than the web ones on purpose: the
        # shell serves assets from the local bundle (no network round trip)
        # and owns sidecar startup, so LCP is budgeted at 1000 ms against the
        # web's 2500 ms.
        island_render_ms: float | None = None
        try:
            shell_context = browser.new_context(viewport={"width": 1440, "height": 900})
            shell_page = shell_context.new_page()
            shell_page.add_init_script(
                "window.__TAURI_INTERNALS__ = { invoke: () => Promise.resolve() };"
            )
            shell_page.goto(base_url, wait_until="domcontentloaded")
            shell_page.evaluate("() => window.dispatchEvent(new Event('helix-backend-ready'))")
            # The island may render the empty state (no review_cases) —
            # any React content proves the mount, so wait on :not(:empty).
            shell_page.wait_for_selector("#queueReactIsland:not(:empty)", timeout=30000)
            shell_page.wait_for_timeout(1500)
            shell_paint = shell_page.evaluate(metrics_script)
            # The desktop shell reuses the same metrics_script as the web
            # pass; map every paint key to its §D5-budgeted desktop twin so
            # none of the desktop budgets can silently go unassigned.
            desktop_key_map = {
                "fcp_ms": "fcp_desktop_ms",
                "lcp_ms": "lcp_desktop_ms",
                "cls": "cls_desktop",
                "fid_ms": "fid_desktop_ms",
                "tti_ms": "tti_desktop_ms",
            }
            for source_key, target_key in desktop_key_map.items():
                metrics[target_key] = shell_paint[source_key]
            _ensure_queue_row(shell_page, "INP 岛模式探针提交方")
            metrics["inp_desktop_ms"] = _measure_interaction_inp(shell_page)

            # 10k-row island render: the queue's windowed path through React
            # is independent of the legacy renderQueue() measured above.
            # Budget: 500 ms (§D3/§124 once React owns the queue).
            island_render_ms = shell_page.evaluate(
                """
                () => new Promise((resolve) => {
                  const total = 10000;
                  const now = new Date().toISOString();
                  const review_cases = Array.from({ length: total }, (_, i) => ({
                    id: `perf_island_${i}`,
                    submitter_name: `压测提交方 ${i}`,
                    status: i % 4 === 0 ? 'waiting_human' : 'open',
                    channel: ['web', 'email', 'chat'][i % 3],
                    preview: '预算门合成审核单，仅用于渲染测量。',
                    labels: [],
                    updated_at: now,
                    version: 1,
                    sla_due_at: null,
                  }));
                  const started = performance.now();
                  window.dispatchEvent(new CustomEvent('helix-review_cases-updated', {
                    detail: {
                      review_cases,
                      selectedId: null,
                      bulkSelected: [],
                      canOperate: true,
                      compact: false,
                    },
                  }));
                  // React renders asynchronously; wait one frame for the
                  // initial windowed list to land, then measure.
                  requestAnimationFrame(() => requestAnimationFrame(() => resolve(Math.round(performance.now() - started))));
                })
                """
            )
            shell_context.close()
        except Exception as exc:
            # This try spans the whole desktop measurement: shell context,
            # lcp_desktop_ms, cls_desktop and the island render. Swallowing it
            # left four of eight budgets unset, and the assertion loop below
            # skips unset keys — so a 60-second island render passed against
            # the 500 ms budget with no warning and no trace in the report.
            # A measurement that was supposed to happen and did not is a gate
            # failure, not an absent optional metric.
            problems.append(
                f"desktop (Tauri shell) measurement failed, leaving the §D5 budgets "
                f"unenforced: {type(exc).__name__}: {exc}"
            )
        metrics["queue_10k_island_render_ms"] = island_render_ms

        browser.close()

        # --- heap growth across refresh cycles (dedicated session) --------
        # Chromium quantizes performance.memory for privacy unless launched
        # with --enable-precise-memory-info: usedJSHeapSize returns a constant
        # 10,000,000 regardless of real usage. That constant is > 0, so the old
        # `all(s > 0)` guard passed, every sample was identical, and the growth
        # read as a clean 0.0 MB — a budget that could never be exceeded. The
        # flag skews wall-clock timings, so the samples are taken here rather
        # than in the timing session that just closed.
        await_refresh = """
        () => new Promise((done) => {
          refreshAll({ silent: true, background: true, refreshDetail: false })
            .then(() => done(performance.memory ? performance.memory.usedJSHeapSize : 0));
        })
        """
        heap_samples: list[int] = []
        try:
            heap_browser = _launch_browser(playwright, chrome_binary, HEAP_SESSION_ARGS)
            heap_context = heap_browser.new_context(viewport={"width": 1440, "height": 900})
            heap_page = heap_context.new_page()
            heap_page.goto(base_url, wait_until="domcontentloaded")
            heap_page.wait_for_selector("#conversationList[aria-busy='false']", timeout=60000)
            # Queue settle before the first sample, so the baseline is a
            # rendered page rather than one still filling in.
            heap_page.wait_for_timeout(800)
            for _ in range(HEAP_REFRESH_CYCLES):
                heap_samples.append(heap_page.evaluate(await_refresh) or 0)
            heap_context.close()
            heap_browser.close()
        except Exception as exc:
            # A measurement that was supposed to happen and did not is a gate
            # failure, not an absent optional metric.
            problems.append(
                f"heap session failed, leaving the "
                f"{BROWSER_BUDGETS['heap_growth_mb']} MB budget unenforced: "
                f"{type(exc).__name__}: {exc}"
            )
            heap_samples = []

        heap_problem = heap_growth_problem(heap_samples)
        if heap_problem:
            problems.append(heap_problem)
        else:
            metrics["heap_growth_mb"] = heap_growth_mb(heap_samples)

        # --- detail open/close leak probe (separate session) -------------
        # A detail render that retains DOM nodes or listeners across
        # clearSelection would climb monotonically. The probe runs in its own
        # browser session with --expose-gc so the forced GC that makes the
        # measurement meaningful never pollutes the timing budgets measured in
        # the timing session (--expose-gc measurably raises desktop LCP). It is
        # meaningful only with real heap numbers — the quantized constant would
        # read as a clean 0 MB — which is why the session carries the precise
        # flag. The queue row seeded for the INP probe is the click target;
        # selectConversation/
        # clearSelection are app.js thin wrappers over the conversation-detail
        # module.
        try:
            leak_browser = _launch_browser(playwright, chrome_binary, LEAK_PROBE_ARGS)
            leak_context = leak_browser.new_context(viewport={"width": 1440, "height": 900})
            leak_page = leak_context.new_page()
            leak_page.goto(base_url, wait_until="domcontentloaded")
            leak_page.wait_for_selector("#conversationList[aria-busy='false']", timeout=60000)
            # Queue render settle before the first cycle.
            leak_page.wait_for_timeout(800)
            leak_script = (
                """
            async (id) => {
              const forceGc = () => { if (window.gc) { window.gc(); window.gc(); } };
              const heap = () => performance.memory ? performance.memory.usedJSHeapSize : 0;
              const samples = [];
              for (let i = 0; i < %d; i++) {
                await selectConversation(id);
                // Detail fetch + render settle.
                await new Promise((r) => setTimeout(r, 400));
                clearSelection();
                // Give the cleared view a beat to detach, then force GC so
                // the sample reflects live objects, not V8's lazy idle
                // collection.
                await new Promise((r) => setTimeout(r, 300));
                forceGc();
                await new Promise((r) => setTimeout(r, 100));
                // Sample the *settled* baseline after close, not the open
                // peak: the budget is "no retention after close", and an
                // open-time climb would be dominated by legitimately live
                // detail data rather than leaked nodes.
                samples.push(heap());
              }
              return samples;
            }
            """
                % DETAIL_LEAK_CYCLES
            )
            row_id = leak_page.evaluate(
                "() => document.querySelector('.conversation-row button.conversation-item')"
                "?.dataset?.id || null"
            )
            if row_id:
                leak_samples = leak_page.evaluate(leak_script, row_id) or []
                leak_problem = leak_retention_problem(leak_samples)
                if leak_problem:
                    problems.append(leak_problem)
                else:
                    growth = leak_samples[-1] - leak_samples[0]
                    metrics["detail_leak_mb"] = round(growth / (1024 * 1024), 2)
            else:
                problems.append(
                    "detail leak probe: no queue row was clickable, so the "
                    f"{BROWSER_BUDGETS['detail_leak_mb']} MB budget went unenforced"
                )
            leak_context.close()
            leak_browser.close()
        except Exception as exc:
            problems.append(f"detail leak probe failed: {type(exc).__name__}: {exc}")

    for key, limit in BROWSER_BUDGETS.items():
        if key not in metrics or metrics[key] is None:
            continue  # measurement unavailable (e.g. no performance.memory)
        if metrics[key] > limit:
            problems.append(f"{key}: {metrics[key]} exceeds budget {limit}")
    for key in ("inp_ms", "inp_desktop_ms"):
        if metrics.get(key) == 0.0:
            problems.append(
                f"{key}: no queue row was clickable, so the Interaction-to-Next-Paint "
                "budget went unenforced — seed the queue before measuring"
            )
    return metrics, problems


def write_baseline(payload: dict[str, object]) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="live server to measure (enables browser layer)")
    parser.add_argument("--update", action="store_true", help="write baseline JSON")
    args = parser.parse_args(argv)

    problems: list[str] = []
    sizes, static_problems, unenforced = check_static_budgets()
    problems.extend(static_problems)
    report: dict[str, object] = {
        "static": sizes,
        "budgets": BUDGETS,
        "unenforced": unenforced,
    }
    print(json.dumps(report["static"], indent=2))
    # Named, not swallowed: a budget nobody measured is reported on every run so
    # it cannot pass for a budget that was measured and held.
    for notice in unenforced:
        print(f"NOTE: {notice}", file=sys.stderr)

    if args.base_url:
        metrics, browser_problems = check_browser_budgets(args.base_url)
        problems.extend(browser_problems)
        report["browser"] = metrics
        report["browser_budgets"] = BROWSER_BUDGETS
        print(json.dumps(metrics, indent=2))

    if args.update:
        write_baseline(report)
        print(f"baseline written: {BASELINE}")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    scope = "static+browser" if args.base_url else "static"
    print(f"performance gate passed ({scope})")
    return 0


if __name__ == "__main__":
    use_utf8_console()
    sys.exit(main())
