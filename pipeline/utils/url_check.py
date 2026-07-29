"""Shared constants for "is this URL safely public?" checks.

Both design_agent (validating a fresh Vercel deployment) and sales_agent
(re-validating the preview link immediately before a cold email goes out)
need to recognise an authentication wall. These lived as private copies in
both modules; a bad merge that referenced one file's names from the other
is what motivated pulling them into one place. Keep them here only --
duplicating them again is how they drift.
"""
from __future__ import annotations

# URL path fragments that mean "this is a login wall, not the customer's site".
AUTH_URL_PARTS: tuple[str, ...] = ("login", "signin", "sign-in", "auth", "authentication")

# Body text that means Vercel served its own auth page instead of the deploy.
AUTH_PAGE_MARKERS: tuple[str, ...] = (
    "vercel authentication",
    "log in to vercel",
    "login to vercel",
    "sign in to vercel",
)

# Seconds to wait when fetching a URL to prove it is publicly reachable.
DEPLOYMENT_VALIDATION_TIMEOUT_SECONDS: int = 15
PREVIEW_VALIDATION_TIMEOUT_SECONDS: int = 15
