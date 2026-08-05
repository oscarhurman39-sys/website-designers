"""Per-offer implementations of the build/price/fulfill contract every
offer plugs into the shared cold-email -> checkout -> fulfill loop
(sales_agent.py, stripe_utils.py, webhook_server.py). See base.py for the
contract, website.py for the (only, today) implementation, registry.py
for get_offer(). See PLATFORM.md for the full generalization plan this is
step 2 of."""
from __future__ import annotations

from agents.offers.registry import get_offer

__all__ = ["get_offer"]
