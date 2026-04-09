"""
card.py – Minecraft-style server status card builder.

Generates a PNG image that resembles the in-game server list entry:

    ┌────────────────────────────────────────────────────────────┐  ← dirt bg
    │ ████████  Server Address                            [JE]   │
    │ ████████  Server Version (gray)                            │
    │  icon     §aColoured §bMOTD §oitalic §nunderline §mstrike  │
    │   64px    §7MOTD line 2                                    │
    │           Player1   Player2   Player3   …                  │
    │           Ping: 42 ms  ▌▌▌▌▌            12 / 100           │
    └────────────────────────────────────────────────────────────┘

§-formatting codes fully rendered: colours, bold, italic (affine shear),
underline, strikethrough.  Obfuscated (§k) shows plain text in static images.

CLI test (from repository root)::

    python tools/test_mc_card.py [options]
    python tools/test_mc_card.py --help
"""

from __future__ import annotations

import base64
import io
import random
from dataclasses import dataclass, field
from typing import Optional

try:
    from nonebot.log import logger
except ImportError:
    import logging as _logging
    logger = _logging.getLogger(__name__)

from builtin.utils.image_common import (
    Image,
    ImageDraw,
    get_font,
    text_width,
)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

CARD_W = 600
SIDE_PAD = 16
ICON_SIZE = 64
TEXT_X = SIDE_PAD + ICON_SIZE + 12   # left edge of the text block

FS_NAME   = 16
FS_VER    = 13
FS_MOTD   = 14
FS_FOOTER = 12
FS_SAMPLE = 12

LH_VER    = FS_VER    + 4
LH_MOTD   = FS_MOTD   + 4
LH_FOOTER = FS_FOOTER + 4
LH_SAMPLE = FS_SAMPLE + 3

# Ping-bar geometry – max height equals FS_FOOTER so bars never overflow the
# footer row.  The bottom anchor (cy) is passed as y + FS_FOOTER.
_PING_N         = 5
_PING_BAR_W     = 4
_PING_BAR_GAP   = 2
_PING_BAR_MAX_H = FS_FOOTER   # 12 px – tallest bar sits exactly within the row

# Italic shear factor (horizontal pixels per vertical pixel, ~12°)
_ITALIC_SHEAR = 0.22

try:
    _RESAMPLE_LANCZOS = getattr(Image, "Resampling").LANCZOS
    _RESAMPLE_BILINEAR = getattr(Image, "Resampling").BILINEAR
except Exception:
    _RESAMPLE_LANCZOS = getattr(Image, "LANCZOS")
    _RESAMPLE_BILINEAR = getattr(Image, "BILINEAR")

try:
    _AFFINE = getattr(Image, "Transform").AFFINE
except Exception:
    _AFFINE = getattr(Image, "AFFINE")

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

# --- Dirt-tile background ------------------------------------------------
# 16×16 colour-index grid (4 tones, deterministic)
_DIRT_GRID: list[int] = [
    2,1,3,2,1,3,2,1, 3,1,2,3,1,2,3,2,
    1,3,2,1,3,1,2,3, 2,1,3,2,1,3,2,1,
    3,2,1,3,2,1,3,1, 2,3,1,2,3,1,2,3,
    2,1,3,2,1,2,3,2, 1,3,2,1,3,2,1,2,
    1,3,2,1,3,2,1,3, 2,1,3,2,1,3,2,1,
    3,2,1,3,1,3,2,1, 3,2,1,3,2,1,3,2,
    2,1,3,2,1,2,1,3, 2,1,2,3,1,2,3,1,
    1,2,3,1,3,1,3,2, 1,3,2,1,3,2,1,3,
    3,1,2,3,2,3,2,1, 3,2,1,2,3,1,3,2,
    2,3,1,2,1,2,1,3, 1,3,2,3,2,3,2,1,
    1,2,3,1,3,1,3,2, 3,1,3,1,2,1,3,3,
    3,1,2,3,2,3,1,3, 1,3,1,3,1,3,1,2,
    2,3,1,2,1,2,3,2, 3,2,3,2,3,2,3,1,
    1,2,3,1,3,1,2,1, 2,1,2,1,2,1,2,3,
    3,1,2,3,2,3,1,3, 1,3,1,3,1,3,1,2,
    2,3,1,2,1,2,3,2, 3,2,3,1,2,1,2,3,
]
_DIM = 0.34   # keep dirt subtle but clearly visible around the panel frame
_DIRT_TONES: list[tuple[int, int, int]] = [
    (int(144 * _DIM), int(103 * _DIM), int(73 * _DIM)),
    (int(134 * _DIM), int(96  * _DIM), int(67 * _DIM)),
    (int(122 * _DIM), int(87  * _DIM), int(59 * _DIM)),
    (int(111 * _DIM), int(77  * _DIM), int(51 * _DIM)),
]

