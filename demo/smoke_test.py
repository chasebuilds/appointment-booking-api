"""Headless smoke test: list slots, book one, check it disappears and a second booking is refused.

Usage: python demo/smoke_test.py [--url URL] [--screenshot PATH]
"""

from __future__ import annotations

import argparse
import functools
import http.server
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import build  # noqa: E402


def serve(folder: Path) -> str:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_port}/"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--screenshot")
    args = ap.parse_args()
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.goto(args.url or serve(build()))
        ready = "['ok', 'bad'].includes(document.querySelector('#status').dataset.kind)"
        page.wait_for_function(ready, timeout=180_000)
        print("status:", page.inner_text("#status"))
        if page.get_attribute("body", "data-ready") != "1":
            print("FAIL: runtime did not load")
            return 1
        page.select_option("#staff", label="Sam (London)")
        first = page.locator(".slot").first
        utc = first.get_attribute("data-utc")
        before = page.locator(".slot").count()
        first.click()
        booked = page.inner_text("#result")
        after = page.locator(".slot").count()
        gone = page.locator(f".slot[data-utc='{utc}']").count() == 0
        page.click("#again")
        again = page.inner_text("#result")
        rows = page.locator("#bookings tbody tr").count()
        print(f"slots {before} -> {after}\n{booked}\n{again}\nbookings: {rows}")
        if before == 0:
            problems.append("no open slots")
        if "201 Created" not in booked:
            problems.append("first booking not created")
        if not gone or after >= before:
            problems.append("booked slot still listed")
        if "409 refused" not in again:
            problems.append("double booking was not refused with 409")
        if rows != 1:
            problems.append(f"expected 1 booking, found {rows}")
        if args.screenshot:
            page.locator("main").screenshot(path=args.screenshot)
        browser.close()
    for msg in problems:
        print(f"FAIL: {msg}")
    print("PASS" if not problems else "FAILED")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
