"""
b23extract/formatter.py – Text and image response builders.
"""

from typing import Any, Optional

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .config import _cfg
from ...utils.i18n import t

_S = {
    "text_video": {"en": "[Video] {title}", "zh": "[视频] {title}"},
    "text_live": {"en": "[Live] {title}", "zh": "[直播] {title}"},
    "text_bangumi": {"en": "[Bangumi] {title}", "zh": "[番剧] {title}"},
    "text_article": {"en": "[Article] {title}", "zh": "[专栏] {title}"},
    "label_up": {"en": "UP", "zh": "UP"},
    "label_category": {"en": "Category", "zh": "分区"},
    "label_tags": {"en": "Tags", "zh": "标签"},
    "label_streamer": {"en": "Streamer", "zh": "主播"},
    "label_area": {"en": "Area", "zh": "分区"},
    "label_author": {"en": "Author", "zh": "作者"},
}


def _trunc_text(text: str) -> str:
    """Truncate description for text-mode replies."""
    max_len = _cfg.bilibili_desc_max_len
    if max_len > 0 and len(text) > max_len:
        return text[:max_len] + "…"
    return text


def build_text(info: dict[str, Any]) -> str:
    """Format a content info dict as a plain-text reply."""
    content_type = info["type"]
    title = info["title"]
    author = info["author"]
    category = info["category"]
    tags = info["tags"]
    desc = _trunc_text(info["description"])
    url = info["url"]

    if content_type == "video":
        parts = [t(_S["text_video"], title=title)]
        if author:
            parts.append(f"{t(_S['label_up'])}: {author}")
        if category:
            parts.append(f"{t(_S['label_category'])}: {category}")
        if tags:
            parts.append(f"{t(_S['label_tags'])}: {', '.join(tags[:8])}")
        if desc:
            parts.append(desc)
        parts.append(url)
        return "\n".join(parts)

    elif content_type == "live":
        parts = [t(_S["text_live"], title=title)]
        if author:
            parts.append(f"{t(_S['label_streamer'])}: {author}")
        if category:
            parts.append(f"{t(_S['label_area'])}: {category}")
        if tags:
            parts.append(f"{t(_S['label_tags'])}: {', '.join(tags)}")
        if desc:
            parts.append(desc)
        parts.append(url)
        return "\n".join(parts)

    elif content_type == "bangumi":
        parts = [t(_S["text_bangumi"], title=title)]
        if desc:
            parts.append(_trunc_text(desc))
        parts.append(url)
        return "\n".join(parts)

    elif content_type == "article":
        parts = [t(_S["text_article"], title=title)]
        if author:
            parts.append(f"{t(_S['label_author'])}: {author}")
        parts.append(url)
        return "\n".join(parts)

    return f"{title}\n{url}"


async def build_image_bytes(info: dict[str, Any]) -> bytes:
    """Build a preview card image and return PNG bytes."""
    from .card import build_bili_card

    return await build_bili_card(
        title=info["title"],
        author=info["author"],
        author_avatar_url=info["author_avatar"] or None,
        cover_url=info["cover_url"] or None,
        category=info["category"],
        tags=info["tags"],
        description=info["description"],
        url=info["url"],
        stats=info.get("stats"),
        post_time=info.get("post_time"),
        desc_max_lines=_cfg.bilibili_image_desc_max_lines,
        font_path=_cfg.card_font,
        font_weight=_cfg.card_font_weight,
        font_lang=_cfg.card_font_lang,
    )


def build_filter_reject_message() -> Optional[Message]:
    text = _cfg.bilibili_filter_reject_text.strip()
    image = _cfg.bilibili_filter_reject_image.strip()
    if not text and not image:
        return None
    parts: list[MessageSegment] = []
    if image:
        with open(image, "rb") as f:
            parts.append(MessageSegment.image(f.read()))
    if text:
        # Prefix newline only when image is present, to render text on next line.
        formatted_text = text if not image else f"\n{text}"
        parts.append(MessageSegment.text(formatted_text))
    return Message(parts)