# --- Panel (Minecraft-GUI slot style) -----------------------------------
C_PANEL_BG: tuple[int, int, int] = (33,  33,  50 )   # base fill
C_PANEL_HI: tuple[int, int, int] = (88,  88,  128)   # top/left highlight (2 px)
C_PANEL_SH: tuple[int, int, int] = (16,  16,  26 )   # bottom/right shadow (2 px)

# --- Text ---------------------------------------------------------------
C_NAME:    tuple[int, int, int] = (255, 255, 255)
C_VER:     tuple[int, int, int] = (85,  85,  85 )
C_PLAYERS: tuple[int, int, int] = (170, 170, 170)
C_FOOTER:  tuple[int, int, int] = (85,  85,  85 )
C_SAMPLE:  tuple[int, int, int] = (170, 170, 170)

# --- Ping bars ----------------------------------------------------------
C_PING_GOOD:  tuple[int, int, int] = (85,  255, 85 )   # < 150 ms
C_PING_OK:    tuple[int, int, int] = (255, 255, 85 )   # 150-300 ms
C_PING_SLOW:  tuple[int, int, int] = (255, 170, 0  )   # 300-600 ms
C_PING_BAD:   tuple[int, int, int] = (255, 85,  85 )   # > 600 ms
C_PING_EMPTY: tuple[int, int, int] = (28,  28,  44 )   # unfilled segment

# --- Minecraft § colour codes -------------------------------------------
MC_COLOR_RGB: dict[str, tuple[int, int, int]] = {
    "0": (0,   0,   0  ),   # black
    "1": (0,   0,   170),   # dark_blue
    "2": (0,   170, 0  ),   # dark_green
    "3": (0,   170, 170),   # dark_aqua
    "4": (170, 0,   0  ),   # dark_red
    "5": (170, 0,   170),   # dark_purple
    "6": (255, 170, 0  ),   # gold
    "7": (170, 170, 170),   # gray
    "8": (85,  85,  85 ),   # dark_gray
    "9": (85,  85,  255),   # blue
    "a": (85,  255, 85 ),   # green
    "b": (85,  255, 255),   # aqua
    "c": (255, 85,  85 ),   # red
    "d": (255, 85,  255),   # light_purple
    "e": (255, 255, 85 ),   # yellow
    "f": (255, 255, 255),   # white
    "g": (221, 214, 5  ),   # minecoin_gold (Bedrock)
}

# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------


def _make_dirt_tile() -> Image.Image:
    """Build the 16 × 16 dim dirt pattern tile (created lazily)."""
    tile = Image.new("RGB", (16, 16))
    px = tile.load()
    for idx, tone in enumerate(_DIRT_GRID):
        row, col = divmod(idx, 16)
        px[col, row] = _DIRT_TONES[tone]
    return tile


def _draw_dirt_bg(canvas: Image.Image) -> None:
    """Tile the dirt pattern across the full canvas."""
    tile = _make_dirt_tile()
    tw, th = tile.size
    cw, ch = canvas.size
    for ty in range(0, ch, th):
        for tx in range(0, cw, tw):
            canvas.paste(tile, (tx, ty))


