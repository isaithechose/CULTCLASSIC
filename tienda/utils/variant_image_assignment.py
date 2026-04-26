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
    "concrete": "concrete",
    "darknavy": "darknavy",
    "dark navy": "darknavy",
    "desertbeige": "desertbeige",
    "desert beige": "desertbeige",
    "offblack": "offblack",
    "off black": "off_black",
    "utilitygreen": "utilitygreen",
    "utility green": "utilitygreen",
}


COLOR_ALIASES = {
    "black": ["black", "oversizedtee black"],
    "white": ["white"],
    "off black": ["off black", "off_black", "offblack"],
    "offblack": ["off black", "off_black", "offblack"],
    "off white": ["off white", "off_white", "offwhite"],
    "offwhite": ["off white", "off_white", "offwhite"],
    "slate blue": ["slate blue", "slate_blue"],
    "slate_blue": ["slate blue", "slate_blue"],
    "burgundy": ["burgundy", "guinda", "dark burgundy", "dark_burgundy"],
    "cream": ["cream"],
    "camo": ["camo"],
    "mocha": ["mocha"],
    "shadow": ["shadow"],
    "latte": ["latte", "desert beige", "desert_beige"],
    "desertbeige": ["desertbeige", "desert beige", "desert_beige"],
    "desert beige": ["desertbeige", "desert beige", "desert_beige"],
    "brown": ["brown"],
    "coral": ["coral"],
    "grey": ["grey", "gray"],
    "gray": ["grey", "gray"],
    "darkgrey": ["darkgrey", "dark grey", "dark_grey"],
    "dark grey": ["darkgrey", "dark grey", "dark_grey"],
    "darknavy": ["darknavy", "dark navy", "dark_navy"],
    "dark navy": ["darknavy", "dark navy", "dark_navy"],
    "tan": ["tan"],
    "concrete": ["concrete"],
    "utilitygreen": ["utilitygreen", "utility green", "utility_green"],
    "utility green": ["utilitygreen", "utility green", "utility_green"],
}

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


EXACT_VARIANT_IMAGE_CANDIDATES = {
    "max_overweight_oversized": {
        "black": [
            "OversizedTee_Black_002.webp",
            "max_overweight_oversized_black_001.webp",
        ],
        "burgundy": [
            "OversizedTee_DarkBurgundy_002.webp",
            "max_overweight_oversized_burgundy_001.webp",
        ],
        "concrete": ["OversizedTee_concrete_002.webp"],
        "darknavy": ["OversizedTee_DarkNavy_002.webp"],
        "desertbeige": ["OversizedTee_DesertBeige_002.webp"],
        "offblack": ["OversizedTee_OffBlack_002.webp"],
        "off_black": [
            "OversizedTee_OffBlack_002.webp",
            "max_overweight_oversized_off_black_001.webp",
        ],
        "utilitygreen": ["OversizedTee_UtilityGreen_002.webp"],
        "white": ["max_overweight_oversized_white_001.webp"],
        "offwhite": ["max_overweight_oversized_offwhite_001.webp"],
        "latte": ["max_overweight_oversized_latte_001.webp"],
        "slate_blue": ["max_overweight_oversized_slate_blue_001.webp"],
    }
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


def _canonical_color_key(color_value):
    normalized = _normalize_text(color_value)
    mapped = FRONTEND_COLOR_MAP.get(normalized, normalized.replace(" ", "_"))
    return mapped


def _exact_variant_image(variant):
    media_root = Path(settings.MEDIA_ROOT)
    products_dir = media_root / "productos"
    if not products_dir.exists():
        return None

    slug_base = _normalize_text(getattr(variant.product, "slug_imagen", "")).replace(" ", "_")
    color_key = _canonical_color_key(variant.color)
    filenames = EXACT_VARIANT_IMAGE_CANDIDATES.get(slug_base, {}).get(color_key, [])

    for filename in filenames:
        candidate = products_dir / filename
        if candidate.exists():
            return str(candidate.relative_to(media_root)).replace("\\", "/")

    return None


def thumbnail_name_for_image(image_name):
    if not image_name:
        return None

    image_path = Path(str(image_name))
    return str(image_path.with_name(f"{image_path.stem}_thumb.webp")).replace("\\", "/")


def existing_thumbnail_or_image_name(image_name):
    if not image_name:
        return None

    media_root = Path(settings.MEDIA_ROOT)
    thumb_name = thumbnail_name_for_image(image_name)
    if thumb_name and (media_root / thumb_name).exists():
        return thumb_name

    return str(image_name).replace("\\", "/")


def _variant_image_from_frontend_pattern(variant):
    media_root = Path(settings.MEDIA_ROOT)
    products_dir = media_root / "productos"
    if not products_dir.exists():
        return None

    slug_base = _normalize_text(getattr(variant.product, "slug_imagen", "")).replace(" ", "_")
    if not slug_base:
        return None

    normalized_color = _normalize_text(variant.color)
    mapped_color = _canonical_color_key(normalized_color)

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


def get_variant_display_image_name(variant):
    if getattr(variant, "imagen", None):
        try:
            if variant.imagen.name:
                return variant.imagen.name
        except Exception:
            pass

    exact_match = _exact_variant_image(variant)
    if exact_match:
        return exact_match

    direct_match = _variant_image_from_frontend_pattern(variant)
    if direct_match:
        return direct_match

    if getattr(variant.product, "imagen", None):
        try:
            if variant.product.imagen.name:
                return variant.product.imagen.name
        except Exception:
            pass

    return None


def find_best_image_for_variant(variant):
    exact_match = _exact_variant_image(variant)
    if exact_match:
        return exact_match

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
        if image_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
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
