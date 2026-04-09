"""
card.py – Bilibili social-preview card builder.

Generates an 800 px wide PNG that looks like a social-media share card:

    ┌─────────────────────────────────────────────────────────────┐
    │  [Cover image – 16 : 9, with subtle bottom-fade gradient]  │
    │  (omitted entirely when no cover is available)             │
    ├─────────────────────────────────────────────────────────────┤
    │ ◎ Author name                                               │
    │                                                             │
    │ Title text – bold, up to 3 lines                            │
    │                                                             │
    │ [Category] ⬡ tag1  ⬡ tag2  ⬡ tag3  …                       │
    │            ⬡ tag4  ⬡ tag5  (additional rows as needed)     │
    │                                                             │
    │ Description line 1 …                                        │
    │ Description line 2 …                (up to desc_max_lines) │
    │                                                             │
    │ ─────────────────────────────────────────────────────────   │
    │ https://b23.tv/BVxxxx                          [QR Code]   │
    └─────────────────────────────────────────────────────────────┘

Designed to be memory-efficient on a 1 GiB RAM VM: the largest canvas
produced is roughly 800 × 1 200 px ≈ 3.8 MB RGBA in RAM before PNG
compression.

The module is intentionally generic so that future plugins (e.g. mcping)
can call ``build_bili_card`` with their own field values without any
Bilibili-specific knowledge.

CLI test (from repository root)::

    python tools/test_bili_card.py [options]
    python tools/test_bili_card.py --help
"""

from __future__ import annotations

import io
import asyncio  # Pycharm shows unused import???
from typing import Any, Optional

try:
    from nonebot.log import logger
except ImportError:
    import logging as _logging
    logger = _logging.getLogger(__name__)

from builtin.utils.image_common import (
    crop_to_size,
    fetch_image,
    get_font,
    make_circle_image,
    text_width,
    wrap_text,
    wrap_text_with_emoji,
    # Pillow stuff
    Image,
    ImageDraw,
)
from builtin.utils.emoji_render import (
    draw_text_with_emoji,
    text_width_with_emoji,
)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

CARD_W = 800
COVER_H = 450       # 16 : 9 at 800 px wide
SIDE_PAD = 20
AVATAR_SIZE = 48
QR_SIZE = 72        # 72 px is comfortably scannable; saves vertical space

# ---------------------------------------------------------------------------
# Bilibili brand palette
# ---------------------------------------------------------------------------

C_BG: tuple[int, int, int, int] = (255, 255, 255, 255)
C_TITLE: tuple[int, int, int] = (24, 25, 28)
C_AUTHOR: tuple[int, int, int] = (55, 61, 71)     # darker than before for readability
C_CAT_BG: tuple[int, int, int] = (251, 114, 153)    # Bilibili pink
C_CAT_FG: tuple[int, int, int] = (255, 255, 255)
C_TAG_BG: tuple[int, int, int] = (227, 244, 252)
C_TAG_FG: tuple[int, int, int] = (0, 161, 214)      # Bilibili link blue
C_DESC: tuple[int, int, int] = (55, 61, 71)          # darker than before for readability
C_DIVIDER: tuple[int, int, int] = (229, 233, 239)
C_URL: tuple[int, int, int] = (120, 124, 131)
C_STATS: tuple[int, int, int] = (120, 124, 131)  # same muted gray as URL

# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------

FS_TITLE = 22
FS_AUTHOR = 16
FS_CAT = 12
FS_TAG = 12
FS_DESC = 15
FS_STATS = 12
FS_URL = 12

LH_TITLE = FS_TITLE + 6     # line height for title
LH_DESC = FS_DESC + 5       # line height for description
LH_STATS = FS_STATS + 4     # line height for stats row
LH_URL = FS_URL + 4         # line height for footer URL

