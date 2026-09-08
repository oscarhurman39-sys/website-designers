"""DesignAgent: renders a niche template for a lead, pushes it to a new
private GitHub repo, deploys it to Vercel, and records the resulting
preview URL.
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urlparse

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from utils import assets, db, github_api, screenshot, tracer, tracker, vercel_api

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
TEMPLATE_FILES = ("index.html", "style.css")
DEFAULT_NICHE = "default"

_PLACEHOLDER_IMAGE_BASE = "https://picsum.photos/seed"
_DEPLOYMENT_VALIDATION_TIMEOUT_SECONDS = 15
_AUTH_URL_PARTS = ("login", "signin", "sign-in", "auth", "authentication")
_AUTH_PAGE_MARKERS = (
    "vercel authentication",
    "log in to vercel",
    "login to vercel",
    "sign in to vercel",
)

# The single photo-led template every niche renders through by default
# (templates/modern). The older per-niche folders are kept and selectable
# with DESIGN_TEMPLATE_STYLE=legacy, but they are text-only and were the
# reason previews looked like templates rather than websites.
MODERN_TEMPLATE = "modern"

# Minimum Google review count before the rating is shown on the page. A
# 5.0 from three reviews reads as thin; from ten it reads as a real business.
MIN_GOOGLE_REVIEWS_FOR_BADGE = 10


def _unsplash(photo_id: str, width: int = 1600, height: int = 900) -> str:
    """Direct Unsplash CDN URL for a hand-picked photo. Hotlinking these is
    what Unsplash's licence expects, and unlike the random-photo API it needs
    no key and never surprises you with an off-topic picture."""
    return f"https://images.unsplash.com/photo-{photo_id}?auto=format&fit=crop&w={width}&h={height}&q=75"


# Per-niche look and copy. Every photo id below was checked by eye. `{city}`
# and `{name}` are filled from the lead. Services are (name, one-line blurb).
NICHE_THEMES: dict[str, dict] = {
    "plumber": {
        "label": "Plumbing & Heating", "icon": "🔧", "accent": "#1d4ed8", "accent_dark": "#1e3a8a",
        "services_heading": "What we do", "cta_secondary": "Get a quote", "trust_line": "Emergency callouts available",
        "tagline": "Keeping {city} homes warm and watertight",
        "about": "{name} is a plumbing and heating business serving {city} and the surrounding area. From a dripping tap to a full bathroom refit or a boiler that has given up on a Sunday night, the job is done properly, left tidy, and priced fairly.",
        "hero": "1542013936693-884638332954", "gallery": ["1584622650111-993a426fbf0a", "1607472586893-edb57bdc0e39"],
        "services": [("Emergency callouts", "Burst pipes, leaks and no hot water, sorted fast."),
                     ("Boiler repairs & servicing", "Annual services and breakdown repairs."),
                     ("Bathroom fitting", "Full installs from first fix to final tile."),
                     ("Radiators & heating", "New radiators, power flushing, thermostats."),
                     ("Leak detection", "Find and fix leaks without tearing the house apart."),
                     ("Blocked drains", "Sinks, showers and outside drains cleared.")],
        "nav": ["Services", "About", "Reviews", "Contact"],
    },
    "electrician": {
        "label": "Electrical Services", "icon": "⚡", "accent": "#b45309", "accent_dark": "#78350f",
        "services_heading": "What we do", "cta_secondary": "Get a quote", "trust_line": "Certified & insured work",
        "tagline": "Safe, certified electrical work across {city}",
        "about": "{name} is a local electrician covering {city} and nearby. Whether it is a fuse box upgrade, a full rewire or a single socket that has stopped working, every job is done to current regulations and certified.",
        "hero": "1621905251189-08b45d6a269e", "gallery": ["1621905252507-b35492cc74b4", "1555963966-b7ae5404b6ed"],
        "services": [("Fuse box upgrades", "Modern consumer units with RCD protection."),
                     ("Rewiring", "Partial or full rewires with minimal disruption."),
                     ("Lighting", "Indoor, outdoor and garden lighting design and install."),
                     ("EV charger installation", "Home charging points fitted and certified."),
                     ("Fault finding", "Tripping circuits and dead sockets traced and fixed."),
                     ("Safety certificates", "EICR reports for landlords and home sales.")],
        "nav": ["Services", "About", "Reviews", "Contact"],
    },
    "landscaper": {
        "label": "Landscaping & Garden Services", "icon": "🌿", "accent": "#15803d", "accent_dark": "#14532d",
        "services_heading": "What we do", "cta_secondary": "Get a quote", "trust_line": "Design, build & maintain",
        "tagline": "Beautiful gardens, built to last, in {city}",
        "about": "{name} designs, builds and maintains gardens in {city} and the surrounding villages. From a tidy weekly cut to patios, fencing and complete garden makeovers, the work is done with care and a proper finish.",
        "hero": "1558904541-efa843a96f01", "gallery": ["1600585154340-be6161a56a0c", "1416879595882-3373a0480b5b"],
        "services": [("Garden design", "Practical, good-looking plans for any size of plot."),
                     ("Patios & paving", "Porcelain, sandstone and block paving laid properly."),
                     ("Fencing & decking", "Boundaries and outdoor living spaces."),
                     ("Lawn care", "Turfing, seeding, treatments and regular mowing."),
                     ("Planting & borders", "Seasonal planting and low-maintenance schemes."),
                     ("Hedge & tree work", "Trimming, reductions and removals.")],
        "nav": ["Services", "Projects", "Reviews", "Contact"],
    },
    "cafe": {
        "label": "Cafe", "icon": "☕", "accent": "#b45309", "accent_dark": "#7c2d12",
        "services_heading": "What we serve", "cta_secondary": "Find us", "trust_line": "Coffee, brunch & lunch",
        "tagline": "Good coffee and a warm welcome in {city}",
        "about": "{name} is an independent cafe in the heart of {city}. Freshly ground coffee, homemade cakes and a proper breakfast, served by people who remember your order.",
        "hero": "1554118811-1e0d58224f24", "gallery": ["1495474472287-4d71bcdd2085", "1554118811-1e0d58224f24"],
        "services": [("Specialty coffee", "Espresso, flat whites and filter from a local roaster."),
                     ("Breakfast & brunch", "Cooked breakfasts, eggs, pancakes and pastries."),
                     ("Lunch", "Sandwiches, soups and salads made fresh each morning."),
                     ("Cakes & bakes", "Baked in-house daily."),
                     ("Takeaway", "Everything on the menu, to go."),
                     ("Dog friendly", "Water bowls and treats for four-legged regulars.")],
        "nav": ["Menu", "About", "Reviews", "Find us"],
    },
    "salon": {
        "label": "Hair & Beauty", "icon": "✂️", "accent": "#be185d", "accent_dark": "#831843",
        "services_heading": "Our services", "cta_secondary": "Book an appointment", "trust_line": "Walk-ins & appointments",
        "tagline": "Look and feel your best, right here in {city}",
        "about": "{name} is a friendly, modern salon in {city}. Precision cuts, colour that lasts and treatments that make an afternoon feel like a holiday, in a space designed to help you relax.",
        "hero": "1560066984-138dadb4c035", "gallery": ["1522337660859-02fbefca4702", "1560066984-138dadb4c035"],
        "services": [("Cut & finish", "Consultation-led cuts for every hair type."),
                     ("Colour", "Balayage, highlights, tints and colour correction."),
                     ("Treatments", "Keratin, conditioning and scalp treatments."),
                     ("Blow dry & styling", "Event-ready hair, any day of the week."),
                     ("Nails", "Manicures, pedicures and gels."),
                     ("Bridal & occasions", "Trials and on-the-day styling.")],
        "nav": ["Services", "About", "Reviews", "Book"],
    },
    "dentist": {
        "label": "Dental Practice", "icon": "🦷", "accent": "#0e7490", "accent_dark": "#164e63",
        "services_heading": "Treatments", "cta_secondary": "Book an appointment", "trust_line": "New patients welcome",
        "tagline": "Gentle, modern dentistry for families in {city}",
        "about": "{name} is a dental practice in {city} welcoming new patients. Routine check-ups, hygiene visits and cosmetic treatments in a calm, modern surgery, with plenty of time to explain your options.",
        "hero": "1606811841689-23dfddce3e95", "gallery": ["1629909613654-28e377c37b09", "1606811841689-23dfddce3e95"],
        "services": [("Check-ups & hygiene", "Routine care that keeps small problems small."),
                     ("Fillings & crowns", "Natural-looking restorations."),
                     ("Teeth whitening", "Safe, professional whitening."),
                     ("Invisible aligners", "Straighter teeth without metal braces."),
                     ("Emergency appointments", "Toothache seen the same day where possible."),
                     ("Nervous patients", "Extra time and a gentle approach.")],
        "nav": ["Treatments", "About", "Reviews", "Contact"],
    },
    "gym": {
        "label": "Gym & Fitness", "icon": "🏋️", "accent": "#dc2626", "accent_dark": "#7f1d1d",
        "services_heading": "Facilities & classes", "cta_secondary": "Join today", "trust_line": "Monthly memberships",
        "tagline": "Train harder, feel better, in {city}",
        "about": "{name} is an independent gym in {city} for people who want results without the corporate feel. Proper free weights, modern cardio, classes and coaches who know your name.",
        "hero": "1534438327276-14e5300c3a48", "gallery": ["1571902943202-507ec2618e8f", "1534438327276-14e5300c3a48"],
        "services": [("Free weights & racks", "Squat racks, platforms and dumbbells to 50kg."),
                     ("Classes", "HIIT, strength, spin and mobility every day."),
                     ("Personal training", "One-to-one coaching and programmes."),
                     ("Cardio zone", "Rowers, bikes, treadmills and skiergs."),
                     ("Flexible memberships", "Monthly, no long contracts."),
                     ("Open early & late", "Fit training around your day.")],
        "nav": ["Facilities", "About", "Reviews", "Join"],
    },
    "restaurant": {
        "label": "Restaurant", "icon": "🍽️", "accent": "#9f1239", "accent_dark": "#4c0519",
        "services_heading": "On the menu", "cta_secondary": "Book a table", "trust_line": "Lunch & dinner",
        "tagline": "Honest, seasonal cooking in {city}",
        "about": "{name} is a neighbourhood restaurant in {city}. A short menu that changes with the seasons, good wine, and the kind of service that makes a Tuesday feel like an occasion.",
        "hero": "1517248135467-4c7edcad34c4", "gallery": ["1414235077428-338989a2e8c0", "1517248135467-4c7edcad34c4"],
        "services": [("Lunch & dinner", "Open six days a week."),
                     ("Sunday roast", "Booking recommended."),
                     ("Private dining", "Groups and celebrations catered for."),
                     ("Vegetarian & vegan", "Proper options, not afterthoughts."),
                     ("Wine list", "Small producers, fairly priced."),
                     ("Takeaway", "Order ahead and collect.")],
        "nav": ["Menu", "About", "Reviews", "Book"],
    },
    "vehicle-repair": {
        "label": "Vehicle Repair", "icon": "🚗", "accent": "#1e3a8a", "accent_dark": "#0f172a",
        "services_heading": "What we do", "cta_secondary": "Get a quote", "trust_line": "All makes & models",
        "tagline": "Keeping {city} drivers on the road",
        "about": "{name} is an independent garage serving {city}. MOTs, servicing and repairs on all makes and models, with honest advice and no work done that you have not agreed to.",
        "hero": "1625047509248-ec889cbff17f", "gallery": ["1486262715619-67b85e0b08d3", "1625047509248-ec889cbff17f"],
        "services": [("MOT & servicing", "Manufacturer-schedule servicing on all makes."),
                     ("Diagnostics", "Dashboard warning lights read and fixed."),
                     ("Brakes & clutches", "Pads, discs, clutch replacement."),
                     ("Tyres & tracking", "Supplied, fitted and aligned."),
                     ("Air conditioning", "Re-gas and repairs."),
                     ("Batteries & electrics", "Tested and replaced while you wait.")],
        "nav": ["Services", "About", "Reviews", "Contact"],
    },
}
DEFAULT_THEME: dict = {
    "label": "Local Business", "icon": "★", "accent": "#0f766e", "accent_dark": "#134e4a",
    "tagline": "Serving {city} with pride",
    "about": "{name} is a local business based in {city}. Friendly, reliable service from people who care about doing the job well.",
    "hero": "1497366216548-37526070297c", "gallery": ["1497366216548-37526070297c"],
    "services": [],
    "nav": ["Services", "About", "Reviews", "Contact"],
}

# --- Per-business accent ------------------------------------------------------
# Every business in a niche used to render byte-identical colours, which is what
# made a run of previews look like one template with the names swapped. The
# niche theme's accent is now the *base* hue: each business gets a small,
# deterministic rotation around it derived from its name alone. Same name ->
# same colour, on every machine and on every rebuild (hashlib, never Python's
# hash(), which is salted per process). No network call, no stored column.
ACCENT_HUE_SHIFT_DEG = 12        # max +/- rotation; a plumber stays blue
ACCENT_SAT_SHIFT = 10            # max +/- saturation, percentage points
ACCENT_LIGHT_SHIFT = 6           # max +/- lightness, percentage points
ACCENT_SAT_RANGE = (0.55, 0.92)  # never muddy, never neon
ACCENT_MIN_LIGHTNESS = 0.12
# accent is BOTH a white-text background and text on white in the modern
# template, so one number governs both: WCAG AA for normal text on white.
ACCENT_MIN_CONTRAST_ON_WHITE = 4.5
ACCENT_DARK_MIN_DROP = 0.05      # accent_dark must stay visibly darker
ACCENT_DARK_MIN_LIGHTNESS = 0.08
_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")


def _hex_to_rgb(value: str) -> tuple:
    v = value.lstrip("#")
    return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in rgb)


def _relative_luminance(rgb) -> float:
    """WCAG 2.1 relative luminance."""
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_with_white(rgb) -> float:
    """WCAG contrast ratio against #ffffff."""
    return 1.05 / (_relative_luminance(rgb) + 0.05)


