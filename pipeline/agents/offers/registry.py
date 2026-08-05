"""Resolves a lead to the offer module that sells to it (see base.py for
the contract every offer implements)."""
from __future__ import annotations

from agents.offers import base, website

base.validate_offer(website)

# Every lead resolves to the 'website' offer today -- there is only one.
# This is the one seam PLATFORM.md's step 3 (an `offer_id` column on
# `leads`) changes: get_offer(lead) starts branching on lead['offer_id']
# instead of returning a constant, and no caller of get_offer() needs to
# change when that happens.
OFFERS = {"website": website}


def get_offer(lead: dict):
    return OFFERS["website"]