def _draw_mc_panel(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
) -> None:
    """Draw a Minecraft-style framed slot without covering the dirt background.

    The old version used a large solid fill, which read as a blue button and
    visually fought the content. This version keeps only a beveled outline so
    the dirt texture remains the main background.
    """
    # Outer frame: light top/left, dark bottom/right.
    draw.rectangle([(x0, y0), (x1, y0)], fill=C_PANEL_HI)
    draw.rectangle([(x0, y0), (x0, y1)], fill=C_PANEL_HI)
    draw.rectangle([(x0, y1), (x1, y1)], fill=C_PANEL_SH)
    draw.rectangle([(x1, y0), (x1, y1)], fill=C_PANEL_SH)

    # Inner bevel to make it feel closer to a Minecraft GUI slot.
    if x1 - x0 > 4 and y1 - y0 > 4:
        draw.rectangle([(x0 + 2, y0 + 2), (x1 - 2, y0 + 2)], fill=(60, 60, 90))
        draw.rectangle([(x0 + 2, y0 + 2), (x0 + 2, y1 - 2)], fill=(60, 60, 90))
        draw.rectangle([(x0 + 2, y1 - 2), (x1 - 2, y1 - 2)], fill=(24, 24, 36))
        draw.rectangle([(x1 - 2, y0 + 2), (x1 - 2, y1 - 2)], fill=(24, 24, 36))


# Characters used to replace §k (obfuscated) text.  Chosen to look visually
# "busy" and non-sensical – intentionally unreadable, consistent with the
# in-game effect.  A fixed-seed RNG makes renders deterministic.
_OBFUSCATED_CHARS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    "0123456789!@#$%&*()_+-=[]{}|;:,./<>?~`^"
    "▒▓░▌▐▀▄■□●◆★♦♠♣♥"
)


def _obfuscate(text: str) -> str:
    """Replace each character in *text* with a non-sensical symbol.

    The replacement is deterministic (seeded by the text itself) so the same
    MOTD always renders identically.  The original character count is preserved
    so width and layout remain unchanged.
    """
    rng = random.Random(hash(text) & 0xFFFF_FFFF)
    return "".join(rng.choice(_OBFUSCATED_CHARS) for _ in text)





@dataclass
class _Span:
    text: str
    color: tuple[int, int, int] = field(default_factory=lambda: (255, 255, 255))
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False


def _motd_to_spans(
    parsed: list,
    default_color: tuple[int, int, int] = (255, 255, 255),
    *,
    prefer_legacy_java_styles: bool = True,
) -> list[_Span]:
    """Convert a ``Motd.parsed`` list into a flat list of :class:`_Span` objects.

    Colour codes reset all active formatting flags (vanilla behaviour).
    §k (obfuscated) replaces each character with a non-sensical symbol so the
    text is intentionally unreadable, matching the in-game visual intent.
    """
    try:
        from mcstatus.motd.components import Formatting, MinecraftColor, WebColor
    except ImportError:
        return [_Span(str(p), default_color) for p in parsed if str(p)]

    color = default_color
    bold = italic = underline = strikethrough = obfuscated = False
    spans: list[_Span] = []

    # mcstatus>=13 reuses 'm'/'n' for Bedrock material colors, which collides
    # with Java legacy style codes (§m/§n). Prefer Java style semantics unless
    # the caller explicitly opts out (e.g. Bedrock rendering).
    legacy_style_alias = {"l", "o", "n", "m", "k", "r"}

    for item in parsed:
        if isinstance(item, MinecraftColor):
            code = item.value
            if prefer_legacy_java_styles and code in legacy_style_alias and code not in MC_COLOR_RGB:
                if code == "r":
                    color = default_color
                    bold = italic = underline = strikethrough = obfuscated = False
                elif code == "l":
                    bold = True
                elif code == "o":
                    italic = True
                elif code == "n":
                    underline = True
                elif code == "m":
                    strikethrough = True
                elif code == "k":
                    obfuscated = True
                continue

            color = MC_COLOR_RGB.get(code, default_color)
            bold = italic = underline = strikethrough = obfuscated = False
        elif isinstance(item, WebColor):
            color = item.rgb
            bold = italic = underline = strikethrough = obfuscated = False
        elif isinstance(item, Formatting):
            v = item.value
            if v == "r":
                color = default_color
                bold = italic = underline = strikethrough = obfuscated = False
            elif v == "l":
                bold = True
            elif v == "o":
                italic = True
            elif v == "n":
                underline = True
            elif v == "m":
                strikethrough = True
            elif v == "k":
                obfuscated = True
        elif isinstance(item, str) and item:
            text = _obfuscate(item) if obfuscated else item
            spans.append(_Span(text, color, bold, italic, underline, strikethrough))

    return spans