def _business_seed(business_name: str) -> Optional[bytes]:
    """Stable digest of a business name. Whitespace-collapsed and casefolded so
    'Acme  Plumbing' and 'ACME PLUMBING' are one business."""
    key = re.sub(r"\s+", " ", business_name or "").strip().casefold()
    if not key:
        return None
    return hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()


def _signed(byte: int, span: int) -> int:
    """A byte mapped onto the inclusive range [-span, +span]."""
    return (byte % (2 * span + 1)) - span


def accent_pair(lead: dict) -> tuple:
    """(accent, accent_dark) for this business: the niche's colours, nudged
    deterministically by business name. Falls back to the untouched niche pair
    for a blank name or anything that isn't a plain #rrggbb."""
    theme = theme_for(lead["niche"])
    base, base_dark = theme["accent"], theme["accent_dark"]
    seed = _business_seed(lead.get("business_name") or "")
    if seed is None:
        return base, base_dark

    h0, l0, s0 = colorsys.rgb_to_hls(*_hex_to_rgb(base))
    hd, ld, sd = colorsys.rgb_to_hls(*_hex_to_rgb(base_dark))

    hue = ((h0 * 360 + _signed(seed[0], ACCENT_HUE_SHIFT_DEG)) % 360) / 360
    sat = min(ACCENT_SAT_RANGE[1],
              max(ACCENT_SAT_RANGE[0], s0 + _signed(seed[1], ACCENT_SAT_SHIFT) / 100))
    light = max(ACCENT_MIN_LIGHTNESS, l0 + _signed(seed[2], ACCENT_LIGHT_SHIFT) / 100)

    # Contrast is a functional constraint, not taste: darken until white text on
    # this colour (and this colour as text on white) clears AA. Terminates --
    # lightness only falls, and black is 21:1.
    # Measured on the ROUNDED hex, not the float RGB: quantising to 8 bits per
    # channel moves the ratio, and measuring before the rounding let colours
    # land at 4.48-4.50 -- just under the floor they were supposed to clear.
    accent = _rgb_to_hex(colorsys.hls_to_rgb(hue, light, sat))
    while (_contrast_with_white(_hex_to_rgb(accent)) < ACCENT_MIN_CONTRAST_ON_WHITE
           and light > ACCENT_MIN_LIGHTNESS):
        light = max(ACCENT_MIN_LIGHTNESS, light - 0.02)
        accent = _rgb_to_hex(colorsys.hls_to_rgb(hue, light, sat))

    # accent_dark keeps the hand-tuned RELATIONSHIP of the niche pair (gym drops
    # 0.20 lightness, dentist 0.07) applied to the varied accent, rather than
    # being re-derived from scratch and drifting out of family.
    hue_dark = (hue + (hd - h0)) % 1.0
    light_dark = max(ACCENT_DARK_MIN_LIGHTNESS,
                     min(light - ACCENT_DARK_MIN_DROP, light - (l0 - ld)))
    sat_dark = min(ACCENT_SAT_RANGE[1], max(0.35, sat * (sd / s0 if s0 else 1.0)))
    accent_dark = _rgb_to_hex(colorsys.hls_to_rgb(hue_dark, light_dark, sat_dark))

    if not (_HEX_RE.match(accent) and _HEX_RE.match(accent_dark)):
        return base, base_dark  # never let anything but #rrggbb reach the template
    return accent, accent_dark


