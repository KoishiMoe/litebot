"""
card.py – Minecraft server status card builder.

Generates an authentic Minecraft-themed PNG image (800 px wide, ~16:9 ratio)
optimized for mobile chat previews (no cropping or squishing in QQ):

    ┌────────────────────────────────────────────────────────────────────────┐  ← dirt bg
    │ ┌───────┐  Server Name [Java版]                     ┌────────────────┐ │
    │ │ Fav   │  版本: Paper 1.20.4                       │    在线玩家    │ │
    │ │ icon  │  ▌▌▌▌▌ 42 ms                              │   12 / 100     │ │
    │ └───────┘                                           └────────────────┘ │
    │ ┌────────────────────────────────────────────────────────────────────┐ │
    │ │ §aHypixel Network §7[1.8-1.20]                                     │ │  ← MOTD Slot
    │ │ §b§nNEW§r §eSkyBlock Update §6§lOut Now! §c§oJoin Today            │ │
    │ └────────────────────────────────────────────────────────────────────┘ │
    │ ┌────────────────────────────────────────────────────────────────────┐ │
    │ │ 👥 玩家列表                                                         │ │  ← Player Chips
    │ │ [☺ Steve]  [☺ Alex]  [☺ Notch]  [☺ Dinnerbone]                     │ │
    │ └────────────────────────────────────────────────────────────────────┘ │
    └────────────────────────────────────────────────────────────────────────┘

CLI test (from repository root)::

    python tools/test_mc_card.py [options]
    python tools/test_mc_card.py --help
"""

from __future__ import annotations

import base64
import io
import math
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
    ImageFont,
    get_font,
    text_width,
)

# ---------------------------------------------------------------------------
# Layout constants (2x HiDPI & ~16:9 mobile aspect ratio)
# ---------------------------------------------------------------------------

CARD_W = 800
MIN_CARD_H = 460   # ~16:9 ratio guarantee (800 x 460 ≈ 1.74:1)
SIDE_PAD = 24
ICON_SIZE = 88

FS_NAME = 24
FS_VER = 15
FS_MOTD = 19
FS_LABEL = 14
FS_PLAYER_NAME = 14
FS_FOOTER = 14

LH_VER = FS_VER + 5
LH_MOTD = FS_MOTD + 9

# Ping-bar geometry (HiDPI scaled)
_PING_N = 5
_PING_BAR_W = 5
_PING_BAR_GAP = 3
_PING_BAR_MAX_H = 16

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
_DIM = 0.32
_DIRT_TONES: list[tuple[int, int, int]] = [
    (int(144 * _DIM), int(103 * _DIM), int(73 * _DIM)),
    (int(134 * _DIM), int(96  * _DIM), int(67 * _DIM)),
    (int(122 * _DIM), int(87  * _DIM), int(59 * _DIM)),
    (int(111 * _DIM), int(77  * _DIM), int(51 * _DIM)),
]

# --- Slot & Frame colors ----------------------------------------------------
C_SLOT_BG    = (22,  22,  32 )
C_SLOT_DARK  = (14,  14,  20 )   # Top/left shadow
C_SLOT_LIGHT = (70,  70,  95 )   # Bottom/right highlight
C_FRAME_HI   = (90,  90,  125)   # Outer frame top/left
C_FRAME_SH   = (16,  16,  24 )   # Outer frame bottom/right

# --- Ping colors -----------------------------------------------------------
C_PING_GOOD:  tuple[int, int, int] = (85,  255, 85 )   # < 150 ms
C_PING_OK:    tuple[int, int, int] = (255, 255, 85 )   # 150-300 ms
C_PING_SLOW:  tuple[int, int, int] = (255, 170, 0  )   # 300-600 ms
C_PING_BAD:   tuple[int, int, int] = (255, 85,  85 )   # > 600 ms
C_PING_EMPTY: tuple[int, int, int] = (35,  35,  48 )

