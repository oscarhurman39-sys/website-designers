"""EditorAgent: lets a client edit a WHITELISTED subset of their site's
content -- text, photos, opening hours, services/menu, reviews, logo --
through the magic-link-authenticated flow in webhook_server.py's /edit
routes (auth: utils/editor_auth.py).

Everything NOT in EDITABLE_FIELDS stays entirely designer-controlled:
spacing, typography, colour hierarchy (brand_colors), mobile layout,
navigation structure, and component styling have no field here at all, and
publish() only ever re-renders the SAME niche template the lead already
has (design_agent.render_template_files(website["template_niche"], ...))
-- there is no way to change which template/niche a lead uses through this
module. `business_name` is also deliberately excluded even though it's
"just text": it's baked into this lead's stable Vercel project name and
GitHub repo name (see github_api.make_repo_name), so editing it here would
silently redeploy to a brand-new project instead of updating the existing
one.

Edits are stored as `site_edits` rows (utils/db.py), layered on top of the
lead's original researched/imported content by effective_lead() -- so
design_agent.build_context() and utils/content_importer.load_content()
need no changes at all; they just see a lead dict that happens to have
edited values in it.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from agents import design_agent
from utils import db, github_api, site_audit, url_safety, vercel_api

# field -> "text" (a single string) or "list" (a JSON list, one item per
# line in the edit form) -- drives both form rendering and validation.
EDITABLE_FIELDS: dict[str, str] = {
    "phone": "text",
    "location": "text",
    "logo_url": "text",
    "hours": "list",
    "scraped_services": "list",
    "reviews": "list",
    "photos": "list",
}

FIELD_LABELS: dict[str, str] = {
    "phone": "Phone number",
    "location": "Location",
    "logo_url": "Logo image URL",
    "hours": "Opening hours",
    "scraped_services": "Services / menu items",
    "reviews": "Reviews",
    "photos": "Photos (image URLs)",
}


def apply_edit(lead_id: int, field: str, value: Any) -> None:
    """Validate and persist one edit. Raises ValueError for any field not
    in EDITABLE_FIELDS -- defense in depth even though the web form itself
    only ever submits whitelisted fields."""
    field_type = EDITABLE_FIELDS.get(field)
    if field_type is None:
        raise ValueError(f"'{field}' is not an editable field.")
    if field_type == "list" and not isinstance(value, list):
        raise ValueError(f"'{field}' must be a list, got {type(value).__name__}.")
    if field_type == "text" and not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string, got {type(value).__name__}.")
    db.upsert_site_edit(lead_id, field, value)


def effective_lead(lead_id: int) -> Optional[dict[str, Any]]:
    """The lead row with any saved edits layered on top -- what
    design_agent.build_context() should actually render. Re-encodes list
    edits back to the same JSON-string shape the `leads` table itself
    uses, so every downstream consumer (content_importer.load_content(),
    build_context()) needs no awareness that an edit ever happened. None
    if the lead doesn't exist."""
    lead = db.get_lead(lead_id)
    if lead is None:
        return None
    effective = dict(lead)
    for field, value in db.get_site_edits(lead_id).items():
        if EDITABLE_FIELDS.get(field) == "list":
            effective[field] = json.dumps(value) if value else ""
        else:
            effective[field] = value
    return effective


def publish(lead_id: int) -> dict[str, Any]:
    """Re-render this lead's site with any saved edits layered in, and
    redeploy it to the SAME Vercel project it has always lived at (never a
    new one -- see the module docstring on why business_name is locked).
    If the site has already been handed off (a GitHub repo exists), also
    best-effort pushes the updated files there: this can fail if the
    operator already removed their own GitHub access as part of transfer
    (main.py's `transfer` command) -- that failure is caught and reported,
    not raised, since the Vercel republish above already succeeded and
    must not be lost.

    Returns {"preview_url": str, "repo_updated": bool}.
    """
    lead = db.get_lead(lead_id)
    website = db.get_website_by_lead(lead_id)
    if lead is None or website is None:
        raise RuntimeError(f"No lead/website found for lead {lead_id}")

    edited_lead = effective_lead(lead_id)
    context = design_agent.build_context(edited_lead)
    files = design_agent.render_template_files(website["template_niche"], context)

    deployment = vercel_api.deploy_files(
        github_api.make_repo_name(lead["business_name"], lead_id), files
    )
    preview_url = url_safety.validate_public_url(deployment.get("url") or "")
    db.update_website_preview_url(lead_id, preview_url)
    # Keep the on-disk copy in sync too, so a later create_handoff_repo()
    # (or another publish()) picks up this edit rather than a stale render.
    design_agent._save_rendered_files(lead_id, files)

    repo_updated = False
    if website.get("repo_full_name"):
        try:
            repo = github_api._get_client().get_repo(website["repo_full_name"])
            github_api.update_or_create_files(repo, files, commit_message="Published edit via client editor")
            repo_updated = True
        except Exception as exc:  # noqa: BLE001 - best-effort: GitHub access may have been removed at handoff
            print(f"[editor_agent] Could not push edit to {website['repo_full_name']}: {exc}")

    try:
        result = site_audit.audit_readiness(files["index.html"], preview_url)
        target = "live_client_site" if website.get("transferred") else "generated_preview"
        db.insert_site_audit(lead_id, target, result, url=preview_url)
    except Exception as exc:  # noqa: BLE001 - readiness audit is an enrichment, never a blocker
        print(f"[editor_agent] Readiness audit failed for lead {lead_id}: {exc}")

    return {"preview_url": preview_url, "repo_updated": repo_updated}
