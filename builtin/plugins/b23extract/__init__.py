"""
b23extract – Parse Bilibili links from messages and reply with rich info cards.

Supports:
  • Short links  : b23.tv/…  bili23.cn/… etc.
  • Video        : av/BV numbers, bilibili.com/video/…
  • Live         : live.bilibili.com/…
  • Bangumi      : bilibili.com/bangumi/…, ep/ss/md numbers
  • Article      : bilibili.com/read/cv…, cv numbers
  • QQ mini-app  : 哔哩哔哩 mini-app shares

Config keys (all optional, set in .env):
  BILIBILI_SESSDATA=
  BILIBILI_BILI_JCT=
  BILIBILI_BUVID3=
  BILIBILI_PROXY=                   # e.g. http://127.0.0.1:7890
  BILIBILI_DESC_MAX_LEN=180         # text-mode description truncation (0 = unlimited)
  BILIBILI_IMAGE_MODE=auto          # auto | on | off
  CARD_FONT=                        # absolute path to a CJK-capable font file (shared)
  CARD_FONT_WEIGHT=medium           # regular | medium | bold (for TTC/OTC faces, shared)
  CARD_FONT_LANG=sc                 # CJK variant: sc | tc | jp | kr | hk | "" (shared)
  # Emoji are rendered via bundled Twemoji v17.0.2 PNGs – no separate emoji font needed.
  BILIBILI_IMAGE_DESC_MAX_LINES=12  # max description lines in image card (0 = unlimited)
  BILIBILI_FILTER_UPLOADER_NAMES=[]       # literal substrings (case-insensitive)
  BILIBILI_FILTER_UPLOADER_NAME_REGEX=[]  # regexes for uploader name
  BILIBILI_FILTER_UPLOADER_UIDS=[]        # exact uploader UIDs
  BILIBILI_FILTER_SENDER_WHITELIST=[]     # sender UIDs bypassing filters
  BILIBILI_FILTER_TITLES=[]               # literal substrings (case-insensitive)
  BILIBILI_FILTER_TITLE_REGEX=[]          # regexes for title
  BILIBILI_FILTER_DESCRIPTIONS=[]         # literal substrings (case-insensitive)
  BILIBILI_FILTER_DESCRIPTION_REGEX=[]    # regexes for description
  BILIBILI_FILTER_TAGS=[]                 # literal substrings (case-insensitive)
  BILIBILI_FILTER_TAG_REGEX=[]            # regexes for each tag
  BILIBILI_FILTER_CATEGORIES=[]           # literal substrings (case-insensitive)
  BILIBILI_FILTER_CATEGORY_REGEX=[]       # regexes for category
  BILIBILI_FILTER_REJECT_TEXT=            # optional text reply when blocked
  BILIBILI_FILTER_REJECT_IMAGE=           # optional image (URL/path) when blocked
"""
import re

from nonebot import on_regex, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from bilibili_api.exceptions import NetworkException, ResponseCodeException

from ...utils.i18n import t
from .config import _cfg
from .filter import is_filtered, sender_bypasses_filter
from .formatter import build_filter_reject_message, build_image_bytes, build_text
from .parser import BILI_PATTERN, extract_info

require("mute")
from ..mute import not_muted  # noqa: E402

require("service")
from ..service import online, register  # noqa: E402

_S = {
    "service_desc": {"en": "Parse Bilibili links/IDs and reply with media info cards", "zh": "解析哔哩哔哩链接/编号并回复信息卡"},
    "meta_desc": {"en": "Parse Bilibili links/IDs and reply with media info", "zh": "解析哔哩哔哩链接/编号并回复媒体信息"},
    "meta_usage": {"en": "Just send a Bilibili link, AV/BV/CV number, or mini-app share.", "zh": "直接发送 Bilibili 链接、AV/BV/CV 编号或小程序分享即可。"},
    "fetch_failed": {"en": "Failed to fetch post info: {error}", "zh": "获取稿件信息失败：{error}"},
    "fetch_network": {"en": "Failed to fetch post info: network error", "zh": "获取稿件信息失败：网络错误"},
    "fetch_unexpected": {"en": "Failed to fetch post info: unexpected error", "zh": "获取稿件信息失败：未知错误"},
}

register("b23extract", t(_S["service_desc"]))

__plugin_meta__ = PluginMetadata(
    name="b23extract",
    description=t(_S["meta_desc"]),
    usage=t(_S["meta_usage"]),
)

# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

_handler = on_regex(pattern=BILI_PATTERN, flags=re.I, rule=not_muted() & online("b23extract"), priority=10, block=False)


def _image_with_url_message(png: bytes, title: str, url: str, reply_id: int) -> Message:
    parts: list = [MessageSegment.reply(reply_id), MessageSegment.image(png)]
    if title:
        parts.append(MessageSegment.text(f"\n{title}\n{url}"))
    else:
        parts.append(MessageSegment.text(f"\n{url}"))
    return Message(parts)


@_handler.handle()
async def _handle(bot: Bot, event: MessageEvent) -> None:
    text = str(event.message).strip()

    try:
        info = await extract_info(text)
    except ResponseCodeException as exc:
        logger.info(f"[b23extract] API error: {exc}")
        await _handler.finish(message=MessageSegment.reply(event.message_id) + Message(t(_S["fetch_failed"], error=exc)))
        return
    except NetworkException as exc:
        logger.error(f"[b23extract] Network error: {exc}")
        await _handler.finish(message=MessageSegment.reply(event.message_id) + Message(t(_S["fetch_network"])))
        return
    except Exception as exc:
        logger.error(f"[b23extract] Unexpected error: {exc}")
        await _handler.finish(message=MessageSegment.reply(event.message_id) + Message(t(_S["fetch_unexpected"])))
        return

    if not info:
        return

    filter_reason = is_filtered(info) if not sender_bypasses_filter(event) else None
    if filter_reason is not None:
        logger.info(f"[b23extract] Content filtered: {filter_reason}")
        reject = build_filter_reject_message()
        if reject:
            await _handler.finish(message=MessageSegment.reply(event.message_id) + reject)
        return

    mode = _cfg.bilibili_image_mode

    # ── Image mode: "on" ─────────────────────────────────────────────────────
    if mode == "on":
        try:
            png = await build_image_bytes(info)
            await bot.send(event=event, message=_image_with_url_message(png, info["title"], info["url"], event.message_id))
        except Exception as exc:
            logger.error(f"[b23extract] Image generation failed (mode=on): {exc}")
            await _handler.finish(message=MessageSegment.reply(event.message_id) + Message(build_text(info)))
        return

    # ── Image mode: "off" ────────────────────────────────────────────────────
    if mode == "off":
        await _handler.finish(message=MessageSegment.reply(event.message_id) + Message(build_text(info)))
        return

    # ── Image mode: "auto" (default) ─────────────────────────────────────────
    # Generate image when a cover is present; fall back to text if there is no
    # cover or if image generation raises an exception.
    if info.get("cover_url"):
        try:
            png = await build_image_bytes(info)
            await bot.send(event=event, message=_image_with_url_message(png, info["title"], info["url"], event.message_id))
            return
        except Exception as exc:
            logger.warning(
                f"[b23extract] Image generation failed (mode=auto), "
                f"falling back to text: {exc}"
            )

    await _handler.finish(message=MessageSegment.reply(event.message_id) + Message(build_text(info)))
