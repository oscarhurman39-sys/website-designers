"""DesignAgent: renders a niche template for a lead, deploys it straight to
Vercel (git-less, inline files), and records the resulting preview URL.

build_context() prefers real content imported from the lead's own site
(utils/content_importer.py) -- logo, photos, hours, services, reviews,
brand colors -- over generic niche fallbacks/stock photos/a fabricated
testimonial, wherever content_importer actually found something.

Niche templates can also opt into shared, reusable sections (photo
gallery, service area banner, etc. -- see templates/_shared/sections.html
and the NICHE_SECTIONS manifest below) instead of every niche hand-rolling
its own copy of the same section.

NO GitHub repo is created at preview time. Rendered files are kept on disk
under pipeline/rendered_sites/ instead, and the private hand-off repo is
created lazily by create_handoff_repo() -- only when a client actually buys
and the operator runs `transfer <lead_id>`. (Previously every preview got
its own repo, which flooded the account with dozens of dead repos.)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

import config
from utils import content_importer, db, github_api, screenshot, site_audit, tracer, tracker, url_safety, vercel_api

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
# Shared Jinja macros (templates/_shared/sections.html) every niche template
# can {% import %} -- not itself a niche, so it's excluded from
# available_niches() by the leading underscore.
SHARED_PARTIALS_DIR = TEMPLATES_DIR / "_shared"
RENDERED_SITES_DIR = Path(__file__).resolve().parent.parent / "rendered_sites"
TEMPLATE_FILES = ("index.html", "style.css")
DEFAULT_NICHE = "default"

# Per-niche manifest of which shared sections (templates/_shared/
# sections.html) that niche's template opts into -- exposed to templates
# as the `enabled_sections` context list. A niche not listed here has
# opted into none; add an entry when a template starts using one.
NICHE_SECTIONS: dict[str, tuple[str, ...]] = {
    "landscaper": ("photo_gallery", "hours_list"),
    "cafe": ("hours_list",),
    "plumber": ("service_area_emergency", "hours_list"),
}


def enabled_sections(niche: str) -> tuple[str, ...]:
    return NICHE_SECTIONS.get(niche, ())

# A lead whose design/deploy keeps failing is retried this many times
# (once per orchestrator cycle) before being marked lost -- without a cap
# the same broken deploy would re-run forever, every 60 seconds.
MAX_DESIGN_ATTEMPTS = 3

_PLACEHOLDER_IMAGE_BASE = "https://picsum.photos/seed"

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
    """Every templates/<niche>/ subfolder, excluding _-prefixed ones like
    _shared/ (shared macros, not a selectable niche)."""
    if not TEMPLATES_DIR.exists():
        return set()
    return {p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")}


def niche_display_name(niche: str) -> str:
    """Un-hyphenate and title-case a niche slug for display, e.g.
    'vehicle-repair' -> 'Vehicle Repair'."""
    return niche.replace("-", " ").replace("_", " ").strip().title()


def niche_services(lead: dict) -> list[str]:
    """The business's OWN service/menu names when content_importer found
    some on their existing site -- real names sell harder than a generic
    curated list. Falls back to the curated per-niche list, then the
    lead's Google Maps 'types' field, then the niche name itself so the
    Services section is never empty."""
    scraped = content_importer.load_content(lead).get("services")
    if scraped:
        return scraped
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


def get_hero_image_url(lead: dict) -> str:
    """A real photo of THEIR OWN business (see utils/content_importer.py)
    beats any stock photo -- a stock shot of someone else's van/shop reads
    as fake to the actual owner, but their own storefront reads as
    authentic. Falls back to Unsplash's search API if UNSPLASH_ACCESS_KEY
    is configured and no real photo was found, then to a deterministic
    (seeded) placeholder image service that needs no API key, so
    DesignAgent works out of the box.
    """
    photos = content_importer.load_content(lead).get("photos")
    if photos:
        return photos[0]

    niche = lead["niche"]
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


_BRAND_TEXT_WHITE = "#ffffff"
_BRAND_TEXT_DARK = "#111827"


def _readable_text_color(background_hex: str) -> str:
    """White or near-black text -- whichever gives better WCAG contrast
    against `background_hex` -- for use alongside an inline brand-color
    background (build_context's `brand_text_color`). A brand color isn't
    guaranteed to be dark enough for white text to read clearly (e.g. a
    pale yellow), so this can't just hardcode white."""
    if site_audit.contrast_ratio(background_hex, _BRAND_TEXT_WHITE) >= site_audit.contrast_ratio(
        background_hex, _BRAND_TEXT_DARK
    ):
        return _BRAND_TEXT_WHITE
    return _BRAND_TEXT_DARK


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


def meta_description(lead: dict) -> str:
    """A <=155-char search-result description built from real lead data --
    every template ships one because their old site almost never has one
    (it's one of the most common site_audit findings)."""
    niche = lead["niche"]
    services = ", ".join(niche_services(lead)[:3])
    city = lead.get("location") or "your area"
    desc = f"{lead['business_name']} -- trusted local {niche_display_name(niche).lower()} in {city}. {services}."
    phone = (lead.get("phone") or "").strip()
    if phone and len(desc) + len(phone) + 8 <= 155:
        desc += f" Call {phone}."
    return desc[:155]


def json_ld_script(lead: dict) -> str:
    """A schema.org LocalBusiness JSON-LD <script> block. Serialized with
    json.dumps (so scraped strings can't break out of the JSON) and with
    '</' escaped (so they can't close the script tag early) -- templates
    render it with `| safe`, which is only OK because of those two steps."""
    data: dict = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "name": lead["business_name"],
        "description": meta_description(lead),
    }
    if lead.get("phone"):
        data["telephone"] = lead["phone"]
    if lead.get("location"):
        data["address"] = {"@type": "PostalAddress", "addressLocality": lead["location"]}
    rating = google_rating_badge(lead)
    if rating:
        data["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": rating,
            "reviewCount": lead.get("google_reviews_count"),
        }
    payload = json.dumps(data, ensure_ascii=True).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def build_context(lead: dict) -> dict:
    niche = lead["niche"]
    imported = content_importer.load_content(lead)
    return {
        "business_name": lead["business_name"],
        "phone": lead.get("phone") or "Call us",
        "location": lead.get("location") or "",
        "pain_point_solution": _pain_point_solution(lead),
        "testimonial": _testimonial(lead),
        "hero_image_url": get_hero_image_url(lead),
        "year": datetime.now(timezone.utc).year,
        # Real content imported from the business's own site (see
        # utils/content_importer.py) -- "" / [] when nothing was found, so
        # every template can safely `{% if logo_url %}` / `{% for %}` these
        # without a KeyError either way.
        "logo_url": imported["logo_url"],
        "photos": imported["photos"],
        "brand_colors": imported["brand_colors"],
        # Whichever of white/near-black actually reads clearly on top of
        # brand_colors[0] -- never assume white just because it usually
        # works (see _readable_text_color).
        "brand_text_color": _readable_text_color(imported["brand_colors"][0]) if imported["brand_colors"] else "",
        "hours": imported["hours"],
        # First real review if any were scraped, else the single
        # (possibly fabricated) `testimonial` above -- so a template can
        # render a multi-review list without special-casing the count.
        "reviews": imported["reviews"] or [_testimonial(lead)],
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
        # Self-serve checkout -- webhook_server.py's GET /buy/<lead_id>
        # creates a fresh Stripe Checkout Session on click (see
        # utils/stripe_utils.py) so a visitor can buy this site directly
        # from the preview itself, no reply/human step required.
        "buy_url": f"{config.PUBLIC_BASE_URL}/buy/{lead['id']}",
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
        # SEO head block, rendered by every template: a real meta
        # description and schema.org LocalBusiness markup. Their old site
        # almost never has these -- it's a selling point we can point at.
        "meta_description": meta_description(lead),
        "json_ld_script": json_ld_script(lead),
        # Which shared sections (templates/_shared/sections.html) this
        # niche's template has opted into -- see NICHE_SECTIONS.
        "enabled_sections": enabled_sections(niche),
    }