def _split_spans_by_newline(spans: list[_Span]) -> list[list[_Span]]:
    """Split spans into logical MOTD lines wherever text contains ``\\n``."""
    lines: list[list[_Span]] = [[]]
    for span in spans:
        parts = span.text.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                lines.append([])
            if part:
                lines[-1].append(
                    _Span(part, span.color, span.bold, span.italic,
                          span.underline, span.strikethrough)
                )
    return lines


# ---------------------------------------------------------------------------
# Italic shear helper
# ---------------------------------------------------------------------------


def _draw_italic_chunk(
    canvas: Image.Image,
    text: str,
    x: int,
    y: int,
    font,
    color: tuple[int, int, int],
    size: int,
) -> None:
    """Render *text* with a right-lean shear (§o italic effect) onto *canvas*.

    The bottom edge of the glyph is anchored at x; the top leans right by
    ``_ITALIC_SHEAR * height`` pixels.  Implemented via PIL affine transform
    on a temporary RGBA surface.
    """
    tw = text_width(text, font)
    if not tw:
        return

    h = size + 6                        # buffer includes descenders
    lean_px = int(_ITALIC_SHEAR * h)   # rightward shift at the top
    tmp_w = tw + lean_px + 4

    tmp = Image.new("RGBA", (tmp_w, h), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((0, 0), text, font=font, fill=color)

    # Inverse affine for bottom-anchor right-lean:
    #   x_src = x_dst + SHEAR * y_dst - lean_px
    # At y_dst=0 (top): x_src = x_dst - lean_px  → top content shifted right
    # At y_dst=h (bottom): x_src = x_dst           → bottom unchanged
    data = (1, _ITALIC_SHEAR, -lean_px, 0, 1, 0)
    sheared = tmp.transform(tmp.size, _AFFINE, data, _RESAMPLE_BILINEAR)
    canvas.paste(sheared, (x, y), sheared)


# ---------------------------------------------------------------------------
# Span renderer
# ---------------------------------------------------------------------------


def _render_spans(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    spans: list[_Span],
    x: int,
    y: int,
    max_width: int,
    font_path: str,
    font_weight: str,
    size: int,
    line_height: int,
) -> int:
    """Render formatted spans onto *canvas*/*draw* starting at (*x*, *y*).

    Handles word-wrapping within *max_width*.
    Returns the y-coordinate of the line after the last rendered line.
    """
    cx = x

    for span in spans:
        weight = "bold" if span.bold else font_weight
        f = get_font(size, font_path, weight)

        try:
            ascent, _ = f.getmetrics()
        except Exception:
            ascent = size

        remaining = span.text
        while remaining:
            # Greedy fit: find the longest prefix that fits on the current line
            for end in range(len(remaining), 0, -1):
                chunk = remaining[:end]
                if cx + text_width(chunk, f) <= x + max_width or cx == x:
                    break
            else:
                chunk = remaining[:1]

            tw = text_width(chunk, f)

            if span.italic:
                _draw_italic_chunk(canvas, chunk, cx, y, f, span.color, size)
            else:
                draw.text((cx, y), chunk, font=f, fill=span.color)

            if span.underline:
                # 1 px below the ascender bottom (just under the baseline)
                uy = y + ascent + 1
                draw.line([(cx, uy), (cx + tw, uy)], fill=span.color, width=1)
            if span.strikethrough:
                # approximately 2/3 of the way up the ascent (mid-x-height)
                sy = y + ascent * 2 // 3
                draw.line([(cx, sy), (cx + tw, sy)], fill=span.color, width=1)

            cx += tw
            remaining = remaining[len(chunk):]

            # Wrap if the remaining text no longer fits on this line
            if remaining and cx + text_width(remaining[0], f) > x + max_width:
                y += line_height
                cx = x

    return y + line_height


# ---------------------------------------------------------------------------
# Icon helpers
# ---------------------------------------------------------------------------


def _decode_favicon(favicon: Optional[str]) -> Optional[Image.Image]:
    """Decode a ``data:image/png;base64,…`` favicon string into a PIL image."""
    if not favicon:
        return None
    prefix = "data:image/png;base64,"
    if not favicon.startswith(prefix):
        return None
    try:
        raw = base64.b64decode(favicon[len(prefix):])
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception as exc:
        logger.debug(f"[mc_card] favicon decode error: {exc}")
        return None


def _draw_default_icon(img: Image.Image, x: int, y: int, size: int) -> None:
    """Draw a pixelated grass-block placeholder icon at (*x*, *y*)."""
    draw = ImageDraw.Draw(img)
    top_h = size // 4
    # Grass top
    draw.rectangle([(x, y), (x + size - 1, y + top_h - 1)], fill=(89, 153, 40))
    # Grass highlight stripe
    draw.rectangle([(x, y + top_h - 2), (x + size - 1, y + top_h - 1)],
                   fill=(106, 179, 52))
    # Dirt body
    draw.rectangle([(x, y + top_h), (x + size - 1, y + size - 1)],
                   fill=(134, 96, 67))
    # Dirt pixel noise (deterministic 4-tone)
    _DTONES = [(134, 96, 67), (122, 87, 59), (111, 77, 51), (144, 103, 73)]
    _DPAT   = [0, 2, 1, 3, 2, 0, 3, 1]  # 8-entry repeating noise
    dirt_h = size - top_h
    step = max(size // 8, 2)
    for dy in range(0, dirt_h, step):
        for dx in range(0, size, step):
            tone = _DPAT[((dy // step) * 4 + (dx // step)) % len(_DPAT)]
            draw.rectangle(
                [(x + dx, y + top_h + dy),
                 (x + dx + step - 1, y + top_h + dy + step - 1)],
                fill=_DTONES[tone],
            )


def _draw_server_icon(
    canvas: Image.Image,
    icon_img: Optional[Image.Image],
    x: int,
    y: int,
    size: int,
) -> None:
    """Paste *icon_img* (or a placeholder) at (*x*, *y*)."""
    if icon_img is None:
        _draw_default_icon(canvas, x, y, size)
        return
    try:
        resized = icon_img.resize((size, size), _RESAMPLE_LANCZOS)
        canvas.paste(resized, (x, y), resized)
    except Exception as exc:
        logger.debug(f"[mc_card] icon paste error: {exc}")
        _draw_default_icon(canvas, x, y, size)


# ---------------------------------------------------------------------------
# Ping-bar helper
# ---------------------------------------------------------------------------


def _ping_color(latency: float) -> tuple[int, int, int]:
    if latency < 150:  return C_PING_GOOD
    if latency < 300:  return C_PING_OK
    if latency < 600:  return C_PING_SLOW
    return C_PING_BAD


def _filled_bars(latency: float) -> int:
    if latency < 150:   return 5
    if latency < 300:   return 4
    if latency < 600:   return 3
    if latency < 1000:  return 2
    return 1


def _draw_ping_bars(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    latency: float,
) -> int:
    """Draw 5 Minecraft-style ping bars anchored at bottom *cy*.

    ``cy`` should equal ``y + FS_FOOTER`` so the tallest bar (height
    ``_PING_BAR_MAX_H == FS_FOOTER``) starts exactly at ``y`` and never
    overflows into the row above.

    Returns the total pixel width consumed.
    """
    filled = _filled_bars(latency)
    color  = _ping_color(latency)
    total_w = _PING_N * _PING_BAR_W + (_PING_N - 1) * _PING_BAR_GAP

    bx = cx
    for i in range(_PING_N):
        bar_h = int(_PING_BAR_MAX_H * (i + 1) / _PING_N)
        fill = color if i < filled else C_PING_EMPTY
        draw.rectangle(
            [(bx, cy - bar_h), (bx + _PING_BAR_W - 1, cy)],
            fill=fill,
        )
        bx += _PING_BAR_W + _PING_BAR_GAP

    return total_w


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_mc_card(
    *,
    display_name: str,
    motd_parsed: Optional[list] = None,
    motd_plain: str = "",
    version_str: str = "",
    players_online: int = 0,
    players_max: int = 0,
    latency: float = 0.0,
    favicon: Optional[str] = None,
    is_bedrock: bool = False,
    extra_info: str = "",
    player_sample: Optional[list[str]] = None,
    font_path: str = "",
    font_weight: str = "medium",
) -> bytes:
    """Build a Minecraft server-status card and return PNG bytes.

    Parameters
    ----------
    display_name:
        Server address or saved alias shown at the top of the card.
    motd_parsed:
        ``Motd.parsed`` list from *mcstatus*.  Full colour/style rendering.
        Falls back to *motd_plain* when ``None``.
    motd_plain:
        Plain-text MOTD (§ codes stripped).  Used when *motd_parsed* is ``None``.
    version_str:
        Server version label (e.g. ``"Paper 1.20.4"``).
    players_online / players_max:
        Player count shown in the top-right and footer.
    latency:
        Round-trip latency in milliseconds.
    favicon:
        ``data:image/png;base64,…`` from the Java ping response, or ``None``.
    is_bedrock:
        ``True`` for Bedrock Edition servers.
    extra_info:
        Optional extra text line (e.g. Bedrock map/mode).
    player_sample:
        Up to 12 player display names from ``status.players.sample``.
        Shown below the MOTD when not ``None`` and non-empty.
    font_path / font_weight:
        See ``image_common.get_font``.
    """
    # ── MOTD spans ────────────────────────────────────────────────────────────
    if motd_parsed is not None:
        all_spans = _motd_to_spans(
            motd_parsed,
            prefer_legacy_java_styles=not is_bedrock,
        )
    else:
        all_spans = [_Span(motd_plain or "")]
    motd_lines = _split_spans_by_newline(all_spans)[:3]

    # ── Player sample ─────────────────────────────────────────────────────────
    sample_names: list[str] = (player_sample or [])[:10]
    sample_cols = 2
    sample_rows = (len(sample_names) + sample_cols - 1) // sample_cols if sample_names else 0

    # ── Height measurement ───────────────────────────────────────────────────
    name_row_h  = FS_NAME   + 6
    ver_row_h   = (LH_VER   + 2) if version_str else 0
    extra_row_h = (LH_VER   + 2) if extra_info  else 0
    motd_h      = len(motd_lines) * LH_MOTD if motd_lines else 0
    sample_h    = (sample_rows * LH_SAMPLE + 4) if sample_rows else 0
    footer_h    = LH_FOOTER + 4

    text_block_h = (
        name_row_h
        + ver_row_h
        + extra_row_h
        + motd_h
        + sample_h
        + footer_h
    )
    content_h = max(ICON_SIZE + SIDE_PAD, text_block_h + SIDE_PAD)
    card_h = SIDE_PAD + content_h + SIDE_PAD

    # ── Canvas – dirt background + MC panel ──────────────────────────────────
    card = Image.new("RGB", (CARD_W, card_h))
    _draw_dirt_bg(card)

    draw = ImageDraw.Draw(card)
    _draw_mc_panel(draw, SIDE_PAD // 2, SIDE_PAD // 2, CARD_W - SIDE_PAD // 2 - 1, card_h - SIDE_PAD // 2 - 1)

    # ── Server icon ───────────────────────────────────────────────────────────
    icon_img = _decode_favicon(favicon)
    _draw_server_icon(card, icon_img, SIDE_PAD, SIDE_PAD, ICON_SIZE)

    # ── Prepare fonts ─────────────────────────────────────────────────────────
    f_name   = get_font(FS_NAME,   font_path, "bold")
    f_ver    = get_font(FS_VER,    font_path, font_weight)
    f_footer = get_font(FS_FOOTER, font_path, font_weight)
    f_sample = get_font(FS_SAMPLE, font_path, font_weight)

    text_area_w = CARD_W - TEXT_X - SIDE_PAD

    y = SIDE_PAD

    # ── Name row ──────────────────────────────────────────────────────────────
    player_str = f"{players_online} / {players_max}"
    player_tw  = text_width(player_str, f_footer)
    player_x   = CARD_W - SIDE_PAD - player_tw

    name_max_w = player_x - TEXT_X - 8
    name_text  = display_name
    while name_text and text_width(name_text, f_name) > name_max_w:
        name_text = name_text[:-1]
    if len(name_text) < len(display_name):
        name_text = name_text[:-1] + "…"
    draw.text((TEXT_X, y), name_text, font=f_name, fill=C_NAME)

    # Edition badge (JE / BE), right-aligned on the name row
    badge_text = "BE" if is_bedrock else "JE"
    badge_col: tuple[int, int, int] = (85, 255, 85) if is_bedrock else (85, 85, 255)
    draw.text(
        (CARD_W - SIDE_PAD - text_width(badge_text, f_footer),
         y + (FS_NAME - FS_FOOTER) // 2),
        badge_text, font=f_footer, fill=badge_col,
    )

    y += name_row_h

    # ── Version ───────────────────────────────────────────────────────────────
    if version_str:
        draw.text((TEXT_X, y), version_str[:80], font=f_ver, fill=C_VER)
        y += ver_row_h

    # ── Extra info (Bedrock map/mode) ─────────────────────────────────────────
    if extra_info:
        draw.text((TEXT_X, y), extra_info[:80], font=f_ver, fill=C_VER)
        y += extra_row_h

    # ── MOTD lines ────────────────────────────────────────────────────────────
    for line_spans in motd_lines:
        if not line_spans:
            y += LH_MOTD
            continue
        y = _render_spans(
            card, draw, line_spans,
            x=TEXT_X, y=y,
            max_width=text_area_w,
            font_path=font_path,
            font_weight=font_weight,
            size=FS_MOTD,
            line_height=LH_MOTD,
        )

    # Ensure footer is below the icon
    y = max(y, SIDE_PAD + ICON_SIZE + 4)

    # ── Player sample ─────────────────────────────────────────────────────────
    if sample_names:
        y += 2  # small gap after MOTD
        col_w = text_area_w // sample_cols
        for row in range(sample_rows):
            for col in range(sample_cols):
                idx = row * sample_cols + col
                if idx >= len(sample_names):
                    break
                nx = TEXT_X + col * col_w
                name = sample_names[idx]
                # Truncate to fit the column
                while name and text_width(name, f_sample) > col_w - 4:
                    name = name[:-1]
                draw.text((nx, y), name, font=f_sample, fill=C_SAMPLE)
            y += LH_SAMPLE
        y += 2  # small gap after sample

    # ── Footer: ping label + bars + player count ──────────────────────────────
    ping_label = f"Ping: {latency:.0f} ms"
    draw.text((TEXT_X, y), ping_label, font=f_footer, fill=C_FOOTER)

    # cy = y + FS_FOOTER ensures tallest bar is exactly FS_FOOTER px tall and
    # starts at y — never overflows into the row above
    bars_x = TEXT_X + text_width(ping_label, f_footer) + 8
    _draw_ping_bars(draw, bars_x, y + FS_FOOTER, latency)

    # Player count, right-aligned at the same y
    draw.text((player_x, y), player_str, font=f_footer, fill=C_PLAYERS)

    # ── Encode ────────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    card.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
