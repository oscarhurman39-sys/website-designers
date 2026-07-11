"""DesignAgent: renders a niche template for a lead, pushes it to a new
private GitHub repo, deploys it to Vercel, and records the resulting
preview URL.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from utils import db, github_api, image_placeholder, screenshot, tracer, tracker, vercel_api

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
TEMPLATE_FILES = ("index.html", "style.css")

# Maps Google Places `types` (stored on the lead as `categories`) to
# human-readable services shown in a preview's "What We Offer" section, so
# the list reflects the real business instead of a generic template default.
SERVICE_MAP = {
    "car_repair": "Auto Repair",
    "oil_change": "Oil Change",
    "brake_service": "Brake Service",
    "tire_shop": "Tire Replacement",
    "auto_body_shop": "Body Work",
    "towing_service": "Towing",
    "air_conditioning_service": "A/C Service",
    "plumber": "Plumbing",
    "electrician": "Electrical Repairs",
    "landscaper": "Landscaping",
    "cafe": "Coffee & Pastries",
    "salon": "Hair & Beauty",
    # Rounded out for the pipeline's other built-in template niches.
    "hair_care": "Hair & Beauty",
    "beauty_salon": "Hair & Beauty",
    "dentist": "Dental Care",
    "gym": "Fitness Training",
    "restaurant": "Dining",
    "bakery": "Fresh Baked Goods",
    "bar": "Drinks & Bar",
    # add more as needed
}


def get_services_from_types(place_types):
    """Map a list of Google Places types to unique service labels, or a
    single generic fallback when none are recognized."""
    return list({SERVICE_MAP[t] for t in place_types if t in SERVICE_MAP}) or ["General Service"]


def available_niches() -> set[str]:
    if not TEMPLATES_DIR.exists():
        return set()
    return {p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()}


def resolve_template_niche(niche: str) -> Optional[str]:
    """Which template folder to actually render for a lead's niche.

    Prefers an exact per-niche template; falls back to the generic
    'default' template (templates/default/) when there's no folder for the
    lead's niche, so a lead in an unsupported niche still gets a clean site
    instead of being dropped. Returns None only when neither exists."""
    niches = available_niches()
    if niche in niches:
        return niche
    if "default" in niches:
        return "default"
    return None


# Hero gradient palettes for the classic (non-Tailwind) templates: picked
# deterministically per business (md5 of the name), so every business gets a
# stable look but the batch doesn't feel copy-pasted. The Tailwind templates
# already vary by business type via their niche accent colors.
_HERO_GRADIENTS: tuple[tuple[str, str], ...] = (
    ("#1e293b", "#0f172a"),  # slate night
    ("#134e4a", "#042f2e"),  # deep teal
    ("#312e81", "#1e1b4b"),  # indigo dusk
    ("#7c2d12", "#431407"),  # warm umber
    ("#164e63", "#082f49"),  # ocean
)


def hero_gradient(business_name: str) -> tuple[str, str]:
    """Stable (start, end) hero gradient colors for a business name."""
    digest = hashlib.md5(business_name.encode("utf-8")).digest()
    return _HERO_GRADIENTS[digest[0] % len(_HERO_GRADIENTS)]


def get_hero_image_url(niche: str) -> str:
    """BYPASSED for the hero since the pure-CSS gradient hero replaced the
    placeholder image block in every template -- kept (with the Unsplash
    option below) for a future paid-client stage that reintroduces real
    photography. Return the hero image for a preview -- placeholder-first,
    always.

    Every cold preview ships with the self-contained, on-brand "your photo
    here" placeholder (utils/image_placeholder.py) instead of a stock or
    third-party photo: honest (the prospect sees a real photo goes there),
    legally safe (no Unsplash/Google Places image rights on a preview they
    never asked for), and unbreakable (a `data:` URI, no external URL to
    404). Real photography is swapped in only once a lead becomes a paying
    client.

    The Unsplash path is deliberately kept commented out below for that
    later, paid stage -- do NOT re-enable it for cold previews.
    """
    # --- Paid-client option (disabled): real stock photo via Unsplash ---
    # if config.UNSPLASH_ACCESS_KEY:
    #     try:
    #         resp = requests.get(
    #             "https://api.unsplash.com/photos/random",
    #             params={"query": niche, "orientation": "landscape"},
    #             headers={"Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}"},
    #             timeout=10,
    #         )
    #         resp.raise_for_status()
    #         url = resp.json().get("urls", {}).get("regular")
    #         if url:
    #             return url
    #     except (requests.RequestException, ValueError, KeyError):
    #         pass  # fall through to placeholder
    return image_placeholder.HERO_PLACEHOLDER_DATA_URI


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


# Placeholder copy for the review/testimonial sections of a preview site.
# Deliberately NOT real review content: republishing Google review text on a
# non-Google site conflicts with Google Maps Platform's display/attribution
# terms, and a made-up quote would be a fabricated testimonial. Like the
# "Your photo here" hero, these are honest placeholders swapped for the
# client's own (permission-cleared) reviews after they become a client.
_TESTIMONIAL_PLACEHOLDER = "Your best customer quote will sit right here."
_REVIEWS_PLACEHOLDER = "Reviews from your customers -- coming soon."


_HF_COPY_MODEL = "mistralai/Mistral-7B-Instruct-v0.2"


def _fallback_headline(lead: dict) -> str:
    return f"{lead['business_name']} -- Trusted Local {lead['niche'].title()}"


def _ai_copy(lead: dict) -> tuple[str, str]:
    """(headline, about) for the preview site: drafted by Hugging Face
    (same Mistral model sales_agent uses for emails) when HF_API_TOKEN is
    set and reachable, otherwise the deterministic fallbacks the templates
    always used -- an HF outage can never block a design. Returns copy
    only; persisting to the lead (ai_headline/ai_about) happens in
    _process_lead_impl so the dashboard can show it."""
    fallback = (_fallback_headline(lead), _pain_point_solution(lead))
    if not config.HF_API_TOKEN:
        return fallback
    try:
        from huggingface_hub import InferenceClient

        prompt = (
            "<s>[INST] You write short website copy for local businesses. "
            "Write a hero headline (max 8 words, no quotes) and an about "
            "paragraph (2 sentences, warm, plain English, no hype) for:\n"
            f"- Business: {lead['business_name']}\n"
            f"- Trade: {lead['niche']}\n"
            f"- Location: {lead.get('location', '')}\n\n"
            "Respond in EXACTLY this format, nothing else:\n"
            "Headline: <headline>\nAbout: <about paragraph>\n[/INST]"
        )
        raw = InferenceClient(model=_HF_COPY_MODEL, token=config.HF_API_TOKEN).text_generation(
            prompt, max_new_tokens=160, temperature=0.7, do_sample=True
        )
        match = re.search(r"Headline:\s*(.+?)\s*\n+\s*About:\s*(.+)", raw, re.DOTALL | re.IGNORECASE)
        if not match:
            return fallback
        headline = match.group(1).strip().strip('"')
        about = " ".join(match.group(2).split())
        if not headline or not about or len(headline) > 90 or len(about) > 500:
            return fallback
        return headline, about
    except Exception as exc:  # noqa: BLE001 - any HF/network failure falls back gracefully
        print(f"[design_agent] HF copy generation failed, using fallback copy: {exc}")
        return fallback


def _testimonial(lead: dict) -> str:
    """Placeholder only -- never real review text (see note above). The
    lead's stored testimonial/reviews stay in the DB for the operator's own
    context; they just don't get published on the preview."""
    return _TESTIMONIAL_PLACEHOLDER


