"""Self-contained 'your photo here' hero placeholder.

Previews intentionally ship with a branded placeholder instead of a stock
photo (Unsplash) or a photo of the business pulled from Google Places:

  * Honest -- the prospect can see at a glance that a real photo goes there,
    which is exactly what the cold email offers to swap in.
  * Legally safe -- no third-party image rights (Unsplash licensing, Google
    Maps Platform photo ToS) attach to a preview we send to someone who
    hasn't asked for it.
  * Unbreakable -- the SVG is embedded as a `data:` URI, so there is no
    external URL to 404. It survives static deployment to Vercel with
    nothing left to fetch, and can't leave a broken-image icon on the page.

The placeholder graphics live in the LOWER half of the 1200x600 viewBox on
purpose: every template overlays the business name in the upper area of the
hero, so the two never collide.

Unsplash remains available as a commented-out option in
agents/design_agent.get_hero_image_url() for when a lead becomes a paying
client and we swap in real photography -- but everything is placeholder-first
until then.
"""
from __future__ import annotations

import base64

# Camera icon + copy sit low (y >= ~300) so the template's business-name
# overlay (upper area) stays clear of them.
_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 600" width="1200" height="600" role="img" aria-label="Your photo here">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#334155"/>
      <stop offset="1" stop-color="#0f172a"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="600" fill="url(#bg)"/>
  <rect x="40" y="40" width="1120" height="520" rx="20" fill="none" stroke="#ffffff" stroke-opacity="0.15" stroke-width="2" stroke-dasharray="10 10"/>
  <g fill="none" stroke="#cbd5e1" stroke-width="5" stroke-linejoin="round">
    <rect x="536" y="312" width="128" height="86" rx="14"/>
    <path d="M568 312 l10 -16 h44 l10 16"/>
    <circle cx="600" cy="358" r="27"/>
  </g>
  <circle cx="642" cy="330" r="5" fill="#cbd5e1" stroke="none"/>
  <text x="600" y="452" text-anchor="middle" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="46" font-weight="700" fill="#e2e8f0">Your photo here</text>
  <text x="600" y="498" text-anchor="middle" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="27" fill="#94a3b8">Your work. Your team. Your shop.</text>
</svg>"""

# Base64 (not raw utf8) so it embeds cleanly in both CSS `url('...')` and an
# <img src="..."> without any quote/escaping surprises.
HERO_PLACEHOLDER_DATA_URI: str = "data:image/svg+xml;base64," + base64.b64encode(
    _SVG.encode("utf-8")
).decode("ascii")

# The exact line design_agent.py stores on a lead (in leads.image_note) and
# sales_agent.py surfaces in the cold email, so the prospect knows the hero
# is a stand-in. Kept here next to the placeholder it describes.
PLACEHOLDER_EMAIL_NOTE: str = (
    "I've kept the design simple for now -- we'd add your own photos and reviews when we talk."
)
