"""
emoji_render.py – Bundled Twemoji PNG rendering for Pillow card generation.

Twemoji graphics (v17.0.2, Unicode 17) are stored in ``builtin/assets/twemoji/``
and licensed under CC-BY 4.0 (see ``LICENSE-GRAPHICS`` in that directory).

Public API
----------
tokenize_with_emoji(text)
    Split a string into (token, is_emoji) pairs.
text_width_with_emoji(text, font)
    Measure pixel width of text that may contain emoji.
draw_text_with_emoji(canvas, draw, xy, text, font, fill, anchor)
    Draw text with Twemoji PNGs composited in place of emoji sequences.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Asset discovery
# ---------------------------------------------------------------------------

_TWEMOJI_DIR = Path(__file__).resolve().parent.parent / "assets" / "twemoji"

# Maximum codepoints to probe in one greedy emoji sequence match.
# Longest Unicode 15 ZWJ sequences are ~10 codepoints (see filename analysis).
_MAX_SEQ_LEN = 10

# Lazy-built: unicode-sequence-string → PNG path
_emoji_index: Optional[dict[str, Path]] = None

# (png_path, size) → RGBA PIL image
_emoji_img_cache: dict[tuple[Path, int], Image.Image] = {}


def _build_index() -> dict[str, Path]:
    """Scan ``_TWEMOJI_DIR`` and build a mapping of Unicode sequences → PNG paths.

    Each PNG filename encodes the emoji as lowercase hex codepoints joined by
    ``-``, e.g. ``1f600.png`` for 😀 and ``1f468-200d-1f469.png`` for 👨‍👩.

    For each PNG the following entries are registered:

    * The literal sequence encoded in the filename.
    * The same sequence with U+FE0F (Variation Selector-16) stripped, so that
      emoji typed without VS-16 also match.
    * For single-codepoint sequences that don't already contain FE0F: the
      sequence *with* FE0F appended (e.g. ``☀️`` → ``2600.png``).  Many
      Misc-Symbol and Dingbat code points (U+2600–U+27BF, etc.) appear in
      text with VS-16 even though Twemoji only ships a bare-codepoint PNG.
      Registering the ``+FE0F`` alias consumes the variation selector in the
      tokenizer so it never reaches the text renderer as a stray glyph.
    """
    index: dict[str, Path] = {}
    if not _TWEMOJI_DIR.exists():
        return index
    for png_file in _TWEMOJI_DIR.glob("*.png"):
        stem = png_file.stem  # e.g. "1f600" or "1f468-200d-1f469"
        try:
            seq = "".join(chr(int(h, 16)) for h in stem.split("-"))
        except (ValueError, OverflowError):
            continue
        index[seq] = png_file
        # Strip VS-16 so bare-form and VS-16-form both resolve.
        seq_no_vs = seq.replace("\ufe0f", "")
        if seq_no_vs and seq_no_vs != seq:
            index.setdefault(seq_no_vs, png_file)
        # For single-codepoint PNGs without FE0F, also register the +FE0F
        # variant.  Many symbols (☀, ✈, ⭐, …) appear as <char>+FE0F in text
        # but Twemoji only provides the bare-codepoint PNG.
        if len(seq) == 1 and "\ufe0f" not in seq:
            index.setdefault(seq + "\ufe0f", png_file)
        # Keycap sequences (e.g. 32-20e3.png → "2⃣") are stored by Twemoji
        # without U+FE0F, but in real-world text they always appear as
        # <base>+FE0F+20E3 (e.g. "2️⃣" = U+0032 U+FE0F U+20E3).
        # Register the +FE0F variant so the tokenizer matches the full sequence
        # instead of leaving the digit as plain text and the modifiers as strays.
        if seq.endswith("\u20e3") and "\ufe0f" not in seq and len(seq) >= 2:
            seq_keycap_vs = seq[:-1] + "\ufe0f\u20e3"
            index.setdefault(seq_keycap_vs, png_file)
    return index


def _get_index() -> dict[str, Path]:
    global _emoji_index
    if _emoji_index is None:
        _emoji_index = _build_index()
    return _emoji_index


def _load_emoji_png(png_path: Path, size: int) -> Optional[Image.Image]:
    """Load, resize (to *size*×*size*), and cache a Twemoji PNG."""
    cache_key = (png_path, size)
    cached = _emoji_img_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        img = (
            Image.open(png_path)
            .convert("RGBA")
            .resize((size, size), Image.LANCZOS)
        )
        _emoji_img_cache[cache_key] = img
        return img
    except Exception:
        return None


def _match_emoji_at(text: str, pos: int) -> Optional[tuple[str, int]]:
    """Greedy-longest match for an emoji sequence starting at *pos*.

    Returns ``(sequence, end_pos)`` on success, or ``None`` when no emoji
    starts at *pos*.
    """
    index = _get_index()
    end = min(pos + _MAX_SEQ_LEN, len(text))
    while end > pos:
        seq = text[pos:end]
        if seq in index:
            return seq, end
        end -= 1
    return None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


# Locale-dependent sequence filters.  Resolved once at import time from the
# BOT_LANGUAGE env var (same source as i18n._language — intentionally static
# for the process lifetime; restart the bot to pick up language changes).
_FILTERED_SEQS: frozenset[str] = (
    frozenset({"\U0001f1f9\U0001f1fc"})
    if os.getenv("BOT_LANGUAGE", "zh").lower().strip() == "zh"
    else frozenset()
)


# Invisible emoji modifiers that should be silently dropped if they appear
# outside of a successfully matched emoji sequence.  These characters are only
# meaningful as part of a multi-codepoint sequence; a lone occurrence is an
# artefact of incomplete sequence matching and must not reach the text renderer
# (some fonts render them as small boxes or circles).
_ORPHANED_MODIFIERS = frozenset([
    "\ufe0f",  # U+FE0F  Variation Selector-16 (emoji presentation)
    "\ufe0e",  # U+FE0E  Variation Selector-15 (text presentation)
    "\u200d",  # U+200D  Zero Width Joiner
    "\u20e3",  # U+20E3  Combining Enclosing Keycap
])


def tokenize_with_emoji(text: str) -> list[tuple[str, bool]]:
    """Split *text* into ``(token, is_emoji)`` pairs.

    Contiguous non-emoji characters are grouped into a single text token.
    Each emoji sequence (single or multi-codepoint ZWJ / flag / keycap) is
    a separate token with ``is_emoji=True``.

    Orphaned emoji modifiers (U+FE0F, U+200D, etc.) that were not consumed as
    part of a longer sequence are silently dropped so that they do not reach
    the text renderer and appear as stray glyphs.
    """
    tokens: list[tuple[str, bool]] = []
    i = 0
    plain_start = 0
    while i < len(text):
        match = _match_emoji_at(text, i)
        if match is not None:
            seq, end = match
            if i > plain_start:
                tokens.append((text[plain_start:i], False))
            tokens.append((seq, True))
            plain_start = end
            i = end
        elif text[i] in _ORPHANED_MODIFIERS:
            # Flush any accumulated plain text before the orphaned modifier,
            # then skip the modifier without including it in any token.
            if i > plain_start:
                tokens.append((text[plain_start:i], False))
            plain_start = i + 1
            i += 1
        else:
            i += 1
    if plain_start < len(text):
        tokens.append((text[plain_start:], False))
    return tokens


def _emoji_size_for_font(font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    """Return an appropriate emoji square size in pixels for *font*."""
    try:
        return int(font.size)  # FreeTypeFont exposes .size
    except AttributeError:
        pass
    try:
        bbox = font.getbbox("Ay")
        return max(1, bbox[3] - bbox[1])
    except Exception:
        return 16


def _emoji_y_offset_for_font(
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    emoji_size: int,
) -> float:
    """Return vertical offset from text origin for emoji placement.

    The text origin is treated as ``anchor='la'`` (left, ascender).  We align the
    emoji box to the visual center of a representative glyph box and then nudge it
    slightly upward to avoid appearing lower than adjacent text.
    """
    try:
        top, bottom = font.getbbox("Ay")[1], font.getbbox("Ay")[3]
    except Exception:
        top, bottom = 0, emoji_size
    centered = (top + bottom - emoji_size) / 2
    # Small optical correction: bitmap emoji usually look ~1 px lower at this size.
    return centered - 1.0


def text_width_with_emoji(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> int:
    """Return the pixel width of *text*, treating each emoji as *emoji_size* wide.

    Emoji with no bundled PNG are measured by the primary *font* (they will
    render as replacement boxes, but layout will still be consistent).
    """
    emoji_size = _emoji_size_for_font(font)
    total = 0
    for token, is_emoji in tokenize_with_emoji(text):
        if is_emoji:
            if token in _FILTERED_SEQS:
                continue
            # Each emoji occupies a square of side emoji_size.
            total += emoji_size
        else:
            try:
                total += int(font.getlength(token))
            except AttributeError:
                total += int(font.getbbox(token)[2])
    return total


def draw_text_with_emoji(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: Any,
    anchor: Optional[str] = None,
) -> None:
    """Draw *text* with Twemoji PNGs composited in place of emoji sequences.

    Parameters
    ----------
    canvas:
        Destination PIL image.  Must be RGBA or have an alpha channel so that
        emoji alpha compositing works correctly.
    draw:
        ``ImageDraw.Draw`` instance associated with *canvas*.
    xy:
        ``(x, y)`` drawing origin.
    text:
        The string to render.
    font:
        Primary font used for non-emoji characters.
    fill:
        Text colour for non-emoji characters.
    anchor:
        Supported values:

        * ``None`` / ``"la"`` – left, ascender (Pillow default).
        * ``"lm"``            – left, middle; the y origin is shifted so that
                                the vertical midpoint of the text aligns with *y*.

        Other values fall back to left-ascender.
    """
    tokens = tokenize_with_emoji(text)

    # Fast path: no emoji present – let Pillow render natively with full anchor support.
    # Reconstruct from tokens rather than using the raw text so that any orphaned
    # emoji modifiers (U+FE0F, U+20E3, etc.) stripped during tokenization never
    # reach the font renderer and appear as replacement boxes.
    if not any(is_emoji for _, is_emoji in tokens):
        clean_text = "".join(t for t, _ in tokens)
        draw.text(xy, clean_text, font=font, fill=fill, anchor=anchor)
        return

    emoji_size = _emoji_size_for_font(font)
    x, y = float(xy[0]), float(xy[1])

    emoji_y_offset = _emoji_y_offset_for_font(font, emoji_size)

    # Convert "lm" (left, middle) to effective "la" (left, ascender) baseline.
    if anchor == "lm":
        try:
            # "Ay" spans from cap-ascender to descender – the broadest vertical extent.
            bbox = font.getbbox("Ay")
            y = y - (bbox[3] + bbox[1]) / 2
        except Exception:
            pass

    for token, is_emoji in tokens:
        if is_emoji:
            if token in _FILTERED_SEQS:
                continue
            index = _get_index()
            png_path = index.get(token)
            if png_path is not None:
                emoji_img = _load_emoji_png(png_path, emoji_size)
                if emoji_img is not None:
                    canvas.paste(
                        emoji_img,
                        (int(x), int(y + emoji_y_offset)),
                        emoji_img,
                    )
            x += emoji_size
        else:
            if token:
                draw.text((x, y), token, font=font, fill=fill)
                try:
                    x += float(font.getlength(token))
                except AttributeError:
                    x += float(font.getbbox(token)[2])