# --- Minecraft § colour codes (Java & Bedrock) ----------------------------
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
    "h": (227, 212, 209),   # material_quartz (Bedrock)
    "i": (206, 202, 202),   # material_iron (Bedrock)
    "j": (68,  58,  59 ),   # material_netherite (Bedrock)
    "m": (151, 22,  7  ),   # material_redstone (Bedrock)
    "n": (180, 104, 77 ),   # material_copper (Bedrock)
    "p": (222, 177, 45 ),   # material_gold (Bedrock)
    "q": (17,  159, 54 ),   # material_emerald (Bedrock)
    "s": (44,  186, 168),   # material_diamond (Bedrock)
    "t": (33,  73,  123),   # material_lapis (Bedrock)
    "u": (154, 92,  198),   # material_amethyst (Bedrock)
    "v": (235, 114, 20 ),   # material_resin (Bedrock)
}

# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------

def _make_dirt_tile() -> Image.Image:
    """Build a chunky 32x32 dirt tile (each pixel 2x2 for crisp HiDPI)."""
    tile = Image.new("RGB", (32, 32))
    px = tile.load()
    for idx, tone in enumerate(_DIRT_GRID):
        row, col = divmod(idx, 16)
        c = _DIRT_TONES[tone]
        for dy in range(2):
            for dx in range(2):
                px[col * 2 + dx, row * 2 + dy] = c
    return tile


def _draw_dirt_bg(canvas: Image.Image) -> None:
    """Tile the dirt pattern across the entire canvas."""
    tile = _make_dirt_tile()
    tw, th = tile.size
    cw, ch = canvas.size
    for ty in range(0, ch, th):
        for tx in range(0, cw, tw):
            canvas.paste(tile, (tx, ty))


def _draw_slot_box(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    fill: tuple[int, int, int] = C_SLOT_BG,
    border_w: int = 2,
) -> None:
    """Draw an authentic Minecraft inset slot box with beveled edges."""
    draw.rectangle([(x0, y0), (x1, y1)], fill=fill)
    for b in range(border_w):
        # Top & Left dark shadow
        draw.line([(x0 + b, y0 + b), (x1 - b, y0 + b)], fill=C_SLOT_DARK)
        draw.line([(x0 + b, y0 + b), (x0 + b, y1 - b)], fill=C_SLOT_DARK)
        # Bottom & Right light highlight
        draw.line([(x0 + b, y1 - b), (x1 - b, y1 - b)], fill=C_SLOT_LIGHT)
        draw.line([(x1 - b, y0 + b), (x1 - b, y1 - b)], fill=C_SLOT_LIGHT)


def _draw_outer_frame(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    border_w: int = 2,
) -> None:
    """Draw the outer card frame bevel."""
    for b in range(border_w):
        draw.line([(x0 + b, y0 + b), (x1 - b, y0 + b)], fill=C_FRAME_HI)
        draw.line([(x0 + b, y0 + b), (x0 + b, y1 - b)], fill=C_FRAME_HI)
        draw.line([(x0 + b, y1 - b), (x1 - b, y1 - b)], fill=C_FRAME_SH)
        draw.line([(x1 - b, y0 + b), (x1 - b, y1 - b)], fill=C_FRAME_SH)


# ---------------------------------------------------------------------------
# Obfuscation & MOTD spans
# ---------------------------------------------------------------------------

_OBFUSCATED_CHARS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    "0123456789!@#$%&*()_+-=[]{}|;:,./<>?~`^"
    "▒▓░▌▐▀▄■□●◆★♦♠♣♥"
)


