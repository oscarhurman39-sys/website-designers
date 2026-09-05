"""Render the preview template locally for a set of sample leads and
screenshot each one -- no GitHub repo, no Vercel deploy, no DB writes.

    python pipeline/render_preview.py                 # all niches, sample data
    python pipeline/render_preview.py --lead 81       # a real lead from the DB
    python pipeline/render_preview.py --niche plumber --no-screenshot

Output goes to pipeline/out/<slug>/index.html (+ style.css) and
pipeline/out/<slug>.png. Use it to check a template change by eye before
the next real lead gets a site built from it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))

from agents import design_agent  # noqa: E402
from utils import db  # noqa: E402

OUT_DIR = PIPELINE_DIR / "out"

# One realistic lead per niche. Phone/address/rating vary on purpose so the
# renders show both the "full facts" and the "nothing known" states.
SAMPLE_LEADS: list[dict] = [
    {"id": 9001, "business_name": "RH Heating", "niche": "plumber", "location": "Oxted, Surrey",
     "phone": "01883 712714", "address": "12 Station Road West, Oxted RH8 9EP", "google_rating": 5.0,
     "google_reviews_count": 359, "contact_email": "info@rhheating.co.uk"},
    {"id": 9002, "business_name": "Bright Spark Electrical", "niche": "electrician", "location": "Caterham, Surrey",
     "phone": "01883 340120", "address": "4 Croydon Road, Caterham CR3 6QB", "google_rating": 4.9, "google_reviews_count": 41},
    {"id": 9003, "business_name": "Greenleaf Landscaping", "niche": "landscaper", "location": "Reigate, Surrey",
     "phone": "01737 224455", "address": "Reigate RH2", "google_rating": 4.8, "google_reviews_count": 27},
    {"id": 9004, "business_name": "The Copper Kettle", "niche": "cafe", "location": "Oxted, Surrey",
     "phone": "01883 715222", "address": "88 Station Road East, Oxted RH8 0PG", "google_rating": 4.7, "google_reviews_count": 212,
     "testimonial": "Best flat white in Oxted and the staff are lovely. The cinnamon buns sell out for a reason."},
    {"id": 9005, "business_name": "Studio 54 Hair", "niche": "salon", "location": "Bromley, London",
     "phone": "020 8460 1234", "address": "54 High Street, Bromley BR1 1EA", "google_rating": 4.9, "google_reviews_count": 88},
    {"id": 9006, "business_name": "Croydon Dental Care", "niche": "dentist", "location": "Croydon, London",
     "phone": "020 8688 5566", "address": "2 George Street, Croydon CR0 1LA", "google_rating": 4.6, "google_reviews_count": 133},
    {"id": 9007, "business_name": "Ironworks Gym", "niche": "gym", "location": "Reigate, Surrey",
     "phone": "01737 555001", "address": "Unit 3, Holmethorpe Industrial Estate, Redhill RH1 2NB", "google_rating": 4.9, "google_reviews_count": 64},
    {"id": 9008, "business_name": "The Old Bell Kitchen", "niche": "restaurant", "location": "Caterham, Surrey",
     "phone": "01883 330099", "address": "The Old Bell, Godstone Road, Caterham CR3 6RB", "google_rating": 4.5, "google_reviews_count": 301},
    {"id": 9009, "business_name": "Surrey Auto Mobile Services", "niche": "vehicle-repair", "location": "Oxted, Surrey",
     "phone": "07700 900123", "google_rating": 5.0, "google_reviews_count": 6},  # too few reviews for a badge
    {"id": 9010, "business_name": "Hillside Framing", "niche": "picture-framer", "location": "Oxted"},  # no theme, no facts
]


def _slug(lead: dict) -> str:
    return "".join(c if c.isalnum() else "-" for c in f"{lead['niche']}-{lead['business_name']}".lower()).strip("-")


def render_one(lead: dict, screenshot: bool = True) -> Path:
    context = design_agent.build_context(lead)
    files = design_agent.render_template_files(lead["niche"], context)
    out = OUT_DIR / _slug(lead)
    out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")
    html_path = out / "index.html"
    if screenshot:
        _screenshot(html_path, OUT_DIR / f"{_slug(lead)}.png")
    return html_path


def _screenshot(html_path: Path, png_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(800)  # let the fade-up animation finish
        page.screenshot(path=str(png_path), full_page=True)
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lead", type=int, help="render a real lead id from the DB instead of the samples")
    parser.add_argument("--niche", help="only render the sample for this niche")
    parser.add_argument("--no-screenshot", action="store_true")
    args = parser.parse_args()

    if args.lead:
        lead = db.get_lead(args.lead)
        if lead is None:
            raise SystemExit(f"No lead with id {args.lead}")
        leads = [lead]
    else:
        leads = [l for l in SAMPLE_LEADS if not args.niche or l["niche"] == args.niche]

    for lead in leads:
        path = render_one(lead, screenshot=not args.no_screenshot)
        print(f"rendered {lead['niche']:<15} {lead['business_name']:<30} -> {path}")


if __name__ == "__main__":
    main()
