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
from utils import db, github_api, screenshot, tracer, tracker, vercel_api, visuals

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
TEMPLATE_FILES = ("index.html", "style.css")
STUDIO_TEMPLATE = "studio"

_DEPLOYMENT_VALIDATION_TIMEOUT_SECONDS = 15
_AUTH_URL_PARTS = ("login", "signin", "sign-in", "auth", "authentication")
_AUTH_PAGE_MARKERS = (
    "vercel authentication",
    "log in to vercel",
    "login to vercel",
    "sign in to vercel",
)
_VERCEL_PROTECTION_DOCS_URL = (
    "https://vercel.com/docs/deployment-protection/"
    "methods-to-bypass-deployment-protection/protection-bypass-automation"
)

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

# Conservative category-level labels for leads whose source site did not
# expose a structured service list. Avoid narrow claims such as tree surgery
# or boiler certification that may not apply to the individual business.
NICHE_SERVICES = {
    "vehicle-repair": ["Servicing", "Repairs", "Diagnostics", "Brakes & Tyres", "General Enquiries"],
    "cafe": ["Coffee & Hot Drinks", "Food & Bakes", "Eat In", "Takeaway", "General Enquiries"],
    "landscaper": ["Garden Maintenance", "Planting", "Garden Design", "Outdoor Improvements", "Seasonal Care"],
    "plumber": ["Repairs", "Maintenance", "Installations", "Leaks & Faults", "General Enquiries"],
    "electrician": ["Repairs", "Installations", "Lighting", "Testing", "General Enquiries"],
    "salon": ["Cut & Style", "Colour", "Treatments", "Appointments", "General Enquiries"],
    "plasterer": ["Skimming", "Repairs", "Walls & Ceilings", "Rendering", "General Enquiries"],
    "restaurant": ["Food", "Drinks", "Menus", "Bookings", "General Enquiries"],
    "dentist": ["Routine Care", "Hygiene", "Treatment", "Appointments", "General Enquiries"],
    "gym": ["Membership", "Training", "Classes", "Facilities", "General Enquiries"],
}

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
    niche = visuals.normalized_niche(lead["niche"])
    if niche in NICHE_SERVICES:
        return NICHE_SERVICES[niche]
    google_types = lead.get("google_maps_types") or []
    if google_types:
        return [t.replace("_", " ").title() for t in google_types]
    return [niche_display_name(niche)]


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


def _pain_point_solution(lead: dict) -> str:
    services = niche_services(lead)
    offering = ", ".join(services[:3])
    location = (lead.get("location") or "the local area").strip()
    return (
        f"From {offering} to straightforward advice, {lead['business_name']} offers "
        f"practical help across {location}. Get in touch to discuss what you need, "
        "check availability, or request a quote."
    )


def _testimonial(lead: dict) -> str:
    # Testimonials are only shown when research found one on the business's
    # own website. Never invent social proof for a prospect preview.
    return (lead.get("testimonial") or "").strip()


def build_context(lead: dict) -> dict:
    niche = lead["niche"]
    direction = visuals.art_direction(niche)
    phone = (lead.get("phone") or "").strip()
    contact_email = (lead.get("contact_email") or "").strip()
    location = (lead.get("location") or "").strip()
    rating = google_rating_badge(lead)
    return {
        "business_name": lead["business_name"],
        "phone": phone,
        "phone_href": "".join(char for char in phone if char.isdigit() or char == "+"),
        "has_phone": bool(phone),
        "contact_email": contact_email,
        "has_email": bool(contact_email),
        "location": location,
        "pain_point_solution": _pain_point_solution(lead),
        "testimonial": _testimonial(lead),
        "hero_image_url": visuals.hero_image_data_uri(lead),
        "year": datetime.now(timezone.utc).year,
        "hero_headline": lead["business_name"],
        # A real, working link back to this lead's own preview, generated
        # from the lead id alone (no dependency on the site already being
        # deployed -- utils/tracker.py signs it purely from lead_id, and
        # the webhook server resolves it to the real preview_url from the
        # `websites` table whenever it's actually clicked).
        "preview_url": tracker.create_click_link(lead["id"]),
        # The studio template combines this evidence-backed business data
        # with the niche art direction. Rating and specialty stay optional.
        "niche_display": direction["label"],
        "hero_tagline": direction["tagline"],
        "eyebrow": direction["eyebrow"],
        "services": niche_services(lead),
        "google_rating": rating,
        "google_reviews_count": lead.get("google_reviews_count") or 0,
        "specialty": lead_specialty(lead),
        "accent": direction["accent"],
        "accent_2": direction["accent_2"],
        "ink": direction["ink"],
        "paper": direction["paper"],
        "image_position": direction["image_position"],
        "art_direction_prompt": visuals.image_prompt(lead),
        "image_negative_prompt": visuals.negative_prompt(),
    }


