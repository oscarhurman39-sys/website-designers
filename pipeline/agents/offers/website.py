"""The 'website' offer -- today's only offer, implementing the contract in
offers/base.py as thin delegation to the existing, unmodified
design_agent.py / onboarding_agent.py / config.py. Nothing here is new
logic; this module exists so main.py and webhook_server.py can go through
agents.offers.get_offer(lead) instead of importing design_agent /
onboarding_agent directly -- see PLATFORM.md."""
from __future__ import annotations

from typing import Any, Optional

import config
from agents import design_agent, onboarding_agent


def build_artifact(lead: dict) -> Optional[dict]:
    return design_agent.process_lead(lead)


def price(lead: dict) -> int:
    # Flat price today (`lead` unused) -- per-lead/tiered pricing is a real
    # future offer decision, not something to stub in speculatively here.
    return config.WEBSITE_PRICE_USD


def fulfill(lead_id: int, **kwargs: Any) -> dict[str, Any]:
    return onboarding_agent.complete_onboarding(
        lead_id, kwargs.get("github_username", ""), kwargs.get("vercel_email", "")
    )
