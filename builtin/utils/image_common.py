"""
image_common.py – Shared image-processing utilities for card-generation plugins.

Provides:
  • Font discovery / loading with CJK fallback
  • Async image fetching (aiohttp)
  • Drawing helpers: circular crop, cover crop, text wrap
"""

from __future__ import annotations

import glob as _glob
import io
import os
import subprocess as _subprocess
from typing import Optional

import aiohttp
from nonebot.log import logger
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Font discovery
# ---------------------------------------------------------------------------

# Ordered list of CJK-capable font paths to probe at runtime.
# SC-specific (Simplified Chinese) OTF/TTF files are listed first to avoid
# the pan-CJK TTC defaulting to the Japanese face.
_FONT_CANDIDATES: list[str] = [
    # Linux – Noto CJK SC (Simplified Chinese) – apt: fonts-noto-cjk
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Medium.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Medium.otf",
    # Arch Linux / Manjaro – noto-fonts-cjk
    "/usr/share/fonts/noto/NotoSansCJKsc-Medium.otf",
    # Linux – pan-CJK TTC (face selected by lang_pref, defaults to JA otherwise)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Medium.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Medium.ttc",
    # Arch Linux / Manjaro – noto-fonts-cjk (flat directory layout)
    "/usr/share/fonts/noto/NotoSansCJK-Medium.ttc",
    # Arch Linux – adobe-source-han-sans-otc-fonts
    "/usr/share/fonts/adobe-source-han-sans/SourceHanSans-Medium.ttc",
    "/usr/share/fonts/adobe-source-han-sans/SourceHanSansSC-Medium.otf",
    # Fedora / RHEL / openSUSE – google-noto-cjk
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Medium.ttc",
    # Linux – WQY (apt: fonts-wqy-zenhei / fonts-wqy-microhei)
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]

_FONT_WEIGHT_STYLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "regular": ("regular", "normal", "book", "roman"),
    "medium": ("medium", "demibold", "semibold"),
    "bold": ("bold", "heavy", "black"),
}

# CJK language keywords matched against the font family name returned by getname().
_FONT_LANG_FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sc": ("cjk sc", "simplified", " sc"),
    "tc": ("cjk tc", "traditional", " tc"),
    "jp": ("cjk jp", "japanese", " jp"),
    "kr": ("cjk kr", "korean", " kr"),
    "hk": ("cjk hk", " hk"),
}

# Glob patterns searched after the static list (recursive, Linux/macOS only).
_FONT_GLOB_PATTERNS: list[str] = [
    "/usr/share/fonts/**/NotoSansCJKsc*.otf",
    "/usr/share/fonts/**/NotoSansCJKsc*.ttf",
    "/usr/share/fonts/**/NotoSansCJK*.ttc",
    "/usr/share/fonts/**/NotoSansCJK*.otf",
    "/usr/share/fonts/**/SourceHanSans*.ttc",
    "/usr/share/fonts/**/SourceHanSans*.otf",
    "/usr/share/fonts/**/wqy-zenhei.ttc",
    "/usr/share/fonts/**/wqy-microhei.ttc",
]

# Module-level cache: resolved path ("" means searched but not found)
_resolved_font_path: str | None = None
# (font_path_key, size) → PIL font object
_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
_DEFAULT_FONT_CACHE_KEY = "__default_aa__"
_ttc_face_index_cache: dict[tuple[str, str, str], int] = {}
# Upper bound for TTC/OTC face probing; enough for common CJK collections.
_MAX_TTC_FACE_PROBE = 64