def render_template_files(niche: str, context: dict) -> dict[str, str]:
    """Render the shared editorial studio template with niche art direction."""
    niche_dir = TEMPLATES_DIR / STUDIO_TEMPLATE
    if not niche_dir.exists():
        raise ValueError(f"Core website template is missing: {STUDIO_TEMPLATE}")

    env = Environment(
        loader=FileSystemLoader(str(niche_dir)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )
    rendered = {}
    for filename in TEMPLATE_FILES:
        template = env.get_template(filename)
        rendered[filename] = template.render(**context)
    rendered["ART-DIRECTION.txt"] = (
        "HERO IMAGE BRIEF\n\n"
        f"{context['art_direction_prompt']}\n\n"
        "DO NOT INCLUDE\n\n"
        f"{context['image_negative_prompt']}\n"
    )
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

    headers = {}
    if not config.ENABLE_LIVE_SEND and config.VERCEL_AUTOMATION_BYPASS_SECRET:
        headers["x-vercel-protection-bypass"] = config.VERCEL_AUTOMATION_BYPASS_SECRET
    try:
        resp = requests.get(
            preview_url,
            allow_redirects=True,
            headers=headers,
            timeout=_DEPLOYMENT_VALIDATION_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Deployment URL is not publicly accessible: {preview_url}") from exc

    final_url = resp.url or preview_url
    final_path = urlparse(final_url).path.lower()
    if resp.status_code >= 400:
        raise RuntimeError(f"Deployment URL returned HTTP {resp.status_code}: {preview_url}")
    if any(part in final_path for part in _AUTH_URL_PARTS):
        raise RuntimeError(
            "Deployment URL redirects to Vercel authentication. Turn off Deployment "
            "Protection for prospect previews, or set VERCEL_AUTOMATION_BYPASS_SECRET "
            f"for dry-run testing only. Docs: {_VERCEL_PROTECTION_DOCS_URL}. "
            f"Redirected URL: {final_url}"
        )
    page_text = resp.text.lower()
    if any(marker in page_text for marker in _AUTH_PAGE_MARKERS):
        raise RuntimeError(
            "Deployment URL shows Vercel authentication. Turn off Deployment Protection "
            "for prospect previews, or set VERCEL_AUTOMATION_BYPASS_SECRET for dry-run "
            f"testing only. Docs: {_VERCEL_PROTECTION_DOCS_URL}. URL: {preview_url}"
        )

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
    # Every niche uses the same art-directed studio shell. A missing core
    # template is therefore a system setup failure, not a niche mismatch.
    if STUDIO_TEMPLATE not in available_niches():
        db.update_lead_status(
            lead["id"], "lost", notes=f"Core website template '{STUDIO_TEMPLATE}' is missing"
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
    for lead in db.list_leads_by_status_priority("researched"):
        try:
            process_lead(lead)
        except Exception as exc:  # noqa: BLE001 - one bad lead must not kill the batch
            db.log_state_history(lead["id"], "researched", "researched", notes=f"Design failed: {exc}")