# Human-readable labels for common Google Places 'types' specialties, shown
# as a trust badge in the hero when a lead has one.
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

# Kept for the legacy per-niche templates (DESIGN_TEMPLATE_STYLE=legacy).
NICHE_SERVICES = {niche: [name for name, _ in theme["services"]] for niche, theme in NICHE_THEMES.items()}
NICHE_HERO_TEXT = {niche: theme["tagline"] for niche, theme in NICHE_THEMES.items()}
DEFAULT_HERO_TEXT = DEFAULT_THEME["tagline"]
NICHE_NAV_LABELS = {niche: ["Home", *theme["nav"]] for niche, theme in NICHE_THEMES.items()}
DEFAULT_NAV_LABELS = ["Home", *DEFAULT_THEME["nav"]]
_NICHE_IMAGE_QUERIES = {niche: theme["label"].lower() for niche, theme in NICHE_THEMES.items()}


def available_niches() -> set[str]:
    if not TEMPLATES_DIR.exists():
        return set()
    return {p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()}


def niche_display_name(niche: str) -> str:
    """Un-hyphenate and title-case a niche slug for display, e.g.
    'vehicle-repair' -> 'Vehicle Repair'."""
    return niche.replace("-", " ").replace("_", " ").strip().title()


def theme_for(niche: str) -> dict:
    return NICHE_THEMES.get(niche, DEFAULT_THEME)