def _obfuscate(text: str) -> str:
    """Replace each character in *text* with a deterministic symbol."""
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
    """Convert a ``Motd.parsed`` list into a flat list of :class:`_Span` objects."""
    try:
        from mcstatus.motd.components import (  # type: ignore[attr-defined]
            BedrockFormatting,
            BedrockMinecraftColor,
            JavaFormatting,
            JavaMinecraftColor,
            WebColor,
        )
        Formatting = (JavaFormatting, BedrockFormatting)
        MinecraftColor = (JavaMinecraftColor, BedrockMinecraftColor)
    except ImportError:
        try:
            from mcstatus.motd.components import Formatting, MinecraftColor, WebColor  # type: ignore[no-redef]
        except ImportError:
            return [_Span(str(p), default_color) for p in parsed if str(p)]

    color = default_color
    bold = italic = underline = strikethrough = obfuscated = False
    spans: list[_Span] = []
    legacy_style_alias = {"l", "o", "n", "m", "k", "r"}

    for item in parsed:
        if isinstance(item, MinecraftColor):
            code = item.value
            if prefer_legacy_java_styles and code in legacy_style_alias and code not in MC_COLOR_RGB:
                if code == "r":
                    color = default_color
                    bold = italic = underline = strikethrough = obfuscated = False
                elif code == "l": bold = True
                elif code == "o": italic = True
                elif code == "n": underline = True
                elif code == "m": strikethrough = True
                elif code == "k": obfuscated = True
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
            elif v == "l": bold = True
            elif v == "o": italic = True
            elif v == "n": underline = True
            elif v == "m": strikethrough = True
            elif v == "k": obfuscated = True
        elif isinstance(item, str) and item:
            text = _obfuscate(item) if obfuscated else item
            spans.append(_Span(text, color, bold, italic, underline, strikethrough))

    return spans


def _split_spans_by_newline(spans: list[_Span]) -> list[list[_Span]]:
    """Split spans into logical MOTD lines, handling both real newlines and escaped \\n."""
    lines: list[list[_Span]] = [[]]
    for span in spans:
        # Normalize escaped newlines from raw server MOTD or CLI
        norm_text = span.text.replace("\r\n", "\n").replace("\\r\\n", "\n").replace("\\n", "\n")
        parts = norm_text.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                lines.append([])
            if part:
                lines[-1].append(
                    _Span(part, span.color, span.bold, span.italic,
                          span.underline, span.strikethrough)
                )
    return lines


