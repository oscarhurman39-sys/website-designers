"""The modern template is assembled from interchangeable section blocks.

Two leads in the same niche must get visibly different pages, the same lead
must get the identical page every time (a rebuild after client photos must
not reshuffle a page a prospect already opened), and every variant must
render against both a fully-known and a fully-unknown lead without leaking
an undefined variable.
"""
from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

import config
from agents import design_agent
from utils import assets

MODERN = Path(design_agent.TEMPLATES_DIR) / design_agent.MODERN_TEMPLATE


def _lead(lead_id: int = 4242, **overrides) -> dict:
    base = {"id": lead_id, "business_name": "Acme Plumbing", "niche": "plumber", "location": "Oxted, Surrey"}
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _modern(monkeypatch):
    monkeypatch.setattr(config, "DESIGN_TEMPLATE_STYLE", "modern")
    monkeypatch.setattr(config, "UNSPLASH_ACCESS_KEY", "")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://track.example.test")


def _render(lead: dict) -> str:
    return design_agent.render_template_files(lead["niche"], design_agent.build_context(lead))["index.html"]


def test_same_lead_renders_byte_identical_twice():
    assert _render(_lead(7)) == _render(_lead(7))


def test_different_leads_in_the_same_niche_get_different_layouts():
    layouts = {tuple(sorted(design_agent.choose_blocks(i)[0].items())) + design_agent.choose_blocks(i)[1]
               for i in range(1, 13)}
    assert len(layouts) >= 6, layouts
    # And the rendered markup differs beyond the business name.
    a = _render(_lead(1, business_name="Same Name")).replace("Same Name", "")
    b = _render(_lead(2, business_name="Same Name")).replace("Same Name", "")
    assert a != b


def test_every_variant_and_order_is_reachable():
    seen = {section: set() for section in design_agent.BLOCK_VARIANTS}
    orders = set()
    for i in range(1, 201):
        blocks, order = design_agent.choose_blocks(i)
        for section, variant in blocks.items():
            seen[section].add(variant)
        orders.add(order)
    for section, variants in design_agent.BLOCK_VARIANTS.items():
        assert seen[section] == set(variants), (section, seen[section])
    assert orders == set(design_agent.SECTION_ORDERS)


def test_contact_is_always_last_and_every_section_appears_once():
    for order in design_agent.SECTION_ORDERS:
        assert order[-1] == "contact"
        assert sorted(order) == sorted(design_agent.BLOCK_VARIANTS.keys() - {"hero"})


@pytest.mark.parametrize("section,variant", [
    (s, v) for s, vs in design_agent.BLOCK_VARIANTS.items() for v in vs
])
def test_every_block_renders_for_known_and_unknown_leads(section, variant):
    """StrictUndefined turns a typo'd variable into an error instead of a
    silent blank, which the production Environment would not."""
    env = Environment(loader=FileSystemLoader(str(MODERN)), undefined=StrictUndefined,
                      autoescape=select_autoescape(enabled_extensions=("html",)))
    template = env.get_template(f"blocks/{section}/{variant}.html")
    rich = design_agent.build_context(_lead(
        phone="01883 123456", contact_email="hi@acme.test", google_rating=4.8,
        google_reviews_count=25, address="1 High St, Oxted RH8 9AA", testimonial="Turned up on time and fixed it first go.",
    ))
    bare = design_agent.build_context(_lead())
    for ctx in (rich, bare):
        html = template.render(**ctx)
        assert "{{" not in html and "None" not in re.sub(r"<[^>]+>", "", html)
    assert "Acme Plumbing" in template.render(**rich) or section in ("services", "reviews", "contact")


def test_full_page_renders_every_combination_without_error():
    """All 216 layouts through the real renderer (default Undefined)."""
    lead = _lead(phone="01883 123456", google_rating=4.8, google_reviews_count=25)
    ctx = design_agent.build_context(lead)
    combos = itertools.product(*design_agent.BLOCK_VARIANTS.values())
    env = Environment(loader=FileSystemLoader(str(MODERN)), undefined=StrictUndefined,
                      autoescape=select_autoescape(enabled_extensions=("html",)))
    page = env.get_template("index.html")
    for combo, order in itertools.product(combos, design_agent.SECTION_ORDERS):
        blocks = dict(zip(design_agent.BLOCK_VARIANTS.keys(), combo))
        html = page.render(**{**ctx, "blocks": blocks, "section_order": order})
        assert html.count('id="contact"') == 1 and html.count('id="top"') == 1


def test_client_photos_force_the_photo_led_about_block(monkeypatch):
    monkeypatch.setattr(assets, "photos", lambda lead_id: [Path("a.jpg"), Path("b.jpg")])
    monkeypatch.setattr(assets, "logo", lambda lead_id: None)
    for i in range(1, 30):
        assert design_agent.build_context(_lead(i))["blocks"]["about"] == "photos"


def test_service_icon_chip_has_a_visible_background():
    """Audit finding: bg-[var(--accent)]/10 renders no background under the
    Tailwind play CDN, leaving six bare ticks in the most prominent grid."""
    html = _render(_lead(1))
    if 'id="services"' in html and "color-mix" not in html:
        pytest.skip("this lead did not draw the cards variant")
    assert "bg-[var(--accent)]/10" not in html


# ---------------------------------------------------------------- contact map

def test_contact_map_ships_the_live_embed_plus_a_screenshot_placeholder():
    env = Environment(loader=FileSystemLoader(str(MODERN)), undefined=StrictUndefined,
                      autoescape=select_autoescape(enabled_extensions=("html",)))
    with_map = design_agent.build_context(_lead(address="1 High St, Oxted RH8 9AA"))
    without = design_agent.build_context(_lead())
    for variant in design_agent.BLOCK_VARIANTS["contact"]:
        template = env.get_template(f"blocks/contact/{variant}.html")
        html = template.render(**with_map)
        assert "map-live" in html and "map-placeholder" in html
        assert "Interactive map locating Acme Plumbing" in html
        assert "map-" not in template.render(**without)
    css = (MODERN / "style.css").read_text(encoding="utf-8")
    assert "html[data-screenshot] .map-placeholder" in css and "html[data-screenshot] .map-live" in css


def test_capture_flips_the_page_into_screenshot_mode():
    from utils import screenshot
    calls = []
    class Page:
        def evaluate(self, js): calls.append(js)
    screenshot.mark_for_screenshot(Page())
    assert calls == [screenshot.SCREENSHOT_MODE_JS] and "data-screenshot" in calls[0]
