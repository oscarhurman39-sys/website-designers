"""Website preview screenshots via Playwright (headless Chromium).

Used by design_agent.py right after a successful Vercel deployment, and by
sales_agent.py to embed the result as an inline image in the cold email.
Screenshots are cached on disk keyed by lead_id: capture never re-runs for
a lead that already has a screenshot file, so a retried or re-run design
step doesn't burn a browser launch (or produce a different image) every
time.

Two public entrypoints, same underlying capture logic:
  - `capture_screenshot_sync()` -- what design_agent.py actually calls.
    Required: design_agent.process_lead() runs inside
    tracer.run_traced()'s asyncio.run(), and Playwright's sync API refuses
    to launch on a thread with an active event loop (confirmed by hitting
    that exact TargetClosedError in testing) -- this dispatches the real
    Playwright work to a plain worker thread to sidestep that.
  - `capture_screenshot()` -- a real `async def`, for any async caller.
    Internally just awaits the same worker-thread dispatch via
    asyncio.to_thread, so it's safe to call from inside an event loop too.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent / "screenshots"

# Pre-installed headless Chromium in this environment; set only when the
# expected binary actually exists so this also works unmodified on a
# regular machine where Playwright manages its own browser install
# (`playwright install chromium`) instead.
_PINNED_CHROMIUM_PATH = Path("/opt/pw-browsers/chromium")

_VIEWPORT = {"width": 1280, "height": 800}
_PAGE_LOAD_TIMEOUT_MS = 30_000


def screenshot_path(lead_id: int) -> Path:
    """Where a given lead's screenshot lives (whether or not it exists yet)."""
    return SCREENSHOTS_DIR / f"{lead_id}.png"


def capture_screenshot_sync(preview_url: str, lead_id: int) -> Path:
    """Capture a full-page screenshot of `preview_url` and save it to
    pipeline/screenshots/{lead_id}.png. Cached: if that file already
    exists, returns it immediately without launching a browser.

    Raises on failure (e.g. the preview isn't reachable yet, or no
    Chromium is available) -- callers that consider a screenshot optional
    (design_agent.py does) should catch and log rather than let this stop
    an otherwise-successful deployment.
    """
    out_path = screenshot_path(lead_id)
    if out_path.exists():
        return out_path

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # See module docstring: dispatched to a plain worker thread so this
    # works whether the caller is inside an active asyncio event loop or
    # not, without the caller needing to know or care which.
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(_capture_sync, preview_url, out_path).result()

    return out_path


async def capture_screenshot(preview_url: str, lead_id: int) -> str:
    """Async entrypoint wrapping capture_screenshot_sync(). Returns the
    local file path as a string. Safe to call from within a running event
    loop (asyncio.to_thread hands the actual Playwright work to a plain
    worker thread, same as the sync entrypoint does internally)."""
    path = await asyncio.to_thread(capture_screenshot_sync, preview_url, lead_id)
    return str(path)


def _capture_sync(preview_url: str, out_path: Path) -> None:
    launch_kwargs: dict = {"headless": True}
    if _PINNED_CHROMIUM_PATH.exists():
        launch_kwargs["executable_path"] = str(_PINNED_CHROMIUM_PATH)

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        try:
            page = browser.new_page(viewport=_VIEWPORT)
            # "load" rather than "networkidle": some templates load fonts/
            # Tailwind's CDN script, and networkidle can hang waiting for
            # long-polling/keep-alive connections that never go idle. A
            # short fixed wait after "load" covers Tailwind's synchronous
            # style injection without risking a near-timeout-length hang.
            page.goto(preview_url, wait_until="load", timeout=_PAGE_LOAD_TIMEOUT_MS)
            page.wait_for_timeout(500)
            # Never screenshot a login/protection page silently: Vercel's
            # deployment protection serves an auth screen on protected URLs,
            # and embedding a picture of "Log in to Vercel" in a cold email
            # is worse than no image. Detect it and fail loudly; the caller
            # (design_agent) treats screenshots as optional and logs this.
            title = (page.title() or "").lower()
            if "vercel" in title and ("log in" in title or "login" in title or "authentication" in title):
                raise RuntimeError(
                    f"Page at {preview_url} is a Vercel login/protection screen (title: {page.title()!r}). "
                    "Use the public production alias, or disable the project's Deployment Protection."
                )
            # Every generated template has a #contact section; the Vercel
            # auth page (and any other interstitial) doesn't. Waiting for it
            # both confirms we're on the real site and gives late-rendering
            # pages a moment to finish.
            try:
                page.wait_for_selector("#contact", timeout=10_000)
            except PlaywrightError as exc:
                raise RuntimeError(
                    f"Page at {preview_url} doesn't look like a generated preview site "
                    "(no #contact section) -- refusing to screenshot it."
                ) from exc
            # Write to a temp path first and rename, so a crash mid-capture
            # never leaves a corrupt/partial file behind masquerading as a
            # valid cache hit on the next call.
            tmp_path = out_path.with_suffix(".png.tmp")
            # type="png" explicitly: Playwright infers screenshot format
            # from the path's extension otherwise, and won't recognize the
            # ".png.tmp" temp suffix used here to avoid a partial/corrupt
            # file ever masquerading as a valid cache hit.
            page.screenshot(path=str(tmp_path), full_page=True, type="png")
            tmp_path.replace(out_path)
        finally:
            browser.close()


def get_cached_screenshot(lead_id: int) -> Optional[Path]:
    """Return the screenshot path for `lead_id` if one has already been
    captured, else None. Never triggers a new capture -- used by
    sales_agent.py, which should only embed a screenshot that's already
    there, not block/slow down sending to take one."""
    path = screenshot_path(lead_id)
    return path if path.exists() else None


__all__ = [
    "capture_screenshot",
    "capture_screenshot_sync",
    "get_cached_screenshot",
    "screenshot_path",
    "PlaywrightError",
]
