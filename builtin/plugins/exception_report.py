"""
exception_report.py – Catch unhandled matcher exceptions and report them to superusers.

Also provides :func:`report_to_superusers` – a shared helper used by any plugin that
needs to notify the configured set of superusers about an error.

Exceptions are stored in a simple in-memory ring-buffer keyed by a numeric track-id.
The `/track` command (superuser-only) lets admins inspect stored reports.

Config keys (all optional, set in .env):
  ERROR_REPORT_SUPERUSERS=all   # "all" (default) | [] (disable) | [uid1, uid2, …]
"""

import asyncio
from collections import OrderedDict
from time import localtime, strftime
from traceback import format_exc
from typing import Literal

from nonebot import get_driver, get_plugin_config, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, MessageSegment, PrivateMessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State
from pydantic import BaseModel

from ..utils.i18n import t

_S = {
    "notify_prompt": {
        "en": (
            "An error occurred (track_id={tid})\n"
            "Time   : {time}\n"
            "Type   : {type}\n"
            "Message: {value}\n"
            "Tip    : use '/track {tid}' for the full traceback"
        ),
        "zh": (
            "发生错误（track_id={tid}）\n"
            "时间   : {time}\n"
            "类型   : {type}\n"
            "消息   : {value}\n"
            "提示   : 使用 '/track {tid}' 查看完整堆栈"
        ),
    },
    "no_records": {"en": "No error records found.", "zh": "没有错误记录。"},
    "cleared": {"en": "All error records cleared.", "zh": "已清空所有错误记录。"},
    "invalid_id": {"en": "Invalid track id - must be a positive integer.", "zh": "无效的 track id - 必须是正整数。"},
    "id_not_found": {"en": "No record found for track_id={tid}.", "zh": "未找到 track_id={tid} 的记录。"},
    "record_fmt": {
        "en": (
            "Track ID : {tid}\n"
            "Time     : {time}\n"
            "Type     : {type}\n"
            "Message  : {value}\n"
            "Traceback:\n{trace}"
        ),
        "zh": (
            "Track ID : {tid}\n"
            "时间     : {time}\n"
            "类型     : {type}\n"
            "消息     : {value}\n"
            "堆栈:\n{trace}"
        ),
    },
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class _Config(BaseModel):
    # "all"          → notify every superuser defined in SUPERUSERS
    # []             → disable error reporting entirely
    # [uid1, uid2]   → notify only the listed UIDs
    error_report_superusers: list[int] | Literal["all"] = "all"


_cfg = get_plugin_config(_Config)

driver = get_driver()


# ---------------------------------------------------------------------------
# Shared error-reporting helper
# ---------------------------------------------------------------------------


async def report_to_superusers(bot: Bot, message: str) -> None:
    """Send *message* to the configured set of superusers as a private message.

    Call this from any plugin that needs to surface an error to admins.
    Respects the ``ERROR_REPORT_SUPERUSERS`` config key:
    - ``"all"``        → all UIDs listed in the ``SUPERUSERS`` config
    - ``[]``           → no-op (reporting disabled)
    - ``[uid, …]``     → only the given UIDs
    """
    targets: list[int]
    if _cfg.error_report_superusers == "all":
        targets = [int(su) for su in driver.config.superusers if su.isdigit()]  # This bot is only designed for onebotv11, but who knows how the config might be set up, so let's be defensive and filter out non-numeric superuser IDs.
    elif not _cfg.error_report_superusers:
        return
    else:
        targets = list(_cfg.error_report_superusers)

    for su in targets:
        try:
            await bot.send_private_msg(user_id=su, message=message)
            await asyncio.sleep(0.5)
        except Exception as notify_exc:
            logger.warning(f"[exception_report] failed to notify superuser {su}: {notify_exc}")


# ---------------------------------------------------------------------------
# Ring-buffer store
# ---------------------------------------------------------------------------

# In-memory store: track_id (str) → {time, type, value, trace}
# Capped at _MAX_RECORDS entries; oldest entries are evicted automatically.
_MAX_RECORDS = 1_000
_TRACK_START = 100_000  # 6-digit minimum makes it easy to copy in QQ
_records: OrderedDict[int, dict] = OrderedDict()
_next_id: int = _TRACK_START


def _alloc_id() -> int:
    global _next_id
    tid = _next_id
    _next_id += 1
    if _next_id > 9_999_999_999:
        _next_id = _TRACK_START
    # evict oldest when cap reached
    while len(_records) >= _MAX_RECORDS:
        _records.popitem(last=False)
    return tid


@run_postprocessor
async def _exception_hook(
    matcher: Matcher,
    exception: Exception | None,
    bot: Bot,
    event,
    state: T_State,
) -> None:
    if exception is None:
        return

    try:
        raise exception
    except Exception:
        trace = format_exc()

    exc_type = type(exception).__name__
    exc_value = str(exception)
    exc_time = strftime("%Y-%m-%d %H:%M:%S", localtime())

    tid = _alloc_id()
    _records[tid] = {
        "time": exc_time,
        "type": exc_type,
        "value": exc_value,
        "trace": trace,
    }
    logger.warning(f"[exception_report] unhandled exception (track_id={tid}): {exc_type}: {exc_value}")

    prompt = t(
        _S["notify_prompt"],
        tid=tid,
        time=exc_time,
        type=exc_type,
        value=exc_value,
    )
    await report_to_superusers(bot, prompt)


track = on_command("track", permission=SUPERUSER, priority=1, block=True)


@track.handle()
async def _track_cmd(event: PrivateMessageEvent, arg: Message = CommandArg()) -> None:
    param = arg.extract_plain_text().strip()

    if not param:
        # return the most recent record
        if not _records:
            await track.finish(MessageSegment.reply(event.message_id) + t(_S["no_records"]))
        tid = next(reversed(_records))
        await track.finish(MessageSegment.reply(event.message_id) + _format_record(tid))

    if param in ("clear", "clean"):
        _records.clear()
        await track.finish(MessageSegment.reply(event.message_id) + t(_S["cleared"]))

    if not param.isdigit():
        await track.finish(MessageSegment.reply(event.message_id) + t(_S["invalid_id"]))

    tid = int(param)
    if tid not in _records:
        await track.finish(MessageSegment.reply(event.message_id) + t(_S["id_not_found"], tid=tid))

    await track.finish(MessageSegment.reply(event.message_id) + _format_record(tid))


def _format_record(tid: int) -> str:
    rec = _records[tid]
    return t(
        _S["record_fmt"],
        tid=tid,
        time=rec["time"],
        type=rec["type"],
        value=rec["value"],
        trace=rec["trace"],
    )
