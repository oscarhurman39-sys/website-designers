"""OnboardingAgent: automates the post-payment handoff that main.py's
`transfer` console command otherwise does entirely interactively --
creating the hand-off repo, inviting the client as a GitHub/Vercel
collaborator, and (only if explicitly opted into via
config.AUTO_REMOVE_GITHUB_ACCESS) removing our own GitHub access to
complete a fully hands-off transfer.

Granting access (GitHub/Vercel collaborator invites) is safe to automate
unconditionally: both are invite-ACCEPTANCE flows the invitee must
approve, not unilateral access grants -- a typo'd username just means an
invite nobody accepts, not a security problem. Removing OUR OWN access is
the one irreversible, consequential step here, so it stays behind an
explicit config flag defaulting to False -- see config.py's comment on
why that default matters.

Two entry points:
  - send_onboarding_email() -- called from webhook_server.py right after
    a Stripe payment completes, emailing the client a link to
    GET /onboard/<lead_id> (auth: utils/editor_auth.py, same DB-backed
    session as the content editor).
  - complete_onboarding() -- called when the client submits that form.

main.py's `transfer` console command remains available throughout as a
manual, operator-triggered fallback/override -- both paths converge on
the same idempotent primitives (design_agent.create_handoff_repo,
github_api.invite_collaborator, vercel_api.invite_collaborator), so
nothing breaks if a client never uses the automated form and the operator
just runs `transfer` instead, or does both.
"""
from __future__ import annotations

from typing import Any

import config
from agents import design_agent, editor_agent
from utils import db, editor_auth, email_utils, github_api, vercel_api


def send_onboarding_email(lead_id: int) -> bool:
    """Email the client a link to claim their website, right after
    payment. Returns False (without raising) if there's no contact email
    on file or the send fails."""
    lead = db.get_lead(lead_id)
    if lead is None or not lead.get("contact_email"):
        return False
    to_addr = lead["contact_email"]

    link = editor_auth.create_onboarding_link(lead_id)
    subject = f"Let's get {lead['business_name']} set up on your own accounts"
    body = (
        "Thanks for your payment! One quick step to finish handing your "
        f"website over to you:\n\n{link}\n\n"
        "That link lets you (optionally) give us your GitHub username and "
        "confirm an email for site access -- takes under a minute. If "
        "you'd rather we just handle it, reply to this email instead."
    )
    try:
        message_id = email_utils.send_email(to_addr=to_addr, subject=subject, body_text=body, lead_id=lead_id)
        db.insert_email_thread(
            lead_id=lead_id, direction="outbound", subject=subject, body=body,
            from_addr=config.EMAIL_USER, to_addr=to_addr, message_id=message_id,
        )
    except RuntimeError as exc:  # noqa: BLE001 - e.g. previously unsubscribed
        print(f"[onboarding_agent] Could not email onboarding link to lead {lead_id}: {exc}")
        return False
    return True


def complete_onboarding(lead_id: int, github_username: str, vercel_email: str) -> dict[str, Any]:
    """Run every automatable step of handoff for one lead: create the
    repo if needed, invite the client to GitHub/Vercel, and (config-
    gated) remove our own GitHub access, then email them the editor link.
    Each step is independently best-effort -- one failure doesn't block
    the rest, since a client who typo'd their GitHub username should
    still get their Vercel invite and editor link.

    Returns {"message": str} -- a human-readable summary of what
    happened, meant to be shown directly on the onboarding page.
    """
    lead = db.get_lead(lead_id)
    website = db.get_website_by_lead(lead_id)
    if lead is None or website is None:
        raise RuntimeError(f"No lead/website found for lead {lead_id}")

    messages: list[str] = []

    github_username = (github_username or "").strip()
    if github_username:
        try:
            if not website.get("repo_full_name"):
                design_agent.create_handoff_repo(lead_id)
                website = db.get_website_by_lead(lead_id)
            github_api.invite_collaborator(website["repo_full_name"], github_username, permission="admin")
            messages.append(
                f"Invited {github_username} to your private GitHub repo -- "
                "check your GitHub notifications to accept."
            )
        except Exception as exc:  # noqa: BLE001 - one failed step must not block the rest of onboarding
            messages.append(f"Could not invite {github_username} to GitHub: {exc}")

    vercel_email = (vercel_email or "").strip()
    if vercel_email and config.VERCEL_TEAM_ID:
        try:
            project_name = github_api.make_repo_name(lead["business_name"], lead_id)
            vercel_api.invite_collaborator(project_name, vercel_email)
            messages.append(f"Invited {vercel_email} to the Vercel project -- check your email to accept.")
        except Exception as exc:  # noqa: BLE001
            messages.append(f"Could not invite {vercel_email} to Vercel: {exc}")

    if config.AUTO_REMOVE_GITHUB_ACCESS and website.get("repo_full_name") and not website.get("transferred"):
        try:
            own_username = github_api.get_authenticated_username()
            github_api.remove_collaborator(website["repo_full_name"], own_username)
            db.mark_website_transferred(lead_id)
            messages.append("Your website is now fully yours -- we've removed our own access to the repo.")
        except Exception as exc:  # noqa: BLE001
            messages.append(f"Could not finish removing our own access automatically: {exc}")

    if editor_agent.send_editor_link(lead_id):
        messages.append("We've also emailed you a link to edit your site's text, photos, and hours any time.")

    if not messages:
        messages.append(
            "Got it -- since you didn't provide a GitHub username, we'll keep "
            "managing the site for you. Reach out any time if that changes."
        )

    return {"message": " ".join(messages)}
