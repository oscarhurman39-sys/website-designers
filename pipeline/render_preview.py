"""Render the preview template locally for a set of sample leads and
screenshot each one -- no GitHub repo, no Vercel deploy, no DB writes.

    python pipeline/render_preview.py                 # all niches, sample data
    python pipeline/render_preview.py --all-widths    # + tablet and phone screenshots
    python pipeline/render_preview.py --lead 81       # a real lead from the DB
    python pipeline/render_preview.py --niche plumber --no-screenshot

Output goes to pipeline/out/<slug>/index.html (+ style.css) and
pipeline/out/<slug>.png (desktop, 1280 wide); --all-widths adds
<slug>.tablet.png (834 wide) and <slug>.mobile.png (390 wide, 2x). Use it to
check a template change by eye before the next real lead gets a site built
from it.

Known capture limitation: the "Get in touch" map (a Google Maps embed
iframe) is not captured by headless Chromium and shows as an empty white
box in these PNGs; that is the screenshot, not the template. Judge
everything else from the PNGs and check the map by opening
pipeline/out/<slug>/index.html (or a deployed preview) in a real browser.
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


# Screenshot viewports: file-name suffix -> (width, height). Width is what
# matters (the templates' breakpoints are width-based); height only sets the
# first fold before the full-page capture.
VIEWPORTS: dict[str, tuple[int, int]] = {
    "": (1280, 800),         # desktop
    ".tablet": (834, 1112),  # iPad-sized portrait
    ".mobile": (390, 844),   # phone; captured at 2x so text stays legible
}


def render_one(lead: dict, screenshot: bool = True, all_widths: bool = False) -> Path:
    context = design_agent.build_context(lead)
    files = design_agent.render_template_files(lead["niche"], context)
    out = OUT_DIR / _slug(lead)
    out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")
    html_path = out / "index.html"
    if screenshot:
        _screenshot(html_path, _slug(lead), list(VIEWPORTS) if all_widths else [""])
    return html_path


def _serve_out_dir():
    """Serve pipeline/out over plain HTTP on a free localhost port for the
    duration of a screenshot run. Google's map embed renders blank when the
    parent page is a file:// URL, which made every local render look as if
    the contact section were broken; over http:// it behaves as on Vercel."""
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(OUT_DIR), **kwargs)

        def log_message(self, *args, **kwargs):  # keep the console clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _screenshot(html_path: Path, slug: str, suffixes: list[str]) -> None:
    from playwright.sync_api import sync_playwright

    server = _serve_out_dir()
    url = f"http://127.0.0.1:{server.server_port}/{html_path.relative_to(OUT_DIR).as_posix()}"
    try:
        with sync_playwright() as p:
            _capture(p, url, slug, suffixes)
    finally:
        server.shutdown()


def _capture(p, url: str, slug: str, suffixes: list[str]) -> None:
    browser = p.chromium.launch()
    try:
        for suffix in suffixes:
            width, height = VIEWPORTS[suffix]
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=2 if width < 500 else 1,
            )
            page.goto(url, wait_until="networkidle", timeout=60_000)
            # The contact map is a lazy-loaded iframe: a full-page capture never
            # scrolls, so without this nudge it screenshots as an empty box that
            # looks like a template bug. Scroll to the bottom, let it load, go back.
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(800)  # let the fade-up animation finish
            # Same flip utils/screenshot.py makes before an emailed capture: the
            # live map iframe paints blank headless, the placeholder does not.
            page.evaluate("document.documentElement.setAttribute('data-screenshot', '1')")
            page.wait_for_timeout(100)
            page.screenshot(path=str(OUT_DIR / f"{slug}{suffix}.png"), full_page=True)
            page.close()
    finally:
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lead", type=int, help="render a real lead id from the DB instead of the samples")
    parser.add_argument("--niche", help="only render the sample for this niche")
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--all-widths", action="store_true", help="also capture tablet and phone screenshots")
    args = parser.parse_args()

    if args.lead:
        lead = db.get_lead(args.lead)
        if lead is None:
            raise SystemExit(f"No lead with id {args.lead}")
        leads = [lead]
    else:
        leads = [l for l in SAMPLE_LEADS if not args.niche or l["niche"] == args.niche]

    for lead in leads:
        path = render_one(lead, screenshot=not args.no_screenshot, all_widths=args.all_widths)
        print(f"rendered {lead['niche']:<15} {lead['business_name']:<30} -> {path}")


if __name__ == "__main__":
    main()
