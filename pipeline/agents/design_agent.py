"""DesignAgent: renders a niche template for a lead, pushes it to a new
private GitHub repo, deploys it to Vercel, and records the resulting
preview URL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from utils import db, github_api, tracer, vercel_api

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
TEMPLATE_FILES = ("index.html", "style.css")

_PLACEHOLDER_IMAGE_BASE = "https://picsum.photos/seed"


def available_niches() -> set[str]:
    if not TEMPLATES_DIR.exists():
        return set()
    return {p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()}


def get_hero_image_url(niche: str) -> str:
    """Fetch a relevant free stock photo URL for the niche.

    Uses Unsplash's search API if UNSPLASH_ACCESS_KEY is configured;
    otherwise falls back to a deterministic (seeded) placeholder image
    service that needs no API key, so DesignAgent works out of the box.
    """
    if config.UNSPLASH_ACCESS_KEY:
        try:
            resp = requests.get(
                "https://api.unsplash.com/photos/random",
                params={"query": niche, "orientation": "landscape"},
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
    return {
        "business_name": lead["business_name"],
        "phone": lead.get("phone") or "Call us",
        "location": lead.get("location") or "",
        "pain_point_solution": _pain_point_solution(lead),
        "testimonial": _testimonial(lead),
        "hero_image_url": get_hero_image_url(lead["niche"]),
        "year": datetime.now(timezone.utc).year,
    }


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
    if niche not in available_niches():
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

    website_id = db.insert_website(
        lead_id=lead["id"],
        template_niche=niche,
        repo_url=repo_url,
        repo_full_name=repo_full_name,
        preview_url=deployment["url"],
        vercel_project_id=deployment["deployment_id"],
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