def _opening_hours(lead: dict) -> str:
    """leads.opening_hours is stored as JSON (a list of human-readable
    per-weekday strings from Google Places, e.g. "Monday: 7:00 AM – 6:00
    PM") -- join it into one display-ready line."""
    raw = lead.get("opening_hours") or ""
    try:
        hours = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        hours = []
    return " | ".join(hours) if hours else ""


def _reviews(lead: dict) -> str:
    """Placeholder only -- never the Google review text stored on the lead
    (see the display-terms note above _TESTIMONIAL_PLACEHOLDER)."""
    return _REVIEWS_PLACEHOLDER


def _place_types(lead: dict) -> list[str]:
    """leads.categories is the business's Google Places `types` stored as a
    comma-joined string -- split it back into the list get_services_from_types
    expects."""
    return [t.strip() for t in (lead.get("categories") or "").split(",") if t.strip()]


def build_context(lead: dict) -> dict:
    # AI-drafted (or deterministic-fallback) copy drives the hero headline
    # and about section; exposed both under the names the templates already
    # use (hero_headline / pain_point_solution) and as ai_headline /
    # ai_about for anything newer.
    ai_headline, ai_about = _ai_copy(lead)
    context = {
        "business_name": lead["business_name"],
        "phone": lead.get("phone") or "Call us",
        "location": lead.get("location") or "",
        "pain_point_solution": ai_about,
        "ai_about": ai_about,
        "testimonial": _testimonial(lead),
        "hero_image_url": get_hero_image_url(lead["niche"]),
        "year": datetime.now(timezone.utc).year,
        # Added for the newer Tailwind-based templates (landscaper/cafe/
        # plumber/salon/electrician); older templates simply ignore unused
        # context keys, so this is additive and doesn't affect them.
        "hero_headline": ai_headline,
        "ai_headline": ai_headline,
        # A real, working link back to this lead's own preview, generated
        # from the lead id alone (no dependency on the site already being
        # deployed -- utils/tracker.py signs it purely from lead_id, and
        # the webhook server resolves it to the real preview_url from the
        # `websites` table whenever it's actually clicked). Only usable when
        # the webhook server is publicly reachable; otherwise the footer
        # link degrades to '#' rather than a dead localhost URL.
        "preview_url": tracker.create_click_link(lead["id"]) if tracker.tracking_is_public() else "#",
        # Added for Google Places-sourced leads; blank/fallback gracefully
        # for leads researched before this data was available.
        "opening_hours": _opening_hours(lead),
        "reviews": _reviews(lead),
        # Photo attribution slot -- intentionally empty while there's no
        # third-party image to credit. Kept for when a paying client's
        # real, licensed photos are swapped in later.
        "photo_credit": "",
        # "Your Offer" pricing card (templates/*/index.html): display-only
        # figures from config, formatted with thousands separators + £.
        "price_regular": f"£{config.WEBSITE_REGULAR_PRICE:,}",
        "price_offer": f"£{config.WEBSITE_OFFER_PRICE:,}",
        "claim_mailto": config.claim_mailto(lead["business_name"]),
    }
    # Pure-CSS hero: a per-business gradient (stable across re-renders)
    # used by the classic templates' hero section inline style.
    gradient_a, gradient_b = hero_gradient(lead["business_name"])
    context["hero_gradient_a"] = gradient_a
    context["hero_gradient_b"] = gradient_b
    # Populate "What We Offer" from the business's real Places types when any
    # map to a service; otherwise leave services_list unset so each template's
    # own niche-specific default (a nicer, tailored list) renders instead.
    services = get_services_from_types(_place_types(lead))
    if services != ["General Service"]:
        context["services_list"] = ", ".join(sorted(services))
    return context