def _find_via_fontconfig() -> Optional[str]:
    """Use fontconfig's ``fc-match`` to locate a CJK-capable font (Linux only)."""
    try:
        for query in (":lang=zh", ":lang=ja", ":lang=ko"):
            result = _subprocess.run(
                ["fc-match", "--format=%{file}", query],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                path = result.stdout.strip()
                if path and os.path.exists(path):
                    return path
    except Exception:
        pass
    return None


def _resolve_font_path(custom: str = "") -> Optional[str]:
    """Return the best available font path.

    Priority: *custom* (if the file exists) → static ``_FONT_CANDIDATES`` list
    → glob patterns in ``_FONT_GLOB_PATTERNS`` → ``fc-match`` (fontconfig)
    → ``None`` (Pillow built-in bitmap fallback will be used).
    """
    global _resolved_font_path

    if custom and os.path.exists(custom):
        return custom

    if _resolved_font_path is None:
        # 1. Static candidate paths
        for candidate in _FONT_CANDIDATES:
            if os.path.exists(candidate):
                logger.info(f"[image_common] CJK font found: {candidate}")
                _resolved_font_path = candidate
                break

        # 2. Glob patterns (catches distro-specific naming / sub-directories)
        if not _resolved_font_path:
            for pattern in _FONT_GLOB_PATTERNS:
                matches = sorted(_glob.glob(pattern, recursive=True))
                if matches:
                    logger.info(f"[image_common] CJK font found via glob: {matches[0]}")
                    _resolved_font_path = matches[0]
                    break

        # 3. fontconfig fc-match (most reliable on any Linux with fontconfig)
        if not _resolved_font_path:
            fc_path = _find_via_fontconfig()
            if fc_path:
                logger.info(f"[image_common] CJK font found via fc-match: {fc_path}")
                _resolved_font_path = fc_path

        if not _resolved_font_path:
            logger.warning(
                "[image_common] No CJK font found. Image cards may display □ for "
                "Chinese/Japanese/Korean characters. "
                "Fix: install fonts-noto-cjk, or set CARD_FONT in .env "
                "to a TrueType/OpenType/TTC font for anti-aliased text."
            )
            _resolved_font_path = ""

    return _resolved_font_path or None


def _select_ttc_face_index(path: str, font_weight: str, lang_pref: str = "") -> int:
    cache_key = (path, font_weight, lang_pref)
    cached = _ttc_face_index_cache.get(cache_key)
    if cached is not None:
        return cached

    # _Config already validates values; this fallback is defensive only.
    default_keywords = _FONT_WEIGHT_STYLE_KEYWORDS.get("medium", ("medium",))
    weight_kws = _FONT_WEIGHT_STYLE_KEYWORDS.get(font_weight, default_keywords)
    lang_kws = _FONT_LANG_FAMILY_KEYWORDS.get(lang_pref.lower(), ())

    weight_match_idx: Optional[int] = None        # first face matching weight only
    weight_lang_match_idx: Optional[int] = None   # first face matching both

    for idx in range(_MAX_TTC_FACE_PROBE):
        try:
            probe_font = ImageFont.truetype(path, size=12, index=idx)
        except OSError:
            logger.debug(f"[image_common] TTC probe ended at index {idx} for {path}")
            break
        except Exception as exc:
            logger.debug(f"[image_common] TTC probe failed at index {idx} for {path}: {exc}")
            break
        try:
            family_name, style_name = probe_font.getname()
        except Exception:
            family_name = style_name = ""
        style_cf = style_name.casefold()
        family_cf = family_name.casefold()

        weight_match = any(kw in style_cf for kw in weight_kws)
        lang_match = bool(lang_kws) and any(kw in family_cf for kw in lang_kws)

        if weight_match:
            if weight_match_idx is None:
                weight_match_idx = idx
            if lang_match:
                weight_lang_match_idx = idx
                break

    if weight_lang_match_idx is not None:
        chosen = weight_lang_match_idx
    elif weight_match_idx is not None:
        chosen = weight_match_idx
    else:
        chosen = 0

    _ttc_face_index_cache[cache_key] = chosen
    logger.debug(
        f"[image_common] TTC face selected for weight={font_weight!r} "
        f"lang={lang_pref!r}: {path}#{chosen}"
    )
    return chosen


def get_font(
    size: int,
    font_path: str = "",
    font_weight: str = "medium",
    lang_pref: str = "",
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Return a PIL font at *size* pixels, preferring a CJK-capable face.

    Parameters
    ----------
    size:
        Font size in pixels.
    font_path:
        Override path to a font file.  Auto-detected when empty.
    font_weight:
        Preferred face weight for TTC/OTC collections: ``"regular"``,
        ``"medium"``, or ``"bold"``.  Ignored for TTF/OTF fonts.
    lang_pref:
        Preferred CJK language variant for TTC/OTC collections.  Accepts
        ``"sc"`` (Simplified Chinese), ``"tc"``, ``"jp"``, ``"kr"``,
        ``"hk"``, or ``""`` (no preference – pick first weight match).
    """
    cache_font_path = f"{font_path or _DEFAULT_FONT_CACHE_KEY}:{font_weight}:{lang_pref}"
    cache_key = (cache_font_path, size)
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    path = _resolve_font_path(font_path)
    font = None
    if path:
        try:
            ext = os.path.splitext(path)[1].casefold()
            if ext in (".ttc", ".otc"):
                index = _select_ttc_face_index(path, font_weight, lang_pref)
                font = ImageFont.truetype(path, size, index=index)
            else:
                font = ImageFont.truetype(path, size)
        except Exception as exc:
            logger.warning(f"[image_common] Font load error ({path}): {exc}")

    if font is None:
        # Pillow >= 10 supports the size keyword; older versions do not.
        font = ImageFont.load_default(size=size)

    _font_cache[cache_key] = font
    return font


# ---------------------------------------------------------------------------
# Image fetching
# ---------------------------------------------------------------------------

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0)"
        " Gecko/20100101 Firefox/120.0"
    ),
    "Referer": "https://www.bilibili.com/",
}


async def fetch_image(url: str, timeout: int = 15) -> Optional[Image.Image]:
    """Download *url* and return an RGBA ``PIL.Image``, or ``None`` on failure."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=_HTTP_HEADERS,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"[image_common] HTTP {resp.status} fetching {url}")
                    return None
                data = await resp.read()
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as exc:
        logger.warning(f"[image_common] Failed to fetch image {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


def make_circle_image(img: Image.Image, size: int) -> Image.Image:
    """Resize *img* to *size*×*size* and apply a circular mask."""
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, mask=mask)
    return result


def crop_to_size(img: Image.Image, width: int, height: int) -> Image.Image:
    """Scale *img* to cover *width*×*height*, then center-crop exactly."""
    img = img.convert("RGBA")
    scale = max(width / img.width, height / img.height)
    new_w = max(width, int(img.width * scale))
    new_h = max(height, int(img.height * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return img.crop((left, top, left + width, top + height))


def text_width(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    """Return the pixel width of *text* rendered with *font*."""
    try:
        return int(font.getlength(text))
    except AttributeError:
        return int(font.getbbox(text)[2])


def wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """Character-level word-wrap for *text* constrained to *max_width* pixels.

    Handles explicit ``\\n`` newlines.  Suitable for CJK content (where breaking
    at any character boundary is correct) as well as Latin text.
    """
    result: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            result.append("")
            continue
        line = ""
        for ch in paragraph:
            candidate = line + ch
            if text_width(candidate, font) > max_width and line:
                result.append(line)
                line = ch
            else:
                line = candidate
        if line:
            result.append(line)
    return result


def wrap_text_with_emoji(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """Character-level word-wrap that accounts for Twemoji PNG widths.

    Identical to :func:`wrap_text` except that emoji sequences are measured
    using :func:`builtin.utils.emoji_render.text_width_with_emoji` so that
    the wrap budget correctly accounts for composited emoji images.
    """
    from builtin.utils.emoji_render import text_width_with_emoji

    result: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            result.append("")
            continue
        line = ""
        for ch in paragraph:
            candidate = line + ch
            if text_width_with_emoji(candidate, font) > max_width and line:
                result.append(line)
                line = ch
            else:
                line = candidate
        if line:
            result.append(line)
    return result