def niche_services(lead: dict) -> list[str]:
    """Service names for the niche (legacy templates). Falls back to the
    lead's Google Maps 'types', then to the niche name itself."""
    niche = lead["niche"]
    if niche in NICHE_SERVICES:
        return NICHE_SERVICES[niche]
    google_types = lead.get("google_maps_types") or []
    if google_types:
        return [t.replace("_", " ").title() for t in google_types]
    return [niche_display_name(niche)]


def hero_tagline(lead: dict) -> str:
    return theme_for(lead["niche"])["tagline"].format(city=_city(lead), name=lead["business_name"])


def nav_labels(niche: str) -> list[str]:
    return NICHE_NAV_LABELS.get(niche, DEFAULT_NAV_LABELS)


def lead_specialty(lead: dict) -> Optional[str]:
    for google_type in lead.get("google_maps_types") or []:
        label = SPECIALTY_LABELS.get(google_type)
        if label:
            return label
    return None


def _places_facts(lead: dict) -> dict:
    """Rating / review count / address for the lead. Prefers the dedicated
    columns; falls back to the JSON blob sourcing_agent stores in `notes`
    for leads sourced before those columns existed."""
    facts = {
        "rating": lead.get("google_rating"),
        "reviews": lead.get("google_reviews_count"),
        "address": (lead.get("address") or "").strip(),
    }
    if facts["rating"] is None or not facts["address"]:
        try:
            blob = json.loads(lead.get("notes") or "")
        except (TypeError, ValueError):
            blob = {}
        if isinstance(blob, dict) and blob.get("source") == "google_places":
            if facts["rating"] is None:
                facts["rating"] = blob.get("rating")
            if facts["reviews"] is None:
                facts["reviews"] = blob.get("review_count")
            facts["address"] = facts["address"] or (blob.get("address") or "").strip()
    return facts