SECTION = 16    # large vertical gap between sections
GAP = 8         # small gap inside a section


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_bili_card(
    *,
    title: str,
    author: str = "",
    author_avatar_url: Optional[str] = None,
    cover_url: Optional[str] = None,
    category: str = "",
    tags: Optional[list[str]] = None,
    description: str = "",
    url: str,
    stats: Optional[dict[str, int]] = None,
    desc_max_lines: int = 12,
    font_path: str = "",
    font_weight: str = "medium",
    font_lang: str = "",
) -> bytes:
    """Build a Bilibili preview card and return PNG bytes.

    Parameters
    ----------
    title:
        Content title (video title, stream title, bangumi name, …).
    author:
        Uploader / streamer display name.
    author_avatar_url:
        URL of the uploader's avatar (will be circle-cropped, optional).
    cover_url:
        URL of the cover / thumbnail (optional; the cover section is omitted
        entirely when not provided or when the download fails).
    category:
        Content category label, displayed as a pink pill.
    tags:
        Tag strings, displayed as blue pills on a single row.
    description:
        Body text.  Long descriptions are wrapped and truncated at
        *desc_max_lines* rendered lines; pass ``0`` for no limit.
    url:
        Canonical URL – used for the QR code and the footer text.
    stats:
        Optional engagement metrics dict.  Recognised keys:

        * ``"view"`` / ``"like"`` / ``"coin"`` / ``"favorite"`` – video stats.
        * ``"online"`` – live-stream current viewer count.

        When provided a compact stats bar is rendered below the chips row.
    desc_max_lines:
        Maximum number of description lines rendered in the card.
        ``0`` means unlimited (use carefully for very long descriptions).
    font_path:
        Absolute path to a TrueType/OpenType font file that supports CJK.
        When empty the module searches common system paths automatically.
    font_weight:
        Requested face weight for TTC/OTC font collections: ``regular``,
        ``medium``, or ``bold``. Ignored for TTF/OTF fonts.
    font_lang:
        Preferred CJK language variant for TTC/OTC collections.  Accepts
        ``"sc"`` (Simplified Chinese), ``"tc"``, ``"jp"``, ``"kr"``,
        ``"hk"``, or ``""`` (no preference – pick first weight match).

    Returns
    -------
    bytes
        PNG image data.
    """
    tags = tags or []

    # ── Fetch remote assets concurrently ─────────────────────────────────────
    cover_img, avatar_img = await asyncio.gather(
        fetch_image(cover_url) if cover_url else _noop(),
        fetch_image(author_avatar_url) if author_avatar_url else _noop(),
    )

    # ── Prepare fonts ─────────────────────────────────────────────────────────
    f_title = get_font(FS_TITLE, font_path, font_weight=font_weight, lang_pref=font_lang)
    f_author = get_font(FS_AUTHOR, font_path, font_weight=font_weight, lang_pref=font_lang)
    f_cat = get_font(FS_CAT, font_path, font_weight=font_weight, lang_pref=font_lang)
    f_tag = get_font(FS_TAG, font_path, font_weight=font_weight, lang_pref=font_lang)
    f_desc = get_font(FS_DESC, font_path, font_weight=font_weight, lang_pref=font_lang)
    f_stats = get_font(FS_STATS, font_path, font_weight=font_weight, lang_pref=font_lang)
    f_url = get_font(FS_URL, font_path, font_weight=font_weight, lang_pref=font_lang)

    inner_w = CARD_W - 2 * SIDE_PAD

    # ── Build stats label (no extra API call – uses data already fetched) ─────
    stats_text = _format_stats(stats)
    has_stats = bool(stats_text)

    # ── Measure content so we can allocate the canvas up front ───────────────
    title_lines = wrap_text_with_emoji(title, f_title, inner_w)[:3]
    title_block_h = len(title_lines) * LH_TITLE

    all_desc = wrap_text_with_emoji(description, f_desc, inner_w) if description.strip() else []
    if desc_max_lines > 0 and len(all_desc) > desc_max_lines:
        all_desc = all_desc[:desc_max_lines]
        last = all_desc[-1]
        ellipsis = "…"
        while last and text_width_with_emoji(last + ellipsis, f_desc) > inner_w:
            last = last[:-1]
        all_desc[-1] = last + ellipsis
    desc_block_h = len(all_desc) * LH_DESC if all_desc else 0

    # Chips row: category (pink) followed by tags (blue), possibly multiple rows
    has_chips_row = bool(tags or category)
    category_chip_w = 0
    if has_chips_row:
        chip_h = max(
            (FS_CAT + 8) if category else 0,
            (FS_TAG + 8) if tags else 0,
        )
        category_chip_w = (text_width(category, f_cat) + 16) if category else 0
        # Pre-compute how tags wrap across rows.
        # Row 0 shares horizontal space with the category pill (if present).
        tag_rows: list[list[str]] = []
        if tags:
            _row: list[str] = []
            _tx = (category_chip_w + 6) if category else 0
            for _tag in tags:
                _tw = text_width(_tag, f_tag) + 16
                if _row and _tx + _tw > inner_w:
                    tag_rows.append(_row)
                    _row = []
                    _tx = 0

                if not _row and _tx > 0 and _tx + _tw > inner_w:
                    tag_rows.append([])
                    _tx = 0

                if _tx + _tw > inner_w:
                    max_text_w = max(1, inner_w - _tx - 16)
                    _tag = _fit_text_with_ellipsis(_tag, f_tag, max_text_w)
                    _tw = text_width(_tag, f_tag) + 16

                _row.append(_tag)
                _tx += _tw + 6
            if _row:
                tag_rows.append(_row)
        num_chip_rows = max(1, len(tag_rows))
        chips_row_h = num_chip_rows * (chip_h + GAP)
    else:
        chip_h = chips_row_h = 0
        tag_rows = []

    footer_h = max(QR_SIZE, LH_URL * 4)

    # Cover section is omitted entirely when the image is unavailable.
    cover_section_h = COVER_H if cover_img else 0

    info_h = (
        SECTION
        + AVATAR_SIZE           # author row
        + SECTION
        + title_block_h
        + (GAP + chips_row_h if has_chips_row else 0)
        + (GAP + LH_STATS if has_stats else 0)
        + SECTION
        + desc_block_h
        + SECTION
        + 1                     # divider line
        + SECTION
        + footer_h
        + SECTION               # bottom padding
    )

    total_h = cover_section_h + info_h

    # ── Canvas ────────────────────────────────────────────────────────────────
    card = Image.new("RGBA", (CARD_W, total_h), C_BG)
    draw = ImageDraw.Draw(card)

    # ── Cover section (omitted when unavailable) ──────────────────────────────
    if cover_img:
        cover = crop_to_size(cover_img, CARD_W, COVER_H)
        _apply_bottom_fade(cover, fade_height=90, max_alpha=130)
        card.paste(cover, (0, 0), cover)

    # ── Info section ──────────────────────────────────────────────────────────
    y = cover_section_h + SECTION

    # Author row: [avatar circle] [name]
    if avatar_img:
        av_circle = make_circle_image(avatar_img, AVATAR_SIZE)
        card.paste(av_circle, (SIDE_PAD, y), av_circle)
    name_x = SIDE_PAD + (AVATAR_SIZE + 10 if avatar_img else 0)
    if author:
        draw_text_with_emoji(
            card,
            draw,
            (name_x, y + AVATAR_SIZE // 2),
            author,
            f_author,
            C_AUTHOR,
            anchor="lm",
        )

    y += AVATAR_SIZE + SECTION

    # Title
    for line in title_lines:
        draw_text_with_emoji(card, draw, (SIDE_PAD, y), line, f_title, C_TITLE)
        y += LH_TITLE

    # Chips rows: category pill (pink, row 0 only) then tag pills (blue, multiple rows)
    if has_chips_row:
        y += GAP

        # Row 0: category pill + first batch of tags
        tx = SIDE_PAD
        chips_cy = y + chip_h // 2

        if category:
            cat_ph = FS_CAT + 8
            cat_tw = category_chip_w
            cat_y0 = chips_cy - cat_ph // 2
            draw.rounded_rectangle(
                [(tx, cat_y0), (tx + cat_tw, cat_y0 + cat_ph)],
                radius=cat_ph // 2,
                fill=C_CAT_BG,
            )
            draw.text((tx + 8, chips_cy), category, font=f_cat, fill=C_CAT_FG, anchor="lm")
            tx += cat_tw + 6

        for tag in (tag_rows[0] if tag_rows else []):
            tw = text_width(tag, f_tag) + 16
            tag_ph = FS_TAG + 8
            tag_y0 = chips_cy - tag_ph // 2
            draw.rounded_rectangle(
                [(tx, tag_y0), (tx + tw, tag_y0 + tag_ph)],
                radius=tag_ph // 2,
                fill=C_TAG_BG,
            )
            draw.text((tx + 8, chips_cy), tag, font=f_tag, fill=C_TAG_FG, anchor="lm")
            tx += tw + 6

        y += chip_h + GAP

        # Additional tag rows (row 1 onward)
        for extra_row in tag_rows[1:]:
            tx = SIDE_PAD
            chips_cy = y + chip_h // 2
            for tag in extra_row:
                tw = text_width(tag, f_tag) + 16
                tag_ph = FS_TAG + 8
                tag_y0 = chips_cy - tag_ph // 2
                draw.rounded_rectangle(
                    [(tx, tag_y0), (tx + tw, tag_y0 + tag_ph)],
                    radius=tag_ph // 2,
                    fill=C_TAG_BG,
                )
                draw.text((tx + 8, chips_cy), tag, font=f_tag, fill=C_TAG_FG, anchor="lm")
                tx += tw + 6
            y += chip_h + GAP

    # Stats bar: views · likes · coins · favorites  (video)  or  online (live)
    if has_stats:
        draw.text((SIDE_PAD, y), stats_text, font=f_stats, fill=C_STATS)
        y += GAP + LH_STATS

    y += SECTION

    # Description
    for line in all_desc:
        draw_text_with_emoji(card, draw, (SIDE_PAD, y), line, f_desc, C_DESC)
        y += LH_DESC

    y += SECTION

    # Divider
    draw.line([(SIDE_PAD, y), (CARD_W - SIDE_PAD, y)], fill=C_DIVIDER, width=1)
    y += 1 + SECTION

    # Footer: QR code on the right, URL text on the left
    qr_img = _make_qr(url, QR_SIZE)
    if qr_img:
        card.paste(qr_img, (CARD_W - SIDE_PAD - QR_SIZE, y))

    url_max_w = CARD_W - 2 * SIDE_PAD - QR_SIZE - SECTION
    url_lines = wrap_text(url, f_url, url_max_w)[:5]
    uy = y + max(0, (QR_SIZE - len(url_lines) * LH_URL) // 2)
    for ul in url_lines:
        draw.text((SIDE_PAD, uy), ul, font=f_url, fill=C_URL)
        uy += LH_URL

    # ── Flatten RGBA → RGB and encode as PNG ─────────────────────────────────
    rgb = Image.new("RGB", (CARD_W, total_h), (255, 255, 255))
    rgb.paste(card, mask=card.split()[3])
    buf = io.BytesIO()
    rgb.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _fmt_num(n: int) -> str:
    """Format an integer count compactly using CJK conventions (万 / 亿)."""
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


def _format_stats(stats: Optional[dict[str, int]]) -> str:
    """Return a compact single-line stats string, or empty string if no data."""
    if not stats:
        return ""
    parts: list[str] = []
    if "view" in stats and stats["view"] > 0:
        parts.append(f"▶ {_fmt_num(stats['view'])}")
    if "like" in stats and stats["like"] > 0:
        parts.append(f"♥ {_fmt_num(stats['like'])}")
    if "coin" in stats and stats["coin"] > 0:
        parts.append(f"＄ {_fmt_num(stats['coin'])}")
    if "favorite" in stats and stats["favorite"] > 0:
        parts.append(f"★ {_fmt_num(stats['favorite'])}")
    if "online" in stats and stats["online"] > 0:
        parts.append(f"● {_fmt_num(stats['online'])} 在线")
    return "   ".join(parts)


def _fit_text_with_ellipsis(text: str, font: Any, max_w: int) -> str:
    """Clamp text to max width and append an ellipsis when truncation is needed."""
    if max_w <= 0:
        return "…"
    if text_width(text, font) <= max_w:
        return text
    ellipsis = "…"
    if text_width(ellipsis, font) > max_w:
        return ellipsis
    out = text
    while out and text_width(out + ellipsis, font) > max_w:
        out = out[:-1]
    return (out + ellipsis) if out else ellipsis


def _apply_bottom_fade(img: "Image.Image", fade_height: int, max_alpha: int) -> None:
    """In-place: overlay a bottom-to-top dark gradient on *img* (RGBA)."""
    from PIL import Image, ImageDraw

    w, h = img.width, img.height
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    start_y = max(0, h - fade_height)
    for row in range(start_y, h):
        alpha = int(max_alpha * (row - start_y) / fade_height)
        od.line([(0, row), (w - 1, row)], fill=(0, 0, 0, alpha))
    img.alpha_composite(overlay)


def _make_qr(url: str, size: int) -> Optional["Image.Image"]:
    """Return a *size*×*size* RGBA QR-code image, or ``None`` on failure."""
    try:
        import qrcode  # type: ignore[import-untyped]
        from PIL import Image

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,  # smallest code
            box_size=3,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        qr_raw = qr.make_image(fill_color="black", back_color="white")
        # Serialise through BytesIO to obtain a clean PIL.Image instance
        buf = io.BytesIO()
        qr_raw.save(buf, format="PNG")
        buf.seek(0)
        return Image.open(buf).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception as exc:
        logger.warning(f"[bili_card] QR code generation failed: {exc}")
        return None


async def _noop() -> None:
    """Coroutine placeholder for missing optional image downloads."""
    return None
