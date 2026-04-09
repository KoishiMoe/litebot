"""
mute.py – Track when the bot is muted/unmuted in QQ groups.

On bot_connect the current mute state of every group is loaded.
Whenever a GroupBanNoticeEvent targeting the bot arrives the
in-memory record is updated and superusers are notified.

Other plugins can call `not_muted()` to obtain a NoneBot Rule
that blocks responses while the bot is muted.
"""

from time import time

from nonebot import get_driver, on_notice
from nonebot.adapters.onebot.v11 import Bot, Event, GroupBanNoticeEvent
from nonebot.adapters.onebot.v11.permission import GROUP
from nonebot.log import logger
from nonebot.rule import Rule

from ..utils.i18n import t

_S = {
    "muted": {
        "en": "I was muted in group {gid} by {op} for {duration} seconds.",
        "zh": "我在群 {gid} 被 {op} 禁言 {duration} 秒。",
    },
    "unmuted": {
        "en": "I was unmuted in group {gid} by {op}.",
        "zh": "我在群 {gid} 被 {op} 解除禁言。",
    },
}

# { group_id: {"time": float, "duration": int} }
_mute_record: dict[int, dict] = {}

driver = get_driver()


@driver.on_bot_connect
async def _load_mute_states(bot: Bot) -> None:
    """Populate _mute_record with any active mutes the bot has on startup."""
    global _mute_record

    groups: list[dict] = []
    for attempt in range(3):
        try:
            groups = await bot.get_group_list()
            break
        except Exception as exc:
            logger.warning(f"[mute] get_group_list attempt {attempt + 1}/3 failed: {exc}")

    if not groups:
        return

    _mute_record = {}
    now = time()
    for group in groups:
        gid = group.get("group_id")
        if gid is None:
            continue
        try:
            member = await bot.get_group_member_info(
                group_id=gid, user_id=int(bot.self_id)
            )
            shutdown_ts: int = member.get("shut_up_timestamp", 0)
            if shutdown_ts and shutdown_ts > now:
                _mute_record[gid] = {
                    "time": now,
                    "duration": int(shutdown_ts - now),
                }
        except Exception as exc:
            logger.warning(f"[mute] failed to query mute state for group {gid}: {exc}")


_ban_listener = on_notice(block=False)


@_ban_listener.handle()
async def _handle_ban(bot: Bot, event: GroupBanNoticeEvent) -> None:
    if not event.is_tome():
        return

    superusers = driver.config.superusers
    gid = event.group_id

    if event.duration:
        _mute_record[gid] = {"time": time(), "duration": event.duration}
        logger.info(
            f"[mute] muted in group {gid} by {event.operator_id} for {event.duration}s"
        )
        msg = (
            t(_S["muted"], gid=gid, op=event.operator_id, duration=event.duration)
        )
    else:
        _mute_record.pop(gid, None)
        logger.info(f"[mute] unmuted in group {gid} by {event.operator_id}")
        msg = t(_S["unmuted"], gid=gid, op=event.operator_id)

    for su in superusers:
        try:
            await bot.send_private_msg(user_id=int(su), message=msg)
        except Exception as exc:
            logger.warning(f"[mute] failed to notify superuser {su}: {exc}")


def is_muted(group_id: int) -> bool:
    """Return True if the bot is currently muted in *group_id*.

    Useful for proactive senders (e.g. ntfy forwarder) that need to decide
    whether to skip a group delivery without holding a real Event object.
    """
    from time import time as _time
    rec = _mute_record.get(group_id)
    if rec is None:
        return False
    if _time() - rec["time"] > rec["duration"]:
        _mute_record.pop(group_id, None)
        return False
    return True


def not_muted() -> Rule:
    """Return a Rule that is False while the bot is actively muted in a group."""

    async def _check(bot: Bot, event: Event) -> bool:
        if not await GROUP(bot, event):
            return True
        gid: int = getattr(event, "group_id", None)
        if gid is None:
            return True
        rec = _mute_record.get(gid)
        if rec is None:
            return True
        if time() - rec["time"] > rec["duration"]:
            _mute_record.pop(gid, None)
            return True
        return False

    return Rule(_check)
