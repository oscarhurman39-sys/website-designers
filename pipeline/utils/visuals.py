"""Niche-specific art direction and safe AI hero-image generation."""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from huggingface_hub import InferenceClient

import config


@dataclass(frozen=True)
class ArtDirection:
    label: str
    eyebrow: str
    tagline: str
    subject: str
    palette: str
    accent: str
    accent_2: str
    ink: str
    paper: str
    image_position: str = "center"


_DIRECTIONS = {
    "landscaper": ArtDirection(
        "Landscaping", "Outdoor spaces, thoughtfully cared for", "Bring the outside in",
        "extreme macro close-up of layered fern fronds, glossy leaves, seed pods and dew; purely botanical, no visible garden",
        "deep leaf green, electric chartreuse, soft lilac and clean off-white",
        "#b8f238", "#a997ff", "#12251b", "#f5f4ea", "center 42%",
    ),
    "plasterer": ArtDirection(
        "Plastering", "Clean finishes, calm spaces", "A better finish starts here",
        "extreme macro study of polished lime plaster, mineral bloom and elegant trowel texture; abstract surface only",
        "chalk white, mineral grey, shell pink and sharp cobalt",
        "#3155ff", "#ef9fae", "#202124", "#f4f1ec",
    ),
    "vehicle-repair": ArtDirection(
        "Vehicle Repair", "Practical care for everyday motoring", "Keep things moving",
        "editorial macro still life of clean nuts, bolts, screw threads, washers and a subtle iridescent motor-oil sheen; no vehicle",
        "graphite black, bright silver, signal red and petrol blue",
        "#ff4d33", "#36c5d8", "#111315", "#f3f0e8",
    ),
    "plumber": ArtDirection(
        "Plumbing", "Straightforward help when it matters", "Flow, fixed",
        "macro composition of copper pipe fittings, brass threads and crisp water refraction; sculptural parts only, not installed plumbing",
        "burnished copper, water blue, lemon yellow and warm white",
        "#17a8c9", "#ffd447", "#17252c", "#f6f2e9", "center 55%",
    ),
    "electrician": ArtDirection(
        "Electrical", "Clear solutions for homes and businesses", "Power, properly handled",
        "extreme macro of braided cable, copper filaments, ceramic insulators and clean geometric shadows; components only",
        "ink black, warm copper, safety yellow and vivid cyan",
        "#ffd21f", "#20c7d9", "#15181a", "#f4f2e9",
    ),
    "cafe": ArtDirection(
        "Cafe", "Good things, served daily", "Your everyday favourite",
        "macro editorial still life of coffee crema, roasted beans, glazed ceramic and folded paper shapes; no cafe interior and no branded cup",
        "espresso, cherry red, sky blue and milky white",
        "#ee4b3b", "#72c9f3", "#2c211d", "#f7f0e5", "center 48%",
    ),
    "restaurant": ArtDirection(
        "Restaurant", "A place worth gathering around", "Come hungry",
        "close-up editorial arrangement of raw herbs, citrus peel, peppercorns, linen and polished cutlery; ingredients and texture, not a finished dish",
        "tomato red, herb green, butter yellow and crisp white",
        "#e83e31", "#e8c938", "#27201d", "#f7f1e7",
    ),
    "salon": ArtDirection(
        "Hair & Beauty", "A little time for yourself", "Fresh energy, your way",
        "abstract macro composition of satin ribbon, chrome scissors silhouette, translucent comb teeth and glossy colour swatches; no person or hairstyle",
        "black cherry, silver, candy pink and cool mint",
        "#f0528d", "#77d9c1", "#291922", "#f7f1f3",
    ),
    "dentist": ArtDirection(
        "Dental Care", "Clear, welcoming dental care", "Feel good about your smile",
        "clean abstract macro still life of translucent glass, white ceramic curves, brushed steel and mint-coloured light; no teeth, mouth, patient or procedure",
        "clean white, mint, cobalt and polished silver",
        "#255ee8", "#64d5b4", "#18222b", "#f5f7f4",
    ),
    "gym": ArtDirection(
        "Fitness", "Move well. Feel stronger.", "Built for momentum",
        "kinetic macro arrangement of textured rubber, chalk dust, knurled steel and bright athletic tape; equipment details only, no people or gym interior",
        "charcoal, acid lime, electric blue and bright white",
        "#c7f43d", "#3987ff", "#151719", "#f3f2ed",
    ),
}