def render_template_files(niche: str, context: dict) -> dict[str, str]:
    """Render index.html and style.css for `niche` with Jinja2. Returns
    {relative_path: rendered_content} ready to push to GitHub / Vercel.
    Falls back to the DEFAULT_NICHE template for any niche without its own
    dedicated template folder (e.g. 'vehicle-repair').

    Templates are resolved against the niche's own folder first, then
    SHARED_PARTIALS_DIR -- so `{% import "sections.html" as sections %}`
    resolves regardless of which niche is rendering, while `index.html`/
    `style.css` still always come from the niche-specific folder (a niche
    folder can't accidentally shadow another niche's files since each
    niche only ever renders its own two filenames)."""
    niche_dir = TEMPLATES_DIR / niche
    if not niche_dir.exists():
        niche_dir = TEMPLATES_DIR / DEFAULT_NICHE
    if not niche_dir.exists():
        raise ValueError(f"No template found for niche '{niche}'")

    env = Environment(
        loader=ChoiceLoader([FileSystemLoader(str(niche_dir)), FileSystemLoader(str(SHARED_PARTIALS_DIR))]),
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


def _run_readiness_audit(lead_id: int, html: str, preview_url: str) -> None:
    """Pre-publish QA gate (see utils/site_audit.py's audit_readiness) run
    against the just-deployed preview, persisted for the dashboard/
    operator to see. Exactly like screenshot capture, a readiness audit is
    an enrichment, never a blocker: any failure here (a slow/unreachable
    linked asset, a network hiccup on the link-check crawl) is caught and
    logged, and the caller keeps the deploy that already succeeded."""
    try:
        result = site_audit.audit_readiness(html, preview_url)
        db.insert_site_audit(lead_id, "generated_preview", result, url=preview_url)
    except Exception as exc:  # noqa: BLE001 - readiness audit must never fail a successful deploy
        print(f"[design_agent] Readiness audit failed for lead {lead_id}, continuing without one: {exc}")


def _validate_deployment_url(deployment: dict) -> str:
    """Return a public website URL or raise before any email can be queued.

    A deploy can technically succeed while the resulting URL is blank,
    malformed, still pending, or hidden behind an authentication page. Those
    states must not advance the lead to "designed", because SalesAgent sends
    every lead in that state. The ready_state check runs first (it needs no
    network); the shared public-URL checks live in utils/url_safety.py.
    """
    preview_url = (deployment.get("url") or "").strip()
    final_state = deployment.get("ready_state")
    if final_state and final_state != "READY":
        raise RuntimeError(f"Deployment is not ready: ready_state={final_state!r}, url={preview_url!r}")
    return url_safety.validate_public_url(preview_url)


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

    # Deliberately no GitHub repo here -- previews deploy git-less to
    # Vercel, and the hand-off repo is created only at transfer time
    # (create_handoff_repo). Files are saved locally so transfer can push
    # exactly what the client saw, not a fresh re-render.
    local_dir = _save_rendered_files(lead["id"], files)
    deployment = vercel_api.deploy_files(
        github_api.make_repo_name(lead["business_name"], lead["id"]), files
    )
    preview_url = _validate_deployment_url(deployment)

    _run_readiness_audit(lead["id"], files["index.html"], preview_url)
    screenshot_url, screenshot_path = _capture_and_publish_screenshot(lead["id"], preview_url)

    website_id = db.insert_website(
        lead_id=lead["id"],
        template_niche=niche,
        repo_url="",
        repo_full_name="",
        preview_url=preview_url,
        vercel_project_id=deployment["deployment_id"],
        screenshot_url=screenshot_url,
        screenshot_path=screenshot_path,
        local_dir=local_dir,
    )
    db.update_lead_status(lead["id"], "designed", notes=f"Preview deployed: {preview_url}")
    return db.get_website_by_lead(lead["id"]) if website_id else None


def _save_rendered_files(lead_id: int, files: dict[str, str]) -> str:
    """Persist rendered template files to pipeline/rendered_sites/lead-<id>/
    so the hand-off repo can be built later without re-rendering. Returns
    the directory path as a string (what gets stored in websites.local_dir)."""
    site_dir = RENDERED_SITES_DIR / f"lead-{lead_id}"
    site_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        out_path = site_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
    return str(site_dir)


def _load_rendered_files(local_dir: str) -> Optional[dict[str, str]]:
    """Read back the files saved by _save_rendered_files. None if the
    directory is missing or empty (e.g. wiped disk, older lead)."""
    site_dir = Path(local_dir) if local_dir else None
    if site_dir is None or not site_dir.is_dir():
        return None
    files = {
        str(p.relative_to(site_dir)): p.read_text(encoding="utf-8")
        for p in site_dir.rglob("*")
        if p.is_file()
    }
    return files or None


def create_handoff_repo(lead_id: int) -> tuple[str, str]:
    """Create the private GitHub repo for a SOLD site -- called by main.py's
    `transfer` command, never at preview time. Uses the exact files that
    were deployed (websites.local_dir), falling back to a fresh re-render
    if they're gone. Records the repo on the website row and returns
    (repo_url, repo_full_name)."""
    lead = db.get_lead(lead_id)
    website = db.get_website_by_lead(lead_id)
    if lead is None or website is None:
        raise RuntimeError(f"No lead/website found for lead {lead_id}")
    if website.get("repo_full_name"):
        return website["repo_url"], website["repo_full_name"]

    files = _load_rendered_files(website.get("local_dir") or "")
    if files is None:
        files = render_template_files(website["template_niche"], build_context(lead))
    _repo, repo_url, repo_full_name = github_api.create_repo_with_files(
        lead["business_name"], lead_id, files
    )
    db.update_website_repo(lead_id, repo_url, repo_full_name)
    return repo_url, repo_full_name


def run() -> None:
    """Main entrypoint called by main.py: design a site for every
    'researched' lead that has a matching template. Each failure bumps the
    lead's design_attempts counter; after MAX_DESIGN_ATTEMPTS the lead is
    marked lost instead of retrying forever every cycle."""
    for lead in db.list_leads_by_status("researched"):
        try:
            process_lead(lead)
        except Exception as exc:  # noqa: BLE001 - one bad lead must not kill the batch
            attempts = db.increment_design_attempts(lead["id"])
            if attempts >= MAX_DESIGN_ATTEMPTS:
                db.update_lead_status(
                    lead["id"], "lost",
                    notes=f"Design failed {attempts}x, giving up. Last error: {exc}",
                )
            else:
                db.log_state_history(
                    lead["id"], "researched", "researched",
                    notes=f"Design failed (attempt {attempts}/{MAX_DESIGN_ATTEMPTS}): {exc}",
                )