def google_rating_badge(lead: dict) -> Optional[float]:
    """The lead's Google rating, only once it's backed by enough reviews to
    be worth showing (see MIN_GOOGLE_REVIEWS_FOR_BADGE)."""
    facts = _places_facts(lead)
    rating, reviews = facts["rating"], facts["reviews"] or 0
    if rating and reviews >= MIN_GOOGLE_REVIEWS_FOR_BADGE:
        return float(rating)
    return None


def _city(lead: dict) -> str:
    """'Oxted, Surrey' -> 'Oxted'; blank -> 'your area'."""
    location = (lead.get("location") or "").strip()
    return location.split(",")[0].strip() or "your area"


def _phone_href(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return "+" + digits if phone.strip().startswith("+") else digits


def get_hero_image_url(niche: str) -> str:
    """Hero photo for the niche. Hand-picked Unsplash photo by default;
    Unsplash's search API if UNSPLASH_ACCESS_KEY is set (a random result
    per build); seeded placeholder only for a niche with no theme at all."""
    theme = theme_for(niche)
    if config.UNSPLASH_ACCESS_KEY:
        try:
            resp = requests.get(
                "https://api.unsplash.com/photos/random",
                params={"query": _NICHE_IMAGE_QUERIES.get(niche, niche_display_name(niche)), "orientation": "landscape"},
                headers={"Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}"},
                timeout=10,
            )
            resp.raise_for_status()
            url = resp.json().get("urls", {}).get("regular")
            if url:
                return url
        except (requests.RequestException, ValueError, KeyError):
            pass  # fall through to the curated photo
    if theme.get("hero"):
        return _unsplash(theme["hero"])
    return f"{_PLACEHOLDER_IMAGE_BASE}/{niche}/1600/900"


def _pain_point_solution(lead: dict) -> str:
    """Legacy-template copy. The modern template uses `about` instead: this
    text talks to the *prospect* about their web presence, which is the
    wrong audience for a page their customers will read."""
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


def _real_testimonial(lead: dict) -> Optional[str]:
    """A testimonial scraped from the lead's own site, or None. Never a
    made-up quote: an invented review on a real business's page is the
    fastest way to lose their trust."""
    text = (lead.get("testimonial") or "").strip()
    return text if len(text) >= 20 else None


def _testimonial(lead: dict) -> str:
    return _real_testimonial(lead) or (
        f"{lead['business_name']} always takes great care of us -- highly recommend "
        "to anyone in the area."
    )


def build_context(lead: dict) -> dict:
    """Everything the templates can render. Every optional fact (phone,
    address, rating, testimonial, email) is None when unknown so the modern
    template can hide the element instead of printing a placeholder."""
    niche = lead["niche"]
    theme = theme_for(niche)
    accent, accent_dark = accent_pair(lead)
    city = _city(lead)
    facts = _places_facts(lead)
    phone = (lead.get("phone") or "").strip() or None
    address = facts["address"] or None
    rating = google_rating_badge(lead)
    name = lead["business_name"]
    # Client-supplied images (utils/assets.py) beat stock photos wherever
    # they exist: their own shopfront in the hero is what sells the site.
    logo_path = assets.logo(lead["id"])
    own_photos = [f"assets/{p.name}" for p in assets.photos(lead["id"])]
    stock_gallery = [_unsplash(pid, 900, 700) for pid in theme.get("gallery", [])]
    hero_image = own_photos[0] if own_photos else get_hero_image_url(niche)
    gallery = (own_photos[1:3] + stock_gallery)[:2] if own_photos else stock_gallery
    return {
        # --- modern template ---------------------------------------------
        "business_name": name,
        "niche_label": theme["label"],
        "icon": theme["icon"],
        "accent": accent,
        "accent_dark": accent_dark,
        "city": city,
        "location": (lead.get("location") or "").strip(),
        "phone": phone,
        "phone_href": _phone_href(phone) if phone else None,
        "email": (lead.get("contact_email") or "").strip() or None,
        "address": address,
        "map_embed_url": f"https://www.google.com/maps?q={quote_plus(address)}&output=embed" if address else None,
        "google_rating": rating,
        "google_reviews_count": facts["reviews"] if rating else None,
        "google_reviews_url": f"https://www.google.com/maps/search/{quote_plus(name + ' ' + city)}" if rating else None,
        "hero_tagline": hero_tagline(lead),
        "hero_image_url": hero_image,
        "gallery_images": gallery,
        "logo_url": f"assets/{logo_path.name}" if logo_path else None,
        "own_photos": own_photos,
        "about": theme["about"].format(name=name, city=city),
        "service_items": [{"name": n, "blurb": b} for n, b in theme["services"]],
        "services_heading": theme.get("services_heading", "What we do"),
        "cta_secondary": theme.get("cta_secondary", "Get a quote"),
        "trust_line": theme.get("trust_line", "Friendly, reliable service"),
        "testimonial": _real_testimonial(lead),
        "specialty": lead_specialty(lead),
        "nav": theme["nav"],
        # Only link to sections that will actually render for this lead.
        "nav_links": [
            (label, anchor) for label, anchor, present in zip(
                theme["nav"], ("services", "about", "reviews", "contact"),
                (bool(theme["services"]), True, bool(_real_testimonial(lead) or rating), True),
            ) if present
        ],
        "year": datetime.now(timezone.utc).year,
        # A working link back to this lead's own preview (signed from the
        # lead id alone; the webhook server resolves it when clicked).
        "preview_url": tracker.create_click_link(lead["id"]),
        "sender_name": config.SENDER_NAME,
        # --- legacy per-niche templates (DESIGN_TEMPLATE_STYLE=legacy) ----
        "phone_or_prompt": phone or "Call us",
        "pain_point_solution": _pain_point_solution(lead),
        "legacy_testimonial": _testimonial(lead),
        "hero_headline": f"{name} -- Trusted Local {niche_display_name(niche)}",
        "niche_display": niche_display_name(niche),
        "services": niche_services(lead),
        "nav_labels": nav_labels(niche),
    }


def _template_dir(niche: str) -> Path:
    """templates/modern for everyone unless DESIGN_TEMPLATE_STYLE=legacy,
    in which case the per-niche folder (or templates/default)."""
    if config.DESIGN_TEMPLATE_STYLE != "legacy":
        modern = TEMPLATES_DIR / MODERN_TEMPLATE
        if modern.exists():
            return modern
    niche_dir = TEMPLATES_DIR / niche
    if not niche_dir.exists():
        niche_dir = TEMPLATES_DIR / DEFAULT_NICHE
    if not niche_dir.exists():
        raise ValueError(f"No template found for niche '{niche}'")
    return niche_dir


def render_template_files(niche: str, context: dict) -> dict[str, str]:
    """Render index.html and style.css with Jinja2. Returns
    {relative_path: rendered_content} ready to push to GitHub / Vercel."""
    niche_dir = _template_dir(niche)
    env = Environment(
        loader=FileSystemLoader(str(niche_dir)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )
    if niche_dir.name != MODERN_TEMPLATE:
        # Legacy templates expect the old string-typed keys.
        context = {**context, "phone": context["phone_or_prompt"], "testimonial": context["legacy_testimonial"]}
    rendered = {}
    for filename in TEMPLATE_FILES:
        template = env.get_template(filename)
        rendered[filename] = template.render(**context)
    return rendered


def build_site_files(lead: dict) -> dict:
    """Rendered template files plus any client assets, as one dict ready
    for github_api.push_files / vercel_api.deploy_files (text and bytes)."""
    files: dict = render_template_files(lead["niche"], build_context(lead))
    files.update(assets.site_files(lead["id"]))
    return files


def rebuild_preview(lead: dict) -> Optional[dict]:
    """Re-render and redeploy an EXISTING preview in place (same repo, same
    Vercel project, same URL) -- used after a prospect sends photos/logo or
    the template changes. Never touches lead status: a 'negotiating' lead
    stays negotiating. Returns the refreshed website row, or None if the
    lead has no website yet (use process_lead for a first build)."""
    website = db.get_website_by_lead(lead["id"])
    if website is None or not website.get("repo_full_name"):
        return None
    files = build_site_files(lead)
    project_raw_name = github_api.make_repo_name(lead["business_name"], lead["id"])
    github_api.push_files(github_api.get_repo(website["repo_full_name"]), files, commit_message="Rebuild preview")
    deployment = vercel_api.deploy_files(project_raw_name, files)
    preview_url = _validate_deployment_url(deployment)
    if preview_url != website.get("preview_url"):
        db.update_website_preview_url(lead["id"], preview_url)
    screenshot.invalidate(lead["id"])
    screenshot_url, screenshot_path = _capture_and_publish_screenshot(lead["id"], preview_url)
    if screenshot_url:
        db.update_website_screenshot_url(lead["id"], screenshot_url)
    db.log_state_history(lead["id"], lead["status"], lead["status"],
                         notes=f"Preview rebuilt with client assets ({assets.summary(lead['id'])}): {preview_url}")
    return db.get_website_by_lead(lead["id"])


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


def _validate_deployment_url(deployment: dict) -> str:
    """Return a public website URL or raise before any email can be queued.

    A deploy can technically succeed while the resulting URL is blank,
    malformed, still pending, or hidden behind an authentication page. Those
    states must not advance the lead to "designed", because SalesAgent sends
    every lead in that state.
    """
    preview_url = (deployment.get("url") or "").strip()
    parsed = urlparse(preview_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(f"Deployment did not return a valid public URL: {preview_url!r}")

    final_state = deployment.get("ready_state")
    if final_state and final_state != "READY":
        raise RuntimeError(f"Deployment is not ready: ready_state={final_state!r}, url={preview_url!r}")

    if any(part in parsed.path.lower() for part in _AUTH_URL_PARTS):
        raise RuntimeError(f"Deployment URL points to an authentication path: {preview_url}")

    try:
        resp = requests.get(preview_url, allow_redirects=True, timeout=_DEPLOYMENT_VALIDATION_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise RuntimeError(f"Deployment URL is not publicly accessible: {preview_url}") from exc

    final_url = resp.url or preview_url
    final_path = urlparse(final_url).path.lower()
    if resp.status_code >= 400:
        raise RuntimeError(f"Deployment URL returned HTTP {resp.status_code}: {preview_url}")
    if any(part in final_path for part in _AUTH_URL_PARTS):
        raise RuntimeError(f"Deployment URL redirects to an authentication path: {final_url}")
    page_text = resp.text.lower()
    if any(marker in page_text for marker in _AUTH_PAGE_MARKERS):
        raise RuntimeError(f"Deployment URL shows an authentication page: {preview_url}")

    return preview_url


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

    files = build_site_files(lead)

    _repo, repo_url, repo_full_name = github_api.create_repo_with_files(
        lead["business_name"], lead["id"], files
    )
    deployment = vercel_api.deploy_files(
        github_api.make_repo_name(lead["business_name"], lead["id"]), files
    )
    preview_url = _validate_deployment_url(deployment)

    screenshot_url, screenshot_path = _capture_and_publish_screenshot(lead["id"], preview_url)

    website_id = db.insert_website(
        lead_id=lead["id"],
        template_niche=niche,
        repo_url=repo_url,
        repo_full_name=repo_full_name,
        preview_url=preview_url,
        vercel_project_id=deployment["deployment_id"],
        screenshot_url=screenshot_url,
        screenshot_path=screenshot_path,
    )
    db.update_lead_status(lead["id"], "designed", notes=f"Preview deployed: {preview_url}")
    return db.get_website_by_lead(lead["id"]) if website_id else None


def run() -> None:
    """Main entrypoint called by main.py: design a site for every
    'researched' lead that has a matching template."""
    for lead in db.list_leads_by_status("researched"):
        try:
            process_lead(lead)
        except Exception as exc:  # noqa: BLE001 - one bad lead must not kill the batch
            db.log_state_history(lead["id"], "researched", "researched", notes=f"Design failed: {exc}")
