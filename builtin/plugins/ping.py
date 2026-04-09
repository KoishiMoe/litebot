"""
ping.py – /ping command to check bot status and responsiveness.

Replies with "Pong!" and the event-delivery latency (time between the
message timestamp – an OB11 Unix-second integer – and the handler wall clock).
"""

import time

from nonebot import on_command, require
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata

from ..utils.i18n import t

_S = {
    "meta_desc": {"en": "Check bot status and responsiveness", "zh": "检查机器人在线状态与响应速度"},
    "meta_usage": {"en": "/ping - reply with Pong! and show event delivery latency", "zh": "/ping - 回复 Pong! 并显示事件投递延迟"},
    "service_desc": {"en": "Bot status check (/ping)", "zh": "机器人状态检测（/ping）"},
    "pong": {"en": "Pong! ({latency} ms)", "zh": "Pong! ({latency} 毫秒)"},
}

__plugin_meta__ = PluginMetadata(
    name="ping",
    description=t(_S["meta_desc"]),
    usage=t(_S["meta_usage"]),
)

require("service")
from .service import online, register  # noqa: E402

require("mute")
from .mute import not_muted  # noqa: E402

register("ping", t(_S["service_desc"]))

_ping = on_command("ping", rule=online("ping") & not_muted(), priority=1, block=True)


@_ping.handle()
async def _handle_ping(event: MessageEvent) -> None:
    # event.time is an OB11 Unix timestamp (int seconds); time.time() is float seconds
    latency_ms = max(0, int((time.time() - event.time) * 1000))
    await _ping.finish(MessageSegment.reply(event.message_id) + t(_S["pong"], latency=latency_ms))
