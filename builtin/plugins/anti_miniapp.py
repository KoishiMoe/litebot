"""
anti_miniapp.py – Extract plain URLs from QQ mini-app / structured messages.

Handles three message formats sent by the QQ client:
  1. com.tencent.miniapp  – JSON blob embedded in the message
  2. com.tencent.structmsg – JSON blob for generic shared cards
  3. [CQ:xml,...] – XML card messages

All XML is parsed via *defusedxml* to prevent entity-expansion / XXE attacks.
JSON is parsed with the stdlib json module (no eval of untrusted data).

Optional config (in .env):
  ANTI_MINIAPP_IGNORED_KEYWORDS=["keyword1", "keyword2"]
"""

import json
import re
from typing import Optional

import defusedxml.ElementTree as ElementTree
from nonebot import on_message, require
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, unescape, MessageSegment
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from pydantic import BaseModel

from ..utils.i18n import t

require("mute")
from .mute import not_muted  # noqa: E402

require("service")
from .service import online, register  # noqa: E402

_S = {
    "service_desc": {"en": "Extract plain URLs from QQ mini-app / structured / XML messages", "zh": "提取 QQ 小程序/结构化/XML 消息中的直链"},
    "meta_desc": {"en": "Extract plain URLs from QQ mini-app / structured / XML messages", "zh": "提取 QQ 小程序/结构化/XML 消息中的直链"},
    "meta_usage": {"en": "Just send a mini-app or XML card; the bot will reply with the URL.", "zh": "直接发送小程序或 XML 卡片，机器人会回复对应链接。"},
}

register("anti_miniapp", t(_S["service_desc"]))

# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------
__plugin_meta__ = PluginMetadata(
    name="anti_miniapp",
    description=t(_S["meta_desc"]),
    usage=t(_S["meta_usage"]),
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class _Config(BaseModel):
    anti_miniapp_ignored_keywords: list[str] = []


from nonebot import get_plugin_config  # noqa: E402

_cfg = get_plugin_config(_Config)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_ignored(text: str) -> bool:
    for kw in _cfg.anti_miniapp_ignored_keywords:
        if re.search(kw, text, re.I):
            return True
    return False


def _parse_json_blob(raw: str) -> Optional[str]:
    """Return the URL extracted from a JSON blob, or None on failure."""
    try:
        blobs = re.findall(r"\{.*?\}", raw, re.DOTALL)
        for blob in blobs:
            blob = unescape(blob)
            try:
                data: dict = json.loads(blob)
            except json.JSONDecodeError:
                continue

            # com.tencent.miniapp → meta.detail_1.qqdocurl
            url = (
                data.get("meta", {})
                .get("detail_1", {})
                .get("qqdocurl", None)
            )
            if url:
                return url

            # com.tencent.structmsg → meta.<first key>.jumpUrl
            meta = data.get("meta", {})
            if meta:
                first_val = next(iter(meta.values()), {})
                url = first_val.get("jumpUrl", None) if isinstance(first_val, dict) else None
                if url:
                    return url
    except Exception as exc:
        logger.debug(f"[anti_miniapp] JSON parse error: {exc}")
    return None


def _parse_xml_message(raw_message: str) -> Optional[str]:
    """Extract url= attribute from a CQ:xml data payload using defusedxml."""
    try:
        # Extract the XML data= value from the CQ code
        match = re.search(r"data=(.+?(?:</msg>|\Z))", raw_message, re.DOTALL)
        if not match:
            return None
        xml_data = match.group(1)
        # defusedxml.ElementTree.fromstring raises on malicious payloads
        tree = ElementTree.fromstring(xml_data)

        # Root element may carry url= directly
        url = tree.get("url", "")
        if url:
            return unescape(url)

        # Otherwise search child elements
        for child in tree:
            url = child.get("url", "")
            if url:
                return unescape(url)
    except ElementTree.ParseError as exc:
        logger.debug(f"[anti_miniapp] XML parse error: {exc}")
    except Exception as exc:
        logger.warning(f"[anti_miniapp] unexpected XML error: {exc}")
    return None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _has_miniapp() -> Rule:
    async def _check(event: MessageEvent) -> bool:
        msg = str(event.message)
        return bool(
            re.search(r"com\.tencent\.(miniapp|structmsg)", msg)
            or re.search(r"\[CQ:xml", msg)
        )
    return Rule(_check)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

_handler = on_message(rule=_has_miniapp() & not_muted() & online("anti_miniapp"), priority=5, block=False)


@_handler.handle()
async def _handle(bot: Bot, event: MessageEvent) -> None:
    raw = str(event.raw_message).strip()
    plain = str(event.message).strip()

    if _is_ignored(plain) or _is_ignored(raw):
        return

    url: Optional[str] = None

    if re.search(r"\[CQ:xml", raw):
        url = _parse_xml_message(raw)
    else:
        url = _parse_json_blob(plain)

    if url:
        await _handler.finish(message=MessageSegment.reply(event.message_id) + MessageSegment.text(url))
    else:
        logger.info("[anti_miniapp] could not extract a URL from the message")