def _draw_italic_chunk(
    canvas: Image.Image,
    text: str,
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    color: tuple[int, int, int],
    size: int,
) -> None:
    """Render *text* with an italic shear transform."""
    tw = text_width(text, font)
    if not tw:
        return

    pad = int(math.ceil(size * _ITALIC_SHEAR)) + 6
    patch_w = tw + pad * 2
    patch_h = size + 10

    patch = Image.new("RGBA", (patch_w, patch_h), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(patch)
    pdraw.text((pad, 0), text, font=font, fill=(*color, 255))

    try:
        ascent, _ = font.getmetrics()
    except Exception:
        ascent = size

    sheared = patch.transform(
        (patch_w, patch_h),
        _AFFINE,
        (1, _ITALIC_SHEAR, -_ITALIC_SHEAR * ascent, 0, 1, 0),
        _RESAMPLE_BILINEAR,
    )
    canvas.paste(sheared, (x - pad, y), sheared)


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
    """Render a line of styled spans, wrapping to new lines when exceeding *max_width*."""
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
                uy = y + ascent + 1
                draw.line([(cx, uy), (cx + tw, uy)], fill=span.color, width=1)
            if span.strikethrough:
                sy = y + ascent * 2 // 3
                draw.line([(cx, sy), (cx + tw, sy)], fill=span.color, width=1)

            cx += tw
            remaining = remaining[len(chunk):]

            if remaining and cx + text_width(remaining[0], f) > x + max_width:
                y += line_height
                cx = x

    return y + line_height


# ---------------------------------------------------------------------------
# Offline pixel avatar & icon helpers
# ---------------------------------------------------------------------------

def _draw_player_head(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    size: int,
    username: str,
) -> None:
    """Draw an authentic 8-bit Minecraft player head completely offline (0 network I/O).

    Uses a deterministic hash of *username* to select authentic skin, hair, and eye tones.
    """
    h = hash(username) & 0xFFFF_FFFF
    skin_tones = [
        (219, 172, 138), (188, 137, 101), (133, 91, 62),
        (240, 195, 160), (105, 75, 50), (170, 195, 160),
    ]
    hair_tones = [
        (45, 30, 20), (110, 60, 30), (180, 120, 50),
        (190, 80, 40), (35, 35, 35), (60, 80, 120),
    ]
    eye_tones = [
        (50, 70, 180), (40, 140, 70), (110, 70, 40),
        (140, 50, 160), (30, 160, 170),
    ]
    skin = skin_tones[h % len(skin_tones)]
    hair = hair_tones[(h >> 4) % len(hair_tones)]
    eye = eye_tones[(h >> 8) % len(eye_tones)]

    px_size = max(size // 8, 2)
    head_w = px_size * 8
    hx = x + (size - head_w) // 2
    hy = y + (size - head_w) // 2

    # Face base
    draw.rectangle([(hx, hy), (hx + head_w - 1, hy + head_w - 1)], fill=skin)
    # Hair top rows
    draw.rectangle([(hx, hy), (hx + head_w - 1, hy + px_size * 2 - 1)], fill=hair)
    # Hair sides
    draw.rectangle([(hx, hy + px_size * 2), (hx + px_size - 1, hy + px_size * 4 - 1)], fill=hair)
    draw.rectangle([(hx + head_w - px_size, hy + px_size * 2), (hx + head_w - 1, hy + px_size * 4 - 1)], fill=hair)
    # Fringe / bangs
    if (h >> 12) % 2 == 0:
        draw.rectangle([(hx + px_size * 2, hy + px_size * 2), (hx + px_size * 3 - 1, hy + px_size * 3 - 1)], fill=hair)

    # Eyes (row 4)
    draw.rectangle([(hx + px_size * 1, hy + px_size * 4), (hx + px_size * 2 - 1, hy + px_size * 5 - 1)], fill=(255, 255, 255))
    draw.rectangle([(hx + px_size * 2, hy + px_size * 4), (hx + px_size * 3 - 1, hy + px_size * 5 - 1)], fill=eye)
    draw.rectangle([(hx + px_size * 6, hy + px_size * 4), (hx + px_size * 7 - 1, hy + px_size * 5 - 1)], fill=(255, 255, 255))
    draw.rectangle([(hx + px_size * 5, hy + px_size * 4), (hx + px_size * 6 - 1, hy + px_size * 5 - 1)], fill=eye)

    # Mouth / goatee
    if (h >> 14) % 3 == 0:
        draw.rectangle([(hx + px_size * 3, hy + px_size * 6), (hx + px_size * 5 - 1, hy + px_size * 7 - 1)],
                       fill=(max(0, hair[0] - 20), max(0, hair[1] - 20), max(0, hair[2] - 20)))


def _decode_favicon(favicon: Optional[str]) -> Optional[Image.Image]:
    """Decode a ``data:image/png;base64,…`` favicon string into a PIL image."""
    prefix = "data:image/png;base64,"
    if not favicon or not favicon.startswith(prefix):
        return None
    try:
        raw = base64.b64decode(favicon[len(prefix):])
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception as exc:
        logger.debug(f"[mc_card] favicon decode error: {exc}")
        return None


def _draw_default_icon(img: Image.Image, x: int, y: int, size: int) -> None:
    """Draw an authentic 16x16 pixel Minecraft grass block icon at (*x*, *y*)."""
    px = size / 16.0
    draw = ImageDraw.Draw(img)

    dirt_pattern = [
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
        [2,1,0,2,0,1,2,0,1,2,0,1,2,0,1,2],
        [1,2,2,0,1,0,1,2,0,0,2,1,0,2,1,0],
        [0,1,0,2,0,2,0,1,2,1,0,2,1,0,2,1],
        [2,0,1,0,1,1,2,0,1,2,1,0,2,1,0,2],
        [1,2,0,2,0,2,0,1,0,1,2,2,0,1,2,0],
        [0,1,2,1,2,0,1,2,1,0,1,0,2,0,1,1],
        [2,0,1,2,0,1,2,0,2,1,0,2,1,2,0,2],
        [1,2,0,1,2,0,1,2,0,2,1,0,2,0,1,0],
        [0,1,2,0,1,2,0,1,2,0,2,1,0,2,0,1],
        [2,0,1,2,0,1,2,0,1,2,0,1,2,0,2,0],
        [1,2,0,1,2,0,1,2,0,1,2,0,1,2,0,2],
        [0,1,2,0,1,2,0,1,2,0,1,2,0,1,2,0],
        [2,0,1,2,0,1,2,0,1,2,0,1,2,0,1,2],
        [1,2,0,1,2,0,1,2,0,1,2,0,1,2,0,1],
    ]
    dirt_colors = [
        (134, 96, 67),   # base
        (144, 103, 73),  # light
        (122, 87, 59),   # dark
        (105, 74, 48),   # darker
    ]

    for r in range(16):
        for c in range(16):
            c_idx = dirt_pattern[r][c]
            col = dirt_colors[c_idx]
            x0 = int(x + c * px)
            y0 = int(y + r * px)
            x1 = int(x + (c + 1) * px)
            y1 = int(y + (r + 1) * px)
            draw.rectangle([(x0, y0), (x1, y1)], fill=col)

    fringe_lens = [3, 4, 3, 2, 4, 3, 5, 3, 4, 2, 3, 4, 3, 5, 4, 3]
    grass_top = (110, 185, 50)
    grass_dark = (85, 145, 35)
    grass_highlight = (130, 205, 65)

    for c in range(16):
        flen = fringe_lens[c]
        x0 = int(x + c * px)
        x1 = int(x + (c + 1) * px)
        # Top highlight
        draw.rectangle([(x0, int(y)), (x1, int(y + px))], fill=grass_highlight)
        # Mid grass
        draw.rectangle([(x0, int(y + px)), (x1, int(y + (flen - 1) * px))], fill=grass_top)
        # Bottom fringe dark edge
        draw.rectangle([(x0, int(y + (flen - 1) * px)), (x1, int(y + flen * px))], fill=grass_dark)


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


def _ping_color(latency: float) -> tuple[int, int, int]:
    if latency < 150: return C_PING_GOOD
    if latency < 300: return C_PING_OK
    if latency < 600: return C_PING_SLOW
    return C_PING_BAD


def _filled_bars(latency: float) -> int:
    if latency < 150: return 5
    if latency < 300: return 4
    if latency < 600: return 3
    if latency < 1000: return 2
    return 1


def _draw_ping_bars(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    latency: float,
) -> int:
    """Draw 5 Minecraft-style ping bars anchored at bottom *cy*."""
    filled = _filled_bars(latency)
    color  = _ping_color(latency)
    total_w = _PING_N * _PING_BAR_W + (_PING_N - 1) * _PING_BAR_GAP

    bx = cx
    for i in range(_PING_N):
        bar_h = int(_PING_BAR_MAX_H * (i + 1) / _PING_N)
        fill = color if i < filled else C_PING_EMPTY
        draw.rectangle([(bx, cy - bar_h), (bx + _PING_BAR_W - 1, cy)], fill=fill)
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
    """Build a Minecraft server-status card (800 px wide, ~16:9 ratio) and return PNG bytes."""
    # ── 1. Parse MOTD ─────────────────────────────────────────────────────────
    if motd_parsed is not None:
        all_spans = _motd_to_spans(
            motd_parsed,
            prefer_legacy_java_styles=not is_bedrock,
        )
    else:
        all_spans = [_Span(motd_plain or "")]
    motd_lines = _split_spans_by_newline(all_spans)[:4]

    # ── 2. Player samples ─────────────────────────────────────────────────────
    sample_names = (player_sample or [])[:12]

    # ── 3. Layout measurement ─────────────────────────────────────────────────
    f_name   = get_font(FS_NAME,   font_path, "bold")
    f_ver    = get_font(FS_VER,    font_path, font_weight)
    f_label  = get_font(FS_LABEL,  font_path, font_weight)
    f_pname  = get_font(FS_PLAYER_NAME, font_path, font_weight)
    f_footer = get_font(FS_FOOTER, font_path, font_weight)

    header_h = ICON_SIZE
    motd_inner_pad = 16
    motd_content_h = max(len(motd_lines) * LH_MOTD, LH_MOTD)
    motd_slot_h = motd_inner_pad * 2 + motd_content_h

    chip_h = 30
    chip_gap = 8
    chip_cols = 3
    if sample_names:
        chip_rows = (len(sample_names) + chip_cols - 1) // chip_cols
        chips_total_h = chip_rows * chip_h + (chip_rows - 1) * chip_gap
        players_content_h = 36 + chips_total_h + 16
        if players_online > len(sample_names):
            players_content_h += FS_LABEL + 14
    else:
        chips_total_h = 0
        players_content_h = 36 + 46

    footer_row_h = (FS_FOOTER + 10) if extra_info else 0
    gap = 16

    base_content_h = (
        SIDE_PAD
        + header_h
        + gap
        + motd_slot_h
        + gap
        + players_content_h
        + (gap + footer_row_h if footer_row_h else 0)
        + SIDE_PAD
    )

    card_h = max(MIN_CARD_H, base_content_h)

    # Balance layout if card_h expands beyond base_content_h
    extra_slack = card_h - base_content_h
    if extra_slack > 0:
        # Give half the slack to MOTD breathing space, half to player slot
        motd_extra = extra_slack // 2
        motd_slot_h += motd_extra
        players_content_h += (extra_slack - motd_extra)

    # ── 4. Canvas creation ────────────────────────────────────────────────────
    card = Image.new("RGB", (CARD_W, card_h))
    _draw_dirt_bg(card)

    draw = ImageDraw.Draw(card)
    _draw_outer_frame(draw, 8, 8, CARD_W - 9, card_h - 9, border_w=2)

    # ── 5. Header Section ─────────────────────────────────────────────────────
    hy = SIDE_PAD

    # Favicon slot
    _draw_slot_box(draw, SIDE_PAD, hy, SIDE_PAD + ICON_SIZE + 4, hy + ICON_SIZE + 4, fill=(18, 18, 26), border_w=2)
    icon_img = _decode_favicon(favicon)
    _draw_server_icon(card, icon_img, SIDE_PAD + 2, hy + 2, ICON_SIZE)

    # Server details beside icon
    tx = SIDE_PAD + ICON_SIZE + 18
    title_text = display_name
    badge_text = "[基岩版]" if is_bedrock else "[Java版]"
    badge_col = (85, 255, 220) if is_bedrock else (100, 180, 255)
    f_badge = get_font(FS_VER, font_path, "bold")

    draw.text((tx, hy + 2), title_text, font=f_name, fill=(255, 255, 255))
    name_w = text_width(title_text, f_name)
    draw.text((tx + name_w + 12, hy + 6), badge_text, font=f_badge, fill=badge_col)

    # Version row
    ver_display = f"版本: {version_str}" if version_str else "版本: 未知"
    draw.text((tx, hy + 34), ver_display, font=f_ver, fill=(180, 180, 190))

    # Ping row
    ping_y = hy + 60
    ping_label = f"{latency:.0f} ms"
    ping_w = _draw_ping_bars(draw, tx, ping_y + _PING_BAR_MAX_H, latency)
    draw.text((tx + ping_w + 8, ping_y + 1), ping_label, font=f_footer, fill=_ping_color(latency))

    # Top-right online player count box
    count_box_w = 160
    count_box_h = ICON_SIZE
    cb_x1 = CARD_W - SIDE_PAD
    cb_x0 = cb_x1 - count_box_w
    _draw_slot_box(draw, cb_x0, hy, cb_x1, hy + count_box_h, fill=(26, 26, 38), border_w=2)

    cb_title = "在线玩家"
    cb_title_w = text_width(cb_title, f_label)
    draw.text((cb_x0 + (count_box_w - cb_title_w) // 2, hy + 14), cb_title, font=f_label, fill=(170, 170, 190))

    count_str = f"{players_online} / {players_max}"
    f_count = get_font(22, font_path, "bold")
    count_str_w = text_width(count_str, f_count)
    draw.text((cb_x0 + (count_box_w - count_str_w) // 2, hy + 42), count_str, font=f_count, fill=(255, 215, 0))

    # ── 6. MOTD Box ───────────────────────────────────────────────────────────
    motd_y = hy + header_h + gap
    motd_x0 = SIDE_PAD
    motd_x1 = CARD_W - SIDE_PAD

    _draw_slot_box(draw, motd_x0, motd_y, motd_x1, motd_y + motd_slot_h, fill=(20, 20, 28), border_w=2)

    # Center lines vertically in motd box if extra space
    actual_motd_text_h = len(motd_lines) * LH_MOTD
    cur_y = motd_y + (motd_slot_h - actual_motd_text_h) // 2
    motd_inner_w = (motd_x1 - motd_x0) - motd_inner_pad * 2

    for line_spans in motd_lines:
        if not line_spans:
            cur_y += LH_MOTD
            continue
        cur_y = _render_spans(
            card, draw, line_spans,
            x=motd_x0 + motd_inner_pad, y=cur_y,
            max_width=motd_inner_w,
            font_path=font_path, font_weight=font_weight,
            size=FS_MOTD, line_height=LH_MOTD,
        )

    # ── 7. Players Section ────────────────────────────────────────────────────
    py = motd_y + motd_slot_h + gap
    player_box_x0 = SIDE_PAD
    player_box_x1 = CARD_W - SIDE_PAD
    player_box_y1 = py + players_content_h

    _draw_slot_box(draw, player_box_x0, py, player_box_x1, player_box_y1, fill=(24, 24, 34), border_w=2)

    # Header label in player box
    p_header = "玩家列表"
    draw.text((player_box_x0 + 16, py + 12), p_header, font=f_label, fill=(180, 190, 210))

    chip_y0 = py + 36
    chip_area_w = (player_box_x1 - player_box_x0) - 32
    col_w = (chip_area_w - (chip_cols - 1) * chip_gap) // chip_cols

    if sample_names:
        for idx, name in enumerate(sample_names):
            row = idx // chip_cols
            col = idx % chip_cols
            cx0 = player_box_x0 + 16 + col * (col_w + chip_gap)
            cy0 = chip_y0 + row * (chip_h + chip_gap)
            cx1 = cx0 + col_w
            cy1 = cy0 + chip_h

            # Mini slot chip
            _draw_slot_box(draw, cx0, cy0, cx1, cy1, fill=(34, 34, 48), border_w=1)

            # Offline deterministic 8-bit player head
            p_head_size = 20
            _draw_player_head(draw, cx0 + 5, cy0 + (chip_h - p_head_size) // 2, p_head_size, name)

            # Player name text
            pname = name
            name_x = cx0 + 5 + p_head_size + 8
            avail_w = col_w - (p_head_size + 18)
            while pname and text_width(pname, f_pname) > avail_w:
                pname = pname[:-1]
            draw.text((name_x, cy0 + 6), pname, font=f_pname, fill=(235, 235, 245))

        if players_online > len(sample_names):
            extra_count = players_online - len(sample_names)
            note = f"+ 以及另外 {extra_count} 名在线玩家..."
            note_y = chip_y0 + chips_total_h + 10
            draw.text((player_box_x0 + 16, note_y), note, font=f_label, fill=(140, 140, 160))
    else:
        # Centered stylish empty-state pill
        empty_text = "当前服务器暂无玩家在线" if players_online == 0 else "在线玩家列表已由服主隐藏"
        pill_w = text_width(empty_text, f_label) + 32
        pill_h = 30
        px0 = player_box_x0 + ((player_box_x1 - player_box_x0) - pill_w) // 2
        py0 = chip_y0 + 4
        _draw_slot_box(draw, px0, py0, px0 + pill_w, py0 + pill_h, fill=(30, 30, 42), border_w=1)
        draw.text((px0 + 16, py0 + 6), empty_text, font=f_label, fill=(150, 150, 170))

    # ── 8. Extra Info Footer ──────────────────────────────────────────────────
    if extra_info:
        fy0 = player_box_y1 + gap // 2
        fy1 = fy0 + footer_row_h
        _draw_slot_box(draw, SIDE_PAD, fy0, CARD_W - SIDE_PAD, fy1, fill=(20, 20, 28), border_w=1)
        draw.text((SIDE_PAD + 14, fy0 + 5), extra_info, font=f_footer, fill=(160, 170, 185))

    # ── 9. Encode ─────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    card.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
