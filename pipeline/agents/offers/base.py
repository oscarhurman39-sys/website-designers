"""The contract every agents/offers/<name>.py module implements.

An "offer" is whatever gets pre-built and sold through the existing
cold-email -> self-serve-checkout -> auto-fulfill loop (sales_agent.py,
stripe_utils.py, webhook_server.py's /buy and /onboard routes) -- websites
today, something else later. Those three pieces are already offer-agnostic
(sales_agent.py drafts from the lead's own scraped facts -- business_name,
niche, pain_point, site_audit -- never anything website-specific); only
what gets built, what it costs, and how it's handed over differ per offer.
See PLATFORM.md for the full generalization plan this is step 2 of.

An offer is any object exposing the three callables below with these
signatures -- in practice a plain module (agents/offers/website.py today),
the same "module as namespace" pattern templates/<niche>/ already uses for
per-niche render logic. No class or instantiation needed:

    build_artifact(lead: dict) -> Optional[dict]
        Build and persist whatever this offer sells for `lead` (e.g. a
        deployed website preview). Returns the persisted record, or None
        if this lead can't be served.

    price(lead: dict) -> int
        Price in whole USD to charge `lead` for this offer.

    fulfill(lead_id: int, **kwargs) -> dict
        Run this offer's post-payment hand-off for `lead_id`. Offer-
        specific inputs (e.g. a GitHub username) come through kwargs.
        Returns a result dict that must include a 'message' key
        describing the outcome to show the client.

validate_offer() checks a module actually implements this at registry
build time (agents/offers/registry.py), so a broken offer module fails
loudly at import instead of on the first real lead that hits it.
"""
from __future__ import annotations

from types import ModuleType

REQUIRED_OFFER_ATTRS = ("build_artifact", "price", "fulfill")


def validate_offer(module: ModuleType) -> None:
    missing = [name for name in REQUIRED_OFFER_ATTRS if not callable(getattr(module, name, None))]
    if missing:
        raise AttributeError(f"{module.__name__} is missing offer method(s): {', '.join(missing)}")
