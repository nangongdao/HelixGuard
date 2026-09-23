"""Desktop-shell real-machine verification for the review case dialog island (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port
and drives the new-review-case dialog end-to-end through the island: the
legacy 新建 button opens the island dialog, a submit bridges the payload to
legacy createConversation (a real POST), and the island closes on success
with the created review case selected in the inspector.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright

EXE = Path(__file__).resolve().parents[1] / "src-tauri" / "target" / "release" / "helix-desktop.exe"
DEBUG_PORT = 9339


def wait_for_cdp(deadline_s: float = 90.0) -> list[dict]:
    """Wait until the WebView2 exposes a sidecar-origin (127.0.0.1) page."""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{DEBUG_PORT}/json/list", timeout=2
            ) as res:
                targets = json.loads(res.read().decode("utf-8"))
            pages = [
                t
                for t in targets
                if t.get("type") == "page" and "127.0.0.1" in (t.get("url") or "")
            ]
            if pages:
                return targets
        except Exception:
            pass
        time.sleep(1.0)
    raise RuntimeError("WebView2 CDP endpoint never exposed a sidecar-origin page")


def main() -> int:
    if not EXE.exists():
        print(f"FAIL: release exe missing at {EXE}")
        return 1
    env = dict(os.environ)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={DEBUG_PORT}"
    proc = subprocess.Popen([str(EXE)], env=env, cwd=str(EXE.parent))
    try:
        wait_for_cdp()
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
            page = None
            deadline = time.time() + 60
            while page is None and time.time() < deadline:
                for context in browser.contexts:
                    for candidate in context.pages:
                        if "127.0.0.1" in candidate.url:
                            page = candidate
                            break
                    if page:
                        break
                if page is None:
                    time.sleep(1.0)
            if page is None:
                print("FAIL: no console page found over CDP")
                return 1

            # The legacy span is yielded, so wait for attachment not visibility;
            # rows live in the island list in island mode.
            page.wait_for_selector("#reviewerIdentity", state="attached", timeout=30000)
            page.wait_for_selector(
                "#queueReactIsland .review-case-item", state="attached", timeout=30000
            )
            checks: dict[str, object] = {}
            checks["island_mode"] = page.evaluate("() => window.__HELIX_ISLAND_MODE__ === true")
            checks["legacy_dialog_hidden"] = page.evaluate(
                "() => { const d = document.querySelector('#newReviewCaseDialog');"
                " return d && d.hidden === true; }"
            )
            checks["island_dialog_present"] = page.evaluate(
                "() => Boolean(document.querySelector('#reviewCaseDialogReactIsland dialog.dialog'))"
            )

            # Open the dialog via the legacy header button (island branch).
            page.locator("#newReviewCase").click()
            page.wait_for_function(
                "() => document.querySelector('#reviewCaseDialogReactIsland dialog')?.open === true",
                timeout=10000,
            )
            checks["island_dialog_opens"] = True
            checks["name_field_focused"] = page.evaluate(
                "() => document.activeElement?.id === 'newSubmitterNameReact'"
            )

            # Fill and submit — a real POST through the create bridge.
            run_id = uuid4().hex[:6]
            submitter = f"对话框验证 {run_id}"
            page.fill("#newSubmitterNameReact", submitter)
            page.select_option("#newChannelReact", "messaging")
            with page.expect_response(
                lambda r: r.url.endswith("/api/review-cases") and r.request.method == "POST"
            ) as created_info:
                page.locator("#newReviewCaseFormReact button[type='submit']").click()
            checks["post_status"] = created_info.value.status
            checks["dialog_closes_on_success"] = (
                page.wait_for_function(
                    "() => document.querySelector('#reviewCaseDialogReactIsland dialog')?.open === false",
                    timeout=10000,
                )
                is not None
            )
            # The created review case is prepended to the island queue and
            # selected (is-selected row) by the create lifecycle.
            page.wait_for_selector(
                f"#queueReactIsland .review-case-item:has-text('{submitter}')",
                timeout=20000,
            )
            selected_row = page.locator(
                "#queueReactIsland .review-case-item", has_text=submitter
            ).first
            checks["created_in_queue_selected"] = "is-active" in (
                selected_row.get_attribute("class") or ""
            )

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop review case dialog island verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