_ALIASES = {
    "gardener": "landscaper", "gardening": "landscaper", "landscape": "landscaper",
    "landscaping": "landscaper", "plastering": "plasterer", "mechanic": "vehicle-repair",
    "garage": "vehicle-repair", "car-repair": "vehicle-repair", "auto-repair": "vehicle-repair",
    "hairdresser": "salon", "beauty": "salon", "fitness": "gym",
}

_DEFAULT = ArtDirection(
    "Local Service", "Local help, made simple", "Ready when you are",
    "abstract macro still life of honest trade materials and tactile tools associated with the service; isolated details only",
    "charcoal, warm white, punchy coral and cool blue",
    "#ff5b45", "#4f9cff", "#171a1d", "#f5f1e8",
)

_NEGATIVE_PROMPT = (
    "people, faces, hands, worker, tradesperson, customer, finished project, completed job, "
    "before and after, garden, hedge, lawn, room, house, building, shopfront, garage interior, "
    "vehicle, branded uniform, logo, company name, readable text, signage, watermark, testimonial, "
    "stock photo, corporate stock photography, fake portfolio, low resolution, blur, clutter"
)


def normalized_niche(niche: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (niche or "").strip().lower()).strip("-")
    return _ALIASES.get(slug, slug)


def art_direction(niche: str) -> dict[str, str]:
    direction = _DIRECTIONS.get(normalized_niche(niche), _DEFAULT)
    values = asdict(direction)
    if direction is _DEFAULT and niche:
        values["label"] = niche.replace("-", " ").replace("_", " ").title()
    return values


def image_prompt(lead: dict) -> str:
    direction = art_direction(lead.get("niche", ""))
    return (
        "Use case: ads-marketing\n"
        "Asset type: full-bleed website hero background\n"
        f"Primary request: {direction['subject']}.\n"
        "Style/medium: premium editorial macro photography blended with playful late-1980s and "
        "early-1990s design-magazine energy; sleek, tactile, slightly surreal, professionally art directed.\n"
        "Composition/framing: landscape 16:9, close crop, layered depth, one strong focal area, "
        "generous darker negative space on the left for white website copy.\n"
        "Lighting/mood: crisp studio light, confident, energetic, refined rather than childish.\n"
        f"Color palette: {direction['palette']}.\n"
        "Constraints: depict only materials, ingredients, components or natural texture associated "
        "with the trade. It must read as visual atmosphere, never documentary evidence of the "
        "company's premises, staff, customers or completed work. No text, logo or watermark."
    )


def negative_prompt() -> str:
    return _NEGATIVE_PROMPT


def hero_image_data_uri(lead: dict) -> str:
    """Generate or reuse a safe hero. Empty means use the designed fallback."""
    if not config.ENABLE_AI_IMAGES or not config.HF_API_TOKEN:
        return ""

    prompt = image_prompt(lead)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    lead_id = int(lead.get("id") or 0)
    cache_dir = Path(config.GENERATED_ASSETS_DIR)
    cache_path = cache_dir / f"lead-{lead_id}-{prompt_hash}.jpg"

    if not cache_path.exists():
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            seed_source = f"{lead_id}:{lead.get('business_name', '')}:{lead.get('niche', '')}"
            seed = int(hashlib.sha256(seed_source.encode("utf-8")).hexdigest()[:8], 16)
            client = InferenceClient(model=config.HF_IMAGE_MODEL, token=config.HF_API_TOKEN)
            image = client.text_to_image(
                prompt,
                negative_prompt=_NEGATIVE_PROMPT,
                width=config.AI_IMAGE_WIDTH,
                height=config.AI_IMAGE_HEIGHT,
                num_inference_steps=config.AI_IMAGE_STEPS,
                guidance_scale=config.AI_IMAGE_GUIDANCE,
                seed=seed,
            )
            image.convert("RGB").save(cache_path, format="JPEG", quality=88, optimize=True)
        except Exception as exc:  # noqa: BLE001 - imagery must not block a viable preview
            print(f"[visuals] AI hero generation failed; using designed fallback: {exc}")
            return ""

    encoded = base64.b64encode(cache_path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"

