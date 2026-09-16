"""The modern template must never print a placeholder: every optional fact
is None when unknown and the template hides the element. These tests pin
that contract and the theme routing."""
from __future__ import annotations

import json

import pytest

import config
from agents import design_agent

_PLACEHOLDERS = ("YOUR LOGO", "Call Call", "coming soon", "will sit right here", "Your Offer", "None")


def _lead(**overrides) -> dict:
    base = {"id": 4242, "business_name": "Acme Plumbing", "niche": "plumber", "location": "Oxted, Surrey"}
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _modern(monkeypatch):
    monkeypatch.setattr(config, "DESIGN_TEMPLATE_STYLE", "modern")
    monkeypatch.setattr(config, "UNSPLASH_ACCESS_KEY", "")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://track.example.test")


def test_unknown_facts_are_none_not_placeholders():
    ctx = design_agent.build_context(_lead())
    assert ctx["phone"] is None and ctx["phone_href"] is None
    assert ctx["address"] is None and ctx["map_embed_url"] is None
    assert ctx["google_rating"] is None and ctx["testimonial"] is None
    assert ctx["city"] == "Oxted"


def test_rating_needs_enough_reviews():
    few = design_agent.build_context(_lead(google_rating=5.0, google_reviews_count=3))
    enough = design_agent.build_context(_lead(google_rating=4.8, google_reviews_count=25))
    assert few["google_rating"] is None
    assert enough["google_rating"] == 4.8 and enough["google_reviews_count"] == 25
    assert "google.com/maps" in enough["google_reviews_url"]


def test_places_facts_fall_back_to_notes_blob():
    notes = json.dumps({"source": "google_places", "rating": 4.9, "review_count": 40, "address": "1 High St, Oxted"})
    ctx = design_agent.build_context(_lead(notes=notes))
    assert ctx["google_rating"] == 4.9
    assert ctx["address"] == "1 High St, Oxted"
    assert "output=embed" in ctx["map_embed_url"]


def test_phone_href_strips_formatting():
    ctx = design_agent.build_context(_lead(phone="01883 712 714"))
    assert ctx["phone_href"] == "01883712714"
    assert design_agent.build_context(_lead(phone="+44 1883 712714"))["phone_href"] == "+441883712714"


def test_short_or_missing_testimonial_is_dropped():
    assert design_agent.build_context(_lead(testimonial="Great!"))["testimonial"] is None
    real = "Fixed our boiler on a Sunday and charged a fair price. Highly recommend."
    assert design_agent.build_context(_lead(testimonial=real))["testimonial"] == real


def test_nav_only_links_to_sections_that_render():
    bare = design_agent.build_context(_lead(niche="picture-framer"))
    assert [a for _, a in bare["nav_links"]] == ["about", "contact"]
    rated = design_agent.build_context(_lead(google_rating=4.7, google_reviews_count=50))
    assert [a for _, a in rated["nav_links"]] == ["services", "about", "reviews", "contact"]


@pytest.mark.parametrize("niche", sorted(design_agent.NICHE_THEMES) + ["picture-framer"])
def test_modern_render_has_no_placeholder_text(niche):
    ctx = design_agent.build_context(_lead(niche=niche))
    files = design_agent.render_template_files(niche, ctx)
    html = files["index.html"]
    for bad in _PLACEHOLDERS:
        assert bad not in html, f"{niche}: found {bad!r}"
    assert "images.unsplash.com" in html
    assert 'href="tel:' not in html  # no phone -> no call buttons at all


def test_modern_render_uses_real_facts():
    lead = _lead(phone="01883 712714", address="12 Station Road West, Oxted", google_rating=5.0,
                 google_reviews_count=359, contact_email="info@acme.test")
    html = design_agent.render_template_files("plumber", design_agent.build_context(lead))["index.html"]
    assert 'href="tel:01883712714"' in html
    assert "359" in html and "5.0" in html
    assert "maps?q=12+Station+Road+West" in html
    assert "mailto:info@acme.test" in html


def test_legacy_style_still_renders_per_niche_folder(monkeypatch):
    monkeypatch.setattr(config, "DESIGN_TEMPLATE_STYLE", "legacy")
    html = design_agent.render_template_files("plumber", design_agent.build_context(_lead()))["index.html"]
    assert "Plumbing" in html and "Call us" in html  # legacy template's own phone prompt


# --- Per-business accent ------------------------------------------------------
# Every plumber used to get byte-identical colours, so a run of previews read as
# one template with the names swapped. These pin that the variation is stable,
# stays inside its niche's hue family, and never breaks contrast.

_ACCENT_NAMES = ["Acme Plumbing", "Crawley Drains", "Hillside Framing", "Emile & Sons",
                 "A1 24/7 Locksmiths", "Zeta Bros"]
_ACCENT_NICHES = sorted(design_agent.NICHE_THEMES) + ["picture-framer"]


def _hue_deg(hex_colour: str) -> float:
    import colorsys
    return colorsys.rgb_to_hls(*design_agent._hex_to_rgb(hex_colour))[0] * 360