def render_template_files(niche: str, context: dict) -> dict[str, str]:
    """Render index.html and style.css for `niche` with Jinja2. Returns
    {relative_path: rendered_content} ready to push to GitHub / Vercel."""
    niche_dir = TEMPLATES_DIR / niche
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
    template_niche = resolve_template_niche(niche)
    if template_niche is None:
        db.update_lead_status(
            lead["id"],
            "lost",
            notes=f"No website template for niche '{niche}' and no 'default' fallback template",
        )
        return None

    context = build_context(lead)
    files = render_template_files(template_niche, context)

    _repo, repo_url, repo_full_name = github_api.create_repo_with_files(
        lead["business_name"], lead["id"], files
    )
    deployment = vercel_api.deploy_files(
        github_api.make_repo_name(lead["business_name"], lead["id"]), files
    )

    screenshot_url, screenshot_path = _capture_and_publish_screenshot(lead["id"], deployment["url"])

    website_id = db.insert_website(
        lead_id=lead["id"],
        template_niche=template_niche,
        repo_url=repo_url,
        repo_full_name=repo_full_name,
        preview_url=deployment["url"],
        vercel_project_id=deployment["deployment_id"],
        screenshot_url=screenshot_url,
        screenshot_path=screenshot_path,
    )
    # No real photos on the preview yet (gradient hero + placeholder review
    # sections), so record a note the SalesAgent surfaces in the cold email,
    # plus the AI/fallback copy so the dashboard can show what the preview
    # actually says.
    db.update_lead_fields(
        lead["id"],
        image_note=image_placeholder.PLACEHOLDER_EMAIL_NOTE,
        ai_headline=context["ai_headline"],
        ai_about=context["ai_about"],
    )
    db.update_lead_status(lead["id"], "designed", notes=f"Preview deployed: {deployment['url']}")
    return db.get_website_by_lead(lead["id"]) if website_id else None


def run() -> None:
    """Main entrypoint called by main.py: design a site for every
    'researched' lead that has a matching template."""
    for lead in db.list_leads_by_status("researched"):
        try:
            process_lead(lead)
        except Exception as exc:  # noqa: BLE001 - one bad lead must not kill the batch
            db.log_state_history(lead["id"], "researched", "researched", notes=f"Design failed: {exc}")
