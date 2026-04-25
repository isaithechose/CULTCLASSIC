import re
import unicodedata
from pathlib import Path

from django.conf import settings


FRONTEND_COLOR_MAP = {
    "negro": "black",
    "blanco": "white",
    "griss": "gray",
    "guinda": "burgundy",
    "azul": "blue",
    "offwhite": "offwhite",
    "off white": "offwhite",
    "off_black": "off_black",
    "off black": "off_black",
    "latte": "latte",
    "slate_blue": "slate_blue",
    "slate blue": "slate_blue",
    "tan": "tan",
    "brown": "brown",
    "cream": "cream",
    "grey": "grey",
    "gray": "gray",
    "darkgrey": "darkgrey",
    "dark grey": "darkgrey",
    "shadow": "shadow",
    "mocha": "mocha",
    "camo": "camo",
    "coral": "coral",
    "black": "black",
    "white": "white",
    "burgundy": "burgundy",
}


COLOR_ALIASES = {
    "black": ["black"],
    "white": ["white"],
    "off black": ["off black", "off_black", "offblack"],
    "offblack": ["off black", "off_black", "offblack"],
    "off white": ["off white", "off_white", "offwhite"],
    "offwhite": ["off white", "off_white", "offwhite"],
    "slate blue": ["slate blue", "slate_blue"],
    "slate_blue": ["slate blue", "slate_blue"],
    "burgundy": ["burgundy", "guinda"],
    "cream": ["cream"],
    "camo": ["camo"],
    "mocha": ["mocha"],
    "shadow": ["shadow"],
    "latte": ["latte", "desert beige", "desert_beige"],
    "brown": ["brown"],
    "coral": ["coral"],
    "grey": ["grey", "gray"],
    "gray": ["grey", "gray"],
    "darkgrey": ["darkgrey", "dark grey", "dark_grey"],
    "dark grey": ["darkgrey", "dark grey", "dark_grey"],
    "tan": ["tan"],
}


def _normalize_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower().replace("_", " ").replace("-", " ")
    ascii_value = re.sub(r"[^a-z0-9\s]", " ", ascii_value)
    return " ".join(ascii_value.split())


def _product_tokens(product):
    raw_values = [product.nombre, getattr(product, "slug_imagen", "")]
    if getattr(product, "imagen", None):
        try:
            raw_values.append(Path(product.imagen.name).stem)
        except Exception:
            pass

    tokens = set()
    for value in raw_values:
        normalized = _normalize_text(value)
        if not normalized:
            continue
        tokens.update(token for token in normalized.split() if len(token) > 2)
        tokens.add(normalized)
    return tokens


def _color_candidates(color_value):
    normalized = _normalize_text(color_value)
    aliases = COLOR_ALIASES.get(normalized, [])
    candidates = {normalized}
    candidates.update(_normalize_text(alias) for alias in aliases)
    return {candidate for candidate in candidates if candidate}


def _variant_image_from_frontend_pattern(variant):
    media_root = Path(settings.MEDIA_ROOT)
    products_dir = media_root / "productos"
    if not products_dir.exists():
        return None

    slug_base = _normalize_text(getattr(variant.product, "slug_imagen", "")).replace(" ", "_")
    if not slug_base:
        return None

    normalized_color = _normalize_text(variant.color)
    mapped_color = FRONTEND_COLOR_MAP.get(normalized_color, normalized_color.replace(" ", "_"))

    candidates = [
        products_dir / f"{slug_base}_{mapped_color}_001.webp",
        products_dir / f"{slug_base}_{mapped_color}_001.png",
        products_dir / f"{slug_base}_{mapped_color}_001.jpg",
        products_dir / f"{slug_base}_{mapped_color}_001.jpeg",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.relative_to(media_root)).replace("\\", "/")
    return None


def find_best_image_for_variant(variant):
    direct_match = _variant_image_from_frontend_pattern(variant)
    if direct_match:
        return direct_match

    media_root = Path(settings.MEDIA_ROOT)
    products_dir = media_root / "productos"
    if not products_dir.exists():
        return None

    product_tokens = _product_tokens(variant.product)
    color_tokens = _color_candidates(variant.color)
    best_path = None
    best_score = -1

    for image_path in products_dir.iterdir():
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue

        normalized_name = _normalize_text(image_path.stem)
        if not any(color_token in normalized_name for color_token in color_tokens):
            continue

        score = 0
        for token in product_tokens:
            if token and token in normalized_name:
                score += max(1, len(token.split()))

        if any(token == normalized_name for token in product_tokens):
            score += 5

        if score > best_score:
            best_score = score
            best_path = image_path

    if not best_path:
        return None

    return str(best_path.relative_to(media_root)).replace("\\", "/")
