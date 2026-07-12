"""DesignAgent: renders a niche template for a lead, pushes it to a new
private GitHub repo, deploys it to Vercel, and records the resulting
preview URL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from utils import db, github_api, screenshot, tracer, tracker, vercel_api

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
TEMPLATE_FILES = ("index.html", "style.css")
DEFAULT_NICHE = "default"

_PLACEHOLDER_IMAGE_BASE = "https://picsum.photos/seed"
_DEPLOYMENT_VALIDATION_TIMEOUT_SECONDS = 15
_AUTH_URL_PARTS = ("login", "signin", "sign-in", "auth", "authentication")
_AUTH_PAGE_MARKERS = (
    "vercel authentication",
    "log in to vercel",
    "login to vercel",
    "sign in to vercel",
)

# Unsplash search terms per niche -- more specific than the raw niche
# string so the fetched photo actually matches the trade (e.g. a mechanic
# under a car, not a generic "vehicle" stock shot). Only used by the older,
# pre-existing per-niche templates (dentist/gym/restaurant) that still
# render a photographic hero; templates/default deliberately does NOT use
# this -- a stock photo of someone else's van/shop reads as fake to the
# actual owner, so its hero is a CSS-only pattern instead (see build_context).
_NICHE_IMAGE_QUERIES = {
    "vehicle-repair": "car mechanic workshop",
    "cafe": "cozy coffee shop interior",
    "landscaper": "landscaped garden",
    "plumber": "plumber at work",
    "electrician": "electrician at work",
    "salon": "hair salon interior",
}

# Minimum Google review count before the "Rated X on Google" hero badge is
# shown -- a handful of reviews reads worse than no badge at all.
MIN_GOOGLE_REVIEWS_FOR_BADGE = 15

# Human-readable labels for common Google Places 'types' specialties, shown
# prominently in the hero/services section when a lead has one.
SPECIALTY_LABELS = {
    "hybrid_vehicle_specialist": "Hybrid & EV Specialist",
    "electric_vehicle_specialist": "EV Specialist",
    "organic_coffee": "Organic Coffee",
    "emergency_service": "24/7 Emergency Service",
    "24_hour_service": "24/7 Emergency Service",
    "same_day_service": "Same-Day Service",
    "mobile_service": "We Come To You",
    "eco_friendly": "Eco-Friendly",
    "family_owned": "Family Owned & Operated",
    "wheelchair_accessible": "Wheelchair Accessible",
}

# Real trade services shown in the Services section, keyed by niche.
NICHE_SERVICES = {
    "vehicle-repair": ["MOT & Servicing", "Brake Repairs", "Mobile Diagnostics", "Clutch Replacement", "Battery Replacement", "Air Con Service"],
    "cafe": ["Specialty Coffee", "Fresh Pastries", "Light Lunches", "Takeaway", "Catering"],
    "landscaper": ["Garden Design", "Lawn Care", "Patios & Decking", "Fencing", "Tree Surgery"],
    "plumber": ["Emergency Callouts", "Bathroom Fitting", "Boiler Repairs", "Radiator Installation", "Leak Detection"],
    "electrician": ["Rewiring", "Fuse Box Upgrades", "Lighting Design", "PAT Testing", "Emergency Repairs"],
    "salon": ["Cut & Style", "Colouring", "Treatments", "Bridal", "Blow Dry"],
}

# Hyper-local hero taglines, keyed by niche. `{city}` is filled in from the
# lead's own location.
NICHE_HERO_TEXT = {
    "vehicle-repair": "Keeping {city} drivers on the road",
    "cafe": "Your daily cup in {city}",
    "landscaper": "Beautiful gardens in {city}",
    "plumber": "Keeping {city}'s pipes flowing",
}
DEFAULT_HERO_TEXT = "Serving {city} with pride"

# Nav labels, keyed by niche.
NICHE_NAV_LABELS = {
    "vehicle-repair": ["Home", "Services", "Areas Covered", "Reviews", "Contact"],
    "cafe": ["Home", "Menu", "Gallery", "Reviews", "Contact"],
    "landscaper": ["Home", "Services", "Projects", "Reviews", "Contact"],
}
DEFAULT_NAV_LABELS = ["Home", "About", "Services", "Reviews", "Contact"]


def available_niches() -> set[str]:
    if not TEMPLATES_DIR.exists():
        return set()
    return {p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()}


def niche_display_name(niche: str) -> str:
    """Un-hyphenate and title-case a niche slug for display, e.g.
    'vehicle-repair' -> 'Vehicle Repair'."""
    return niche.replace("-", " ").replace("_", " ").strip().title()


def niche_services(lead: dict) -> list[str]:
    """Real service names for the niche. Falls back to the lead's Google
    Maps 'types' field if there's no curated list, then to the niche name
    itself so the Services section is never empty."""
    niche = lead["niche"]
    if niche in NICHE_SERVICES:
        return NICHE_SERVICES[niche]
    google_types = lead.get("google_maps_types") or []
    if google_types:
        return [t.replace("_", " ").title() for t in google_types]
    return [niche_display_name(niche)]


def hero_tagline(lead: dict) -> str:
    niche = lead["niche"]
    city = lead.get("location") or "your area"
    template = NICHE_HERO_TEXT.get(niche, DEFAULT_HERO_TEXT)
    return template.format(city=city)


def nav_labels(niche: str) -> list[str]:
    return NICHE_NAV_LABELS.get(niche, DEFAULT_NAV_LABELS)


def lead_specialty(lead: dict) -> Optional[str]:
    """First recognized specialty from the lead's Google Places 'types'
    array (e.g. 'hybrid_vehicle_specialist' -> 'Hybrid & EV Specialist'),
    mapped to a human-readable label. None if the lead has no 'types' data
    or none of it matches a known specialty."""
    for google_type in lead.get("google_maps_types") or []:
        label = SPECIALTY_LABELS.get(google_type)
        if label:
            return label
    return None


def google_rating_badge(lead: dict) -> Optional[float]:
    """The lead's Google rating, but only once it's backed by enough
    reviews to be worth highlighting (see MIN_GOOGLE_REVIEWS_FOR_BADGE)."""
    rating = lead.get("google_rating")
    reviews_count = lead.get("google_reviews_count") or 0
    if rating and reviews_count > MIN_GOOGLE_REVIEWS_FOR_BADGE:
        return rating
    return None


def get_hero_image_url(niche: str) -> str:
    """Fetch a relevant free stock photo URL for the niche.

    Uses Unsplash's search API if UNSPLASH_ACCESS_KEY is configured;
    otherwise falls back to a deterministic (seeded) placeholder image
    service that needs no API key, so DesignAgent works out of the box.
    """
    query = _NICHE_IMAGE_QUERIES.get(niche, niche_display_name(niche))
    if config.UNSPLASH_ACCESS_KEY:
        try:
            resp = requests.get(
                "https://api.unsplash.com/photos/random",
                params={"query": query, "orientation": "landscape"},
                headers={"Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            url = data.get("urls", {}).get("regular")
            if url:
                return url
        except (requests.RequestException, ValueError, KeyError):
            pass  # fall through to placeholder
    return f"{_PLACEHOLDER_IMAGE_BASE}/{niche}/1600/900"


def _pain_point_solution(lead: dict) -> str:
    pain_point = (lead.get("pain_point") or "").strip()
    if pain_point:
        return (
            f"We noticed something on your current online presence: \"{pain_point}\" "
            "This preview site is built to fix exactly that -- fast, mobile-friendly, "
            "and designed to turn visitors into customers."
        )
    return (
        f"{lead['business_name']} deserves a site that loads fast, looks great on "
        "phones, and makes it effortless for new customers to reach you."
    )


def _testimonial(lead: dict) -> str:
    return (lead.get("testimonial") or "").strip() or (
        f"{lead['business_name']} always takes great care of us -- highly recommend "
        "to anyone in the area."
    )


def build_context(lead: dict) -> dict:
    niche = lead["niche"]
    return {
        "business_name": lead["business_name"],
        "phone": lead.get("phone") or "Call us",
        "location": lead.get("location") or "",
        "pain_point_solution": _pain_point_solution(lead),
        "testimonial": _testimonial(lead),
        "hero_image_url": get_hero_image_url(niche),
        "year": datetime.now(timezone.utc).year,
        # Added for the newer Tailwind-based templates (landscaper/cafe/
        # plumber/salon/electrician); older templates simply ignore unused
        # context keys, so this is additive and doesn't affect them.
        "hero_headline": f"{lead['business_name']} -- Trusted Local {niche_display_name(niche)}",
        # A real, working link back to this lead's own preview, generated
        # from the lead id alone (no dependency on the site already being
        # deployed -- utils/tracker.py signs it purely from lead_id, and
        # the webhook server resolves it to the real preview_url from the
        # `websites` table whenever it's actually clicked).
        "preview_url": tracker.create_click_link(lead["id"]),
        # Used by templates/default -- hyper-local hero tagline, niche
        # display name, real service names, tailored nav labels, and an
        # optional Google rating badge (only shown when the lead actually
        # has one).
        "niche_display": niche_display_name(niche),
        "hero_tagline": hero_tagline(lead),
        "services": niche_services(lead),
        "nav_labels": nav_labels(niche),
        # Only set once the lead has enough reviews to be worth
        # highlighting (see MIN_GOOGLE_REVIEWS_FOR_BADGE).
        "google_rating": google_rating_badge(lead),
        "specialty": lead_specialty(lead),
    }


def render_template_files(niche: str, context: dict) -> dict[str, str]:
    """Render index.html and style.css for `niche` with Jinja2. Returns
    {relative_path: rendered_content} ready to push to GitHub / Vercel.
    Falls back to the DEFAULT_NICHE template for any niche without its own
    dedicated template folder (e.g. 'vehicle-repair')."""
    niche_dir = TEMPLATES_DIR / niche
    if not niche_dir.exists():
        niche_dir = TEMPLATES_DIR / DEFAULT_NICHE
    if not niche_dir.exists():
        raise ValueError(f"No template found for niche '{niche}'")

    env = Environment(
        loader=FileSystemLoader(str(niche_dir)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )
    rendered = {}
    for filename in TEMPLATE_FILES:
        template = env.get_template(filename)
        rendered[filename] = template.render(**context)
    return rendered


def _capture_and_publish_screenshot(lead_id: int, preview_url: str) -> tuple[str, str]:
    """Capture (or reuse a cached) screenshot of the freshly-deployed
    preview. Returns (public_screenshot_url, local_screenshot_path) -- the
    former is what gets linked/displayed, the latter is what sales_agent.py
    actually reads bytes from for the CID attachment. A screenshot is a
    nice-to-have for the cold email, not a requirement for a successful
    deploy -- any failure here (Playwright/browser unavailable, the
    preview not reachable yet, etc.) is caught and logged, and the caller
    proceeds with both empty rather than losing the deploy that already
    succeeded."""
    try:
        local_path = screenshot.capture_screenshot_sync(preview_url, lead_id)
    except Exception as exc:  # noqa: BLE001 - screenshot capture must never fail a successful deploy
        print(f"[design_agent] Screenshot capture failed for lead {lead_id}, continuing without one: {exc}")
        return "", ""
    return f"{config.PUBLIC_BASE_URL}/screenshots/{lead_id}.png", str(local_path)


def _validate_deployment_url(deployment: dict) -> str:
    """Return a public website URL or raise before any email can be queued.

    A deploy can technically succeed while the resulting URL is blank,
    malformed, still pending, or hidden behind an authentication page. Those
    states must not advance the lead to "designed", because SalesAgent sends
    every lead in that state.
    """
    preview_url = (deployment.get("url") or "").strip()
    parsed = urlparse(preview_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(f"Deployment did not return a valid public URL: {preview_url!r}")

    final_state = deployment.get("ready_state")
    if final_state and final_state != "READY":
        raise RuntimeError(f"Deployment is not ready: ready_state={final_state!r}, url={preview_url!r}")

    if any(part in parsed.path.lower() for part in _AUTH_URL_PARTS):
        raise RuntimeError(f"Deployment URL points to an authentication path: {preview_url}")

    try:
        resp = requests.get(preview_url, allow_redirects=True, timeout=_DEPLOYMENT_VALIDATION_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise RuntimeError(f"Deployment URL is not publicly accessible: {preview_url}") from exc

    final_url = resp.url or preview_url
    final_path = urlparse(final_url).path.lower()
    if resp.status_code >= 400:
        raise RuntimeError(f"Deployment URL returned HTTP {resp.status_code}: {preview_url}")
    if any(part in final_path for part in _AUTH_URL_PARTS):
        raise RuntimeError(f"Deployment URL redirects to an authentication path: {final_url}")
    page_text = resp.text.lower()
    if any(marker in page_text for marker in _AUTH_PAGE_MARKERS):
        raise RuntimeError(f"Deployment URL shows an authentication page: {preview_url}")

    return preview_url


def process_lead(lead: dict) -> Optional[dict]:
    """Render, deploy, and persist a website for a single 'researched' lead.
    Returns the website record, or None if the niche has no template.
    Wrapped in a trace -> agent -> tool span (see utils/tracer.py); the
    actual design/deploy logic lives untouched in _process_lead_impl."""
    return tracer.run_traced(
        agent_id="design-agent",
        agent_name="DesignAgent",
        tool_name="render_and_deploy_preview",
        input_data={"lead_id": lead["id"], "business_name": lead["business_name"], "niche": lead["niche"]},
        fn=lambda: _process_lead_impl(lead),
    )


def _process_lead_impl(lead: dict) -> Optional[dict]:
    niche = lead["niche"]
    # Niches without a dedicated template folder (e.g. 'vehicle-repair')
    # fall back to templates/default -- so only a totally missing default
    # template (should never happen) marks the lead lost.
    if niche not in available_niches() and DEFAULT_NICHE not in available_niches():
        db.update_lead_status(
            lead["id"], "lost", notes=f"No website template exists for niche '{niche}'"
        )
        return None

    context = build_context(lead)
    files = render_template_files(niche, context)

    _repo, repo_url, repo_full_name = github_api.create_repo_with_files(
        lead["business_name"], lead["id"], files
    )
    deployment = vercel_api.deploy_files(
        github_api.make_repo_name(lead["business_name"], lead["id"]), files
    )
    preview_url = _validate_deployment_url(deployment)

    screenshot_url, screenshot_path = _capture_and_publish_screenshot(lead["id"], preview_url)

    website_id = db.insert_website(
        lead_id=lead["id"],
        template_niche=niche,
        repo_url=repo_url,
        repo_full_name=repo_full_name,
        preview_url=preview_url,
        vercel_project_id=deployment["deployment_id"],
        screenshot_url=screenshot_url,
        screenshot_path=screenshot_path,
    )
    db.update_lead_status(lead["id"], "designed", notes=f"Preview deployed: {preview_url}")
    return db.get_website_by_lead(lead["id"]) if website_id else None


def run() -> None:
    """Main entrypoint called by main.py: design a site for every
    'researched' lead that has a matching template."""
    for lead in db.list_leads_by_status("researched"):
        try:
            process_lead(lead)
        except Exception as exc:  # noqa: BLE001 - one bad lead must not kill the batch
            db.log_state_history(lead["id"], "researched", "researched", notes=f"Design failed: {exc}")
