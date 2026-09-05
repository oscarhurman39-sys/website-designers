"""Client photos/logo: attachment parsing, asset normalisation, template use,
the in-place rebuild, and the sourcing exclusion radius."""
from __future__ import annotations

import email
import io
from email.message import EmailMessage
from unittest.mock import Mock

import pytest
from PIL import Image

import config
from agents import design_agent, sourcing_agent
from utils import assets, email_utils


def _png(width: int, height: int, colour=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    img = Image.effect_noise((width, height), 80).convert("RGB")  # noise -> not tiny once compressed
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


@pytest.fixture
def assets_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "ASSETS_DIR", tmp_path / "assets")
    return tmp_path / "assets"


# ---------------------------------------------------------------- parsing

def test_extract_attachments_picks_images_only():
    msg = EmailMessage()
    msg["From"] = "owner@acme.test"
    msg.set_content("here are the pics")
    msg.add_attachment(_jpeg(800, 600), maintype="image", subtype="jpeg", filename="shop.jpg")
    msg.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="quote.pdf")
    parsed = email.message_from_bytes(msg.as_bytes())
    found = email_utils._extract_attachments(parsed)
    assert [a.filename for a in found] == ["shop.jpg"]
    assert found[0].content_type == "image/jpeg" and len(found[0].data) > 1000


# ------------------------------------------------------------- saving rules

def test_logo_and_photos_are_saved_normalised(assets_dir):
    saved = assets.save_attachments(7, [
        assets.Attachment("Company LOGO final.png", "image/png", _png(1400, 900)),
        assets.Attachment("IMG_0001.jpg", "image/jpeg", _jpeg(2400, 1600)),
        assets.Attachment("IMG_0002.jpg", "image/jpeg", _jpeg(1200, 900)),
    ])
    kinds = sorted(s.kind for s in saved)
    assert kinds == ["logo", "photo", "photo"]
    assert assets.logo(7).name == "logo.png"
    assert [p.name for p in assets.photos(7)] == ["photo-1.jpg", "photo-2.jpg"]
    with Image.open(assets.photos(7)[0]) as im:
        assert max(im.size) == assets.MAX_PHOTO_EDGE  # resized down
    with Image.open(assets.logo(7)) as im:
        assert max(im.size) <= assets.MAX_LOGO_EDGE
    assert set(assets.site_files(7)) == {"assets/logo.png", "assets/photo-1.jpg", "assets/photo-2.jpg"}
    assert assets.summary(7) == "logo + 2 photos"


def test_signature_badges_and_tiny_images_are_ignored(assets_dir):
    saved = assets.save_attachments(8, [
        assets.Attachment("image001.png", "image/png", _png(120, 40)),        # email signature
        assets.Attachment("thumb.jpg", "image/jpeg", _jpeg(300, 200)),       # too small
    ])
    assert saved == [] and assets.summary(8) == "none"


def test_photo_cap_and_logo_replacement(assets_dir):
    many = [assets.Attachment(f"p{i}.jpg", "image/jpeg", _jpeg(900, 700)) for i in range(8)]
    assets.save_attachments(9, many)
    assert len(assets.photos(9)) == assets.MAX_PHOTOS
    assets.save_attachments(9, [assets.Attachment("logo-v1.png", "image/png", _png(300, 300))])
    assets.save_attachments(9, [assets.Attachment("new logo.svg", "image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>")])
    assert assets.logo(9).name == "logo.svg"  # newest wins, old one removed
    assert not (assets.lead_dir(9) / "logo.png").exists()


# ------------------------------------------------------------- template use