def test_accent_is_stable_per_business_and_differs_between_them():
    once = design_agent.accent_pair(_lead(business_name="Acme Plumbing"))
    again = design_agent.accent_pair(_lead(business_name="Acme Plumbing"))
    other = design_agent.accent_pair(_lead(business_name="Crawley Drains"))
    assert once == again          # a rebuild must not recolour the site under them
    assert once != other


def test_accent_ignores_case_and_extra_whitespace_in_the_name():
    assert (design_agent.accent_pair(_lead(business_name="Acme Plumbing"))
            == design_agent.accent_pair(_lead(business_name="  acme   PLUMBING ")))


def test_blank_business_name_falls_back_to_the_niche_theme():
    theme = design_agent.theme_for("plumber")
    assert (design_agent.accent_pair(_lead(business_name=""))
            == (theme["accent"], theme["accent_dark"]))


@pytest.mark.parametrize("niche", _ACCENT_NICHES)
def test_accent_stays_inside_the_niche_hue_family(niche):
    """A plumber stays blue and a salon stays pink: the variation is a bounded
    rotation around the theme's hue, not a free choice of colour."""
    base_hue = _hue_deg(design_agent.theme_for(niche)["accent"])
    for name in _ACCENT_NAMES:
        accent, _ = design_agent.accent_pair(_lead(niche=niche, business_name=name))
        drift = abs((_hue_deg(accent) - base_hue + 180) % 360 - 180)
        assert drift <= design_agent.ACCENT_HUE_SHIFT_DEG + 0.5, f"{niche}/{name}: {drift:.1f} deg"


@pytest.mark.parametrize("niche", _ACCENT_NICHES)
def test_accent_clears_white_contrast_for_every_niche(niche):
    """accent is both a white-text background and text on white, so it has to
    clear WCAG AA either way round."""
    for name in _ACCENT_NAMES:
        accent, accent_dark = design_agent.accent_pair(_lead(niche=niche, business_name=name))
        assert design_agent._contrast_with_white(design_agent._hex_to_rgb(accent)) >= 4.5
        assert design_agent._contrast_with_white(design_agent._hex_to_rgb(accent_dark)) >= 4.5


@pytest.mark.parametrize("niche", _ACCENT_NICHES)
def test_accent_dark_is_always_darker_than_accent(niche):
    for name in _ACCENT_NAMES:
        accent, accent_dark = design_agent.accent_pair(_lead(niche=niche, business_name=name))
        assert (design_agent._relative_luminance(design_agent._hex_to_rgb(accent_dark))
                < design_agent._relative_luminance(design_agent._hex_to_rgb(accent)))


def test_accent_is_a_plain_lowercase_six_digit_hex():
    for niche in _ACCENT_NICHES:
        for name in _ACCENT_NAMES:
            for colour in design_agent.accent_pair(_lead(niche=niche, business_name=name)):
                assert design_agent._HEX_RE.match(colour), f"{niche}/{name}: {colour!r}"


def test_rendered_page_carries_this_business_accent():
    """An empty accent renders `--accent: ;` -- a broken-looking page that still
    deploys, and _PLACEHOLDERS does not catch an empty string."""
    ctx = design_agent.build_context(_lead(business_name="Crawley Drains"))
    html = design_agent.render_template_files("plumber", ctx)["index.html"]
    assert f'--accent: {ctx["accent"]};' in html
    assert ctx["accent"] != design_agent.theme_for("plumber")["accent"]


def test_accent_invariants_hold_across_many_names_and_niches():
    """Brute force, because the failure mode here is statistical: measuring
    contrast before rounding to 8-bit hex put roughly 1 in 250 businesses just
    under the AA floor (4.48-4.50), which a handful of sample names missed."""
    import colorsys
    names = [f"{p} {s} {i}"
             for p in ("Acme", "Harbour", "Copthorne", "Orca", "Emile & Sons")
             for s in ("Plumbing", "Heating Ltd", "Services", "& Co", "24/7")
             for i in range(8)]
    failures = []
    for niche in _ACCENT_NICHES:
        base = design_agent.theme_for(niche)["accent"]
        base_hue = _hue_deg(base)
        for name in names:
            accent, dark = design_agent.accent_pair(_lead(niche=niche, business_name=name))
            rgb, rgb_dark = design_agent._hex_to_rgb(accent), design_agent._hex_to_rgb(dark)
            if design_agent._contrast_with_white(rgb) < 4.5:
                failures.append(f"{niche}/{name}: {accent} contrast too low")
            if design_agent._relative_luminance(rgb_dark) >= design_agent._relative_luminance(rgb):
                failures.append(f"{niche}/{name}: {dark} not darker than {accent}")
            drift = abs((_hue_deg(accent) - base_hue + 180) % 360 - 180)
            if drift > design_agent.ACCENT_HUE_SHIFT_DEG + 0.6:  # +0.6 for hex quantisation
                failures.append(f"{niche}/{name}: hue drift {drift:.1f} deg")
            if accent == base:
                failures.append(f"{niche}/{name}: identical to the niche base colour")
    assert not failures, f"{len(failures)} of {len(_ACCENT_NICHES) * len(names)}: {failures[:5]}"
