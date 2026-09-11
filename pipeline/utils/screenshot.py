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

import config

SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent / "screenshots"

# Pre-installed headless Chromium in this environment; set only when the
# expected binary actually exists so this also works unmodified on a
# regular machine where Playwright manages its own browser install
# (`playwright install chromium`) instead.
_PINNED_CHROMIUM_PATH = Path("/opt/pw-browsers/chromium")

_VIEWPORT = {"width": 1280, "height": 800}
_PAGE_LOAD_TIMEOUT_MS = 30_000

# Markers Vercel's own deployment-protection interstitial ("Vercel
# Authentication" / password wall) renders instead of the actual site.
# Checked case-insensitively against the page title + body text so a
# blocked preview is never screenshotted and emailed to a lead.
_VERCEL_LOGIN_WALL_MARKERS = ("vercel authentication", "log in to vercel")
_LOGIN_WALL_RETRY_DELAY_MS = 3_000


def screenshot_path(lead_id: int) -> Path:
    """Where a given lead's screenshot lives (whether or not it exists yet)."""
    return SCREENSHOTS_DIR / f"{lead_id}.png"


def capture_screenshot_sync(preview_url: str, lead_id: int, fresh: bool = False) -> Path:
    """Capture a full-page screenshot of `preview_url` and save it to
    pipeline/screenshots/{lead_id}.png. Cached: if that file already
    exists, returns it immediately without launching a browser -- unless
    `fresh` is true, which drops the cached file first.

    A NEW build must pass fresh=True. The cache is keyed by lead id only,
    and after the 2026-09-07 database reset restarted ids at 1, leads 11-56
    silently inherited July test images (a Vercel login page, another
    business's site); eight cold emails went out carrying them.

    Raises on failure (e.g. the preview isn't reachable yet, no Chromium,
    or the page is a Vercel login wall) -- callers that consider a
    screenshot optional (design_agent.py does) should catch and log rather
    than let this stop an otherwise-successful deployment.
    """
    out_path = screenshot_path(lead_id)
    if fresh:
        invalidate(lead_id)
    elif out_path.exists():
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


# Contact map: Google's embed paints blank in headless Chromium, so every
# cold-email screenshot used to carry a white box. templates/modern ships a
# map-styled placeholder that only shows while <html data-screenshot> is set.
SCREENSHOT_MODE_JS = "document.documentElement.setAttribute('data-screenshot', '1')"


def mark_for_screenshot(page) -> None:
    """Swap capture-hostile elements (the live map) for their placeholders."""
    page.evaluate(SCREENSHOT_MODE_JS)


def _looks_like_vercel_login_wall(page) -> bool:
    haystack = f"{page.title() or ''} {page.content() or ''}".lower()
    return any(marker in haystack for marker in _VERCEL_LOGIN_WALL_MARKERS)


def _capture_sync(preview_url: str, out_path: Path) -> None:
    launch_kwargs: dict = {"headless": True}
    if _PINNED_CHROMIUM_PATH.exists():
        launch_kwargs["executable_path"] = str(_PINNED_CHROMIUM_PATH)

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        try:
            page = browser.new_page(viewport=_VIEWPORT)
            # Secondary defense against Vercel's login wall, on top of
            # vercel_api.py disabling deployment protection outright: if a
            # protection setting hasn't propagated yet (or fails to
            # disable), this header tells Vercel to bypass it anyway.
            # No-op (empty dict) when VERCEL_BYPASS_TOKEN isn't configured.
            if config.VERCEL_BYPASS_TOKEN:
                page.set_extra_http_headers({"x-vercel-protection-bypass": config.VERCEL_BYPASS_TOKEN})
            # "load" rather than "networkidle": some templates load fonts/
            # Tailwind's CDN script, and networkidle can hang waiting for
            # long-polling/keep-alive connections that never go idle. A
            # short fixed wait after "load" covers Tailwind's synchronous
            # style injection without risking a near-timeout-length hang.
            page.goto(preview_url, wait_until="load", timeout=_PAGE_LOAD_TIMEOUT_MS)
            page.wait_for_timeout(500)

            # A freshly-deployed Vercel project can serve its own
            # "Vercel Authentication" login wall instead of the site (see
            # vercel_api.py's _disable_deployment_protection). Retry once
            # after a short delay in case the settings change just hadn't
            # propagated yet, so a login screen never gets screenshotted
            # and emailed to a lead as their "website preview".
            if _looks_like_vercel_login_wall(page):
                page.wait_for_timeout(_LOGIN_WALL_RETRY_DELAY_MS)
                page.goto(preview_url, wait_until="load", timeout=_PAGE_LOAD_TIMEOUT_MS)
                page.wait_for_timeout(500)
                if _looks_like_vercel_login_wall(page):
                    # Raise rather than save: a cached login page would be
                    # embedded in the cold email as the lead's "preview".
                    # Without an image the email is sent plain-text, which
                    # is the lesser harm.
                    raise RuntimeError(
                        f"{preview_url} is still showing a Vercel login wall after one retry -- "
                        "deployment protection is likely still enabled for this project "
                        "(Vercel -> project -> Settings -> Deployment Protection). Not saving "
                        "a login page as the preview screenshot."
                    )

            # Write to a temp path first and rename, so a crash mid-capture
            # never leaves a corrupt/partial file behind masquerading as a
            # valid cache hit on the next call.
            tmp_path = out_path.with_suffix(".png.tmp")
            # type="png" explicitly: Playwright infers screenshot format
            # from the path's extension otherwise, and won't recognize the
            # ".png.tmp" temp suffix used here to avoid a partial/corrupt
            # file ever masquerading as a valid cache hit.
            mark_for_screenshot(page)
            page.wait_for_timeout(100)
            page.screenshot(path=str(tmp_path), full_page=True, type="png")
            tmp_path.replace(out_path)
        finally:
            browser.close()


def invalidate(lead_id: int) -> None:
    """Drop the cached screenshot so the next capture reflects a rebuild."""
    path = screenshot_path(lead_id)
    if path.exists():
        path.unlink()


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
