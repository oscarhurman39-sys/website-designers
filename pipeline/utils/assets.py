"""Client-supplied photos and logos for a lead's preview site.

Prospects are told they can reply with their own photos and logo attached.
This module turns those attachments into a small, web-ready asset set on
disk (pipeline/assets/<lead_id>/) that design_agent.py bakes into the next
build, so a client's own images end up inside the deployed site (and its
GitHub repo), not served from our tracking server.

    assets/<lead_id>/logo.png|svg     one logo (last one received wins)
    assets/<lead_id>/photo-1.jpg ...  up to MAX_PHOTOS photos, oldest first

Files dropped into that folder by hand (photos that arrived over WhatsApp,
say) are picked up exactly the same way -- run `python run.py rebuild <id>`.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, UnidentifiedImageError

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

MAX_PHOTOS = 6
MAX_PHOTO_EDGE = 1600        # px; enough for a full-width hero, small enough to deploy
MAX_LOGO_EDGE = 600
PHOTO_QUALITY = 82
MIN_PHOTO_EDGE = 500         # anything smaller is an icon, a signature image or a thumbnail
MIN_PHOTO_BYTES = 30_000     # email signature logos/badges are tiny; real photos are not
_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/svg+xml", "image/gif", "image/heic"}
_LOGO_HINT = re.compile(r"logo|brand|mark|icon", re.IGNORECASE)


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class SavedAsset:
    kind: str          # "logo" | "photo"
    path: Path


def lead_dir(lead_id: int) -> Path:
    return ASSETS_DIR / str(int(lead_id))


def is_image(attachment: Attachment) -> bool:
    return attachment.content_type.lower().split(";")[0] in _IMAGE_TYPES


def looks_like_logo(attachment: Attachment) -> bool:
    """Filename says so, or it's an SVG (nobody emails an SVG photo)."""
    return bool(_LOGO_HINT.search(attachment.filename)) or attachment.content_type.lower().startswith("image/svg")


def save_attachments(lead_id: int, attachments: list[Attachment]) -> list[SavedAsset]:
    """Persist usable images from one email. Returns what was saved (may be
    empty: signature badges and tiny images are ignored on purpose so a
    reply with a 2KB email-footer logo doesn't rebuild the site)."""
    saved: list[SavedAsset] = []
    for att in attachments:
        if not is_image(att) or not att.data:
            continue
        try:
            if looks_like_logo(att):
                path = _save_logo(lead_id, att)
            else:
                path = _save_photo(lead_id, att)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            print(f"[assets] Skipped {att.filename!r} for lead {lead_id}: {exc}")
            continue
        if path is not None:
            saved.append(SavedAsset("logo" if looks_like_logo(att) else "photo", path))
    return saved


def _save_logo(lead_id: int, att: Attachment) -> Optional[Path]:
    folder = lead_dir(lead_id)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("logo.*"):
        old.unlink()
    if att.content_type.lower().startswith("image/svg"):
        path = folder / "logo.svg"
        path.write_bytes(att.data)
        return path
    img = Image.open(io.BytesIO(att.data))
    img.thumbnail((MAX_LOGO_EDGE, MAX_LOGO_EDGE))
    path = folder / "logo.png"
    img.save(path, format="PNG", optimize=True)   # PNG keeps transparency, which logos need
    return path


def _save_photo(lead_id: int, att: Attachment) -> Optional[Path]:
    if len(att.data) < MIN_PHOTO_BYTES:
        return None
    img = Image.open(io.BytesIO(att.data))
    if min(img.size) < MIN_PHOTO_EDGE:
        return None
    folder = lead_dir(lead_id)
    folder.mkdir(parents=True, exist_ok=True)
    existing = photos(lead_id)
    if len(existing) >= MAX_PHOTOS:
        return None
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((MAX_PHOTO_EDGE, MAX_PHOTO_EDGE))
    path = folder / f"photo-{len(existing) + 1}.jpg"
    img.save(path, format="JPEG", quality=PHOTO_QUALITY, optimize=True, progressive=True)
    return path


def logo(lead_id: int) -> Optional[Path]:
    matches = sorted(lead_dir(lead_id).glob("logo.*")) if lead_dir(lead_id).exists() else []
    return matches[0] if matches else None


def photos(lead_id: int) -> list[Path]:
    folder = lead_dir(lead_id)
    if not folder.exists():
        return []
    return sorted(folder.glob("photo-*.jpg"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))


def site_files(lead_id: int) -> dict[str, bytes]:
    """{'assets/logo.png': bytes, 'assets/photo-1.jpg': bytes, ...} ready to
    merge into the rendered site before it's pushed and deployed."""
    files: dict[str, bytes] = {}
    for path in ([logo(lead_id)] if logo(lead_id) else []) + photos(lead_id):
        files[f"assets/{path.name}"] = path.read_bytes()
    return files


def summary(lead_id: int) -> str:
    n = len(photos(lead_id))
    parts = []
    if logo(lead_id):
        parts.append("logo")
    if n:
        parts.append(f"{n} photo{'s' if n != 1 else ''}")
    return " + ".join(parts) if parts else "none"
