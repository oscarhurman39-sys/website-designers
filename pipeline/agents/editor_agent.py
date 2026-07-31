"""EditorAgent: lets a client edit a WHITELISTED subset of their site's
content -- text, photos, opening hours, services/menu, reviews, logo --
through the magic-link-authenticated flow in webhook_server.py's /edit
routes (auth: utils/editor_auth.py, a DB-backed session -- NOT the
stateless HMAC pattern used for unsubscribe/click links, because this one
grants actual publishing authority over a paying client's live site).

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

URL fields (`logo_url`, `photos`) are validated with utils/ssrf_guard at
submission time -- a client could otherwise point a "photo" at an
internal address, which the readiness scanner's own link crawl would then
dutifully fetch on the server's behalf.

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
from utils import db, editor_auth, email_utils, github_api, site_audit, ssrf_guard, url_safety, vercel_api

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

# Fields that hold a URL a client submitted directly -- validated with
# ssrf_guard at edit time (not just later when something crawls them).
_URL_FIELDS = ("logo_url", "photos")

FIELD_LABELS: dict[str, str] = {
    "phone": "Phone number",
    "location": "Location",
    "logo_url": "Logo image URL",
    "hours": "Opening hours",
    "scraped_services": "Services / menu items",
    "reviews": "Reviews",
    "photos": "Photos (image URLs)",
}


def _validate_edit(field: str, value: Any) -> None:
    """Raises ValueError (or its subclass ssrf_guard.BlockedURLError for a
    disallowed URL) for anything that shouldn't be persisted -- defense in
    depth even though the web form itself only ever submits whitelisted
    fields with browser-side formatting."""
    field_type = EDITABLE_FIELDS.get(field)
    if field_type is None:
        raise ValueError(f"'{field}' is not an editable field.")
    if field_type == "list" and not isinstance(value, list):
        raise ValueError(f"'{field}' must be a list, got {type(value).__name__}.")
    if field_type == "text" and not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string, got {type(value).__name__}.")

    if field in _URL_FIELDS:
        urls = value if isinstance(value, list) else ([value] if value else [])
        for url in urls:
            ssrf_guard.validate_submittable_url(url)


def apply_edit(lead_id: int, field: str, value: Any) -> None:
    """Validate and persist one edit."""
    _validate_edit(field, value)
    db.upsert_site_edit(lead_id, field, value)


def apply_edits(lead_id: int, edits: dict[str, Any]) -> None:
    """Validate EVERY field in `edits` before persisting ANY of them -- a
    single bad field (e.g. a blocked photo URL) must not leave a partial,
    confusing save behind for the fields that happened to be validated
    first (dict iteration order would otherwise make persistence depend on
    which field the client happened to list last)."""
    for field, value in edits.items():
        _validate_edit(field, value)
    for field, value in edits.items():
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
    If a GitHub hand-off repo exists but full transfer hasn't happened yet
    (repo_full_name set, website.transferred still 0 -- we still have
    push access), also updates the files there so the repo doesn't fall
    behind Vercel.

    Once website.transferred is set (the operator explicitly removed
    their own GitHub access as part of `transfer` -- see main.py), this
    refuses to publish at all: proceeding would redeploy Vercel from a
    newer render than the GitHub repo the client now believes IS their
    website, silently creating two diverging copies with no way to tell
    which one is "real." This is the "true handoff" model -- editing
    through this tool stops at that point; see README.md's "Client
    editor" section for the two alternative architectures (fully-managed,
    or a client-connected editor that commits to the client's repo first
    and lets Vercel deploy from that commit) if continuous self-service
    editing after a full handoff is wanted later.

    Returns {"preview_url": str, "repo_updated": bool}.
    """
    lead = db.get_lead(lead_id)
    website = db.get_website_by_lead(lead_id)
    if lead is None or website is None:
        raise RuntimeError(f"No lead/website found for lead {lead_id}")

    if website.get("transferred"):
        raise RuntimeError(
            "This site has been fully handed off to the client (GitHub access was "
            "removed at transfer) -- further edits must go through the client's own "
            "GitHub repo, not this editor. Publishing here would leave Vercel and "
            "GitHub as two diverging copies of the site."
        )

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
        # transferred is guaranteed falsy here (checked above), so we
        # still hold GitHub access -- this should reliably succeed; the
        # try/except is for transient GitHub API failures, not "we might
        # not have access anymore" (that case is the guard clause above).
        try:
            repo = github_api._get_client().get_repo(website["repo_full_name"])
            github_api.update_or_create_files(repo, files, commit_message="Published edit via client editor")
            repo_updated = True
        except Exception as exc:  # noqa: BLE001 - a transient GitHub failure must not lose the Vercel republish
            print(f"[editor_agent] Could not push edit to {website['repo_full_name']}: {exc}")

    try:
        result = site_audit.audit_readiness(files["index.html"], preview_url)
        db.insert_site_audit(lead_id, "generated_preview", result, url=preview_url)
    except Exception as exc:  # noqa: BLE001 - readiness audit is an enrichment, never a blocker
        print(f"[editor_agent] Readiness audit failed for lead {lead_id}: {exc}")

    return {"preview_url": preview_url, "repo_updated": repo_updated}


def request_new_editor_link(lead_id: int, submitted_email: str) -> bool:
    """Issue and email a fresh editor link to a lead's ON-FILE contact
    email. Returns True iff the submitted address matched (case-
    insensitively) and an email was sent -- an HTTP handler for this MUST
    show the identical response either way ("if that email is on file,
    a new link is on its way") regardless of the return value, or this
    endpoint becomes an oracle for enumerating valid emails per lead_id,
    or worse, a way to get a working link without controlling that inbox
    at all.

    Known simplification: if the lead's email previously unsubscribed
    from cold-outreach email (db.is_unsubscribed), email_utils.send_email
    refuses to send at all -- there's no separate transactional-vs-
    marketing send path in this pipeline yet. That failure is caught and
    treated as "could not deliver" here rather than raised.
    """
    lead = db.get_lead(lead_id)
    if lead is None:
        return False
    on_file = (lead.get("contact_email") or "").strip().lower()
    if not on_file or on_file != (submitted_email or "").strip().lower():
        return False

    link = editor_auth.create_editor_link(lead_id)
    try:
        email_utils.send_email(
            to_addr=on_file,
            subject=f"Your website editing link -- {lead['business_name']}",
            body_text=(
                f"Here's a fresh link to edit your website:\n\n{link}\n\n"
                f"This link expires in {editor_auth.SESSION_LIFETIME_DAYS} days. "
                "If you didn't request this, you can safely ignore this email."
            ),
            lead_id=lead_id,
        )
    except RuntimeError as exc:  # noqa: BLE001 - e.g. previously unsubscribed; see docstring
        print(f"[editor_agent] Could not email a new editor link to lead {lead_id}: {exc}")
        return False
    return True