def test_context_prefers_client_images(assets_dir, monkeypatch):
    monkeypatch.setattr(config, "DESIGN_TEMPLATE_STYLE", "modern")
    monkeypatch.setattr(config, "UNSPLASH_ACCESS_KEY", "")
    lead = {"id": 11, "business_name": "Acme Plumbing", "niche": "plumber", "location": "Horsham"}
    before = design_agent.build_context(lead)
    assert before["logo_url"] is None and "images.unsplash.com" in before["hero_image_url"]
    assets.save_attachments(11, [
        assets.Attachment("logo.png", "image/png", _png(400, 200)),
        assets.Attachment("front.jpg", "image/jpeg", _jpeg(1600, 1000)),
    ])
    after = design_agent.build_context(lead)
    assert after["logo_url"] == "assets/logo.png"
    assert after["hero_image_url"] == "assets/photo-1.jpg"
    html = design_agent.render_template_files("plumber", after)["index.html"]
    assert '<img src="assets/logo.png"' in html and 'src="assets/photo-1.jpg"' in html
    files = design_agent.build_site_files(lead)
    assert isinstance(files["assets/photo-1.jpg"], bytes) and isinstance(files["index.html"], str)


# ------------------------------------------------------------------ rebuild

def test_rebuild_preview_redeploys_in_place_without_touching_status(assets_dir, monkeypatch, tmp_path):
    from utils import db
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "DESIGN_TEMPLATE_STYLE", "modern")
    monkeypatch.setattr(config, "UNSPLASH_ACCESS_KEY", "")
    db.init_db()
    lead_id = db.insert_lead("Acme Plumbing", "plumber", "Horsham", status="negotiating")
    db.insert_website(lead_id=lead_id, template_niche="plumber", repo_url="https://github.com/x/acme",
                      repo_full_name="x/acme", preview_url="https://acme.vercel.app", vercel_project_id="dpl_1")
    pushed = {}
    monkeypatch.setattr(design_agent.github_api, "get_repo", lambda name: name)
    monkeypatch.setattr(design_agent.github_api, "push_files", lambda repo, files, commit_message="": pushed.update(files))
    monkeypatch.setattr(design_agent.vercel_api, "deploy_files", lambda name, files: {"url": "https://acme.vercel.app", "ready_state": "READY", "deployment_id": "dpl_2"})
    monkeypatch.setattr(design_agent, "_validate_deployment_url", lambda d: d["url"])
    monkeypatch.setattr(design_agent, "_capture_and_publish_screenshot", lambda lead_id, url: ("", ""))
    assets.save_attachments(lead_id, [assets.Attachment("hero.jpg", "image/jpeg", _jpeg(1600, 1000))])

    website = design_agent.rebuild_preview(db.get_lead(lead_id))
    assert website["preview_url"] == "https://acme.vercel.app"
    assert "assets/photo-1.jpg" in pushed and "index.html" in pushed
    assert db.get_lead(lead_id)["status"] == "negotiating"


# ------------------------------------------------------ sourcing exclusion

def _cand(lat, lng):
    return sourcing_agent.Candidate("X", "plumber", "Oxted, Surrey", "pid", "https://x.test", "", "", None, 0, lat, lng)


def test_exclusion_zone_uses_distance_from_centre(monkeypatch):
    monkeypatch.setattr(config, "SOURCING_EXCLUDE_CENTER", (51.2572, 0.0040))
    monkeypatch.setattr(config, "SOURCING_EXCLUDE_RADIUS_MILES", 6)
    assert sourcing_agent._within_exclusion_zone(_cand(51.2572, 0.0040))        # Oxted itself
    assert sourcing_agent._within_exclusion_zone(_cand(51.2803, -0.0776))       # Caterham, ~4 mi
    assert not sourcing_agent._within_exclusion_zone(_cand(51.2374, -0.2055))   # Reigate, ~9 mi
    assert not sourcing_agent._within_exclusion_zone(_cand(51.1124, -0.1870))   # Crawley, ~13 mi
    assert not sourcing_agent._within_exclusion_zone(_cand(None, None))         # unknown -> keep
    monkeypatch.setattr(config, "SOURCING_EXCLUDE_CENTER", None)
    assert not sourcing_agent._within_exclusion_zone(_cand(51.2572, 0.0040))    # feature off
