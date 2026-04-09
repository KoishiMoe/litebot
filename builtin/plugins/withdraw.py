"""
withdraw.py – Allow users to recall bot messages in a conversation.

The bot tracks every message it sends (via Bot.on_called_api).
Users can then ask the bot to delete those messages with:

    withdraw              – recall the most recent message (index 0)
    withdraw 2            – recall the message at index 2 (0 = newest, 1 = second newest, …)
    withdraw 0-3          – recall messages at indices 0 through 3 (inclusive)
    withdraw +5           – recall the 6 most recent messages (indices 0–5)
    withdraw <reply>      – recall the replied-to message

The command must @mention the bot.  No command prefix is required.

Optional config (in .env):
  WITHDRAW_MAX_HISTORY=200   # max messages to remember per conversation (default 200)
"""

import asyncio
from collections import deque
from typing import Any

from nonebot import on_message, require, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from pydantic import BaseModel

from ..utils.command import cmd_arg, cmd_rule
from ..utils.i18n import t

_S = {
    "meta_desc": {"en": "Recall bot messages on demand", "zh": "按需撤回机器人消息"},
    "meta_usage": {
        "en": (
            "withdraw - recall the last message (index 0 = newest)\n"
            "withdraw N - recall message at 0-based index N from most recent\n"
            "withdraw N-M - recall all messages at indices N to M inclusive\n"
            "withdraw +N - recall the N+1 most recent messages (indices 0 to N)\n"
            "Or reply to a bot message and say withdraw\n"
            "(Prefix the bot @mention; no command prefix required)"
        ),
        "zh": (
            "withdraw - 撤回最近一条消息（索引 0 为最新）\n"
            "withdraw N - 撤回倒序第 N 条（从 0 开始）\n"
            "withdraw N-M - 撤回索引 N 到 M（含）\n"
            "withdraw +N - 撤回最近 N+1 条（索引 0 到 N）\n"
            "或回复机器人消息后发送 withdraw\n"
            "（需要 @机器人；无需命令前缀）"
        ),
    },
    "service_desc": {"en": "Recall bot-sent messages on demand", "zh": "按需撤回机器人发送的消息"},
    "no_match": {"en": "No matching messages to recall.", "zh": "没有可撤回的匹配消息。"},
    "partial_fail": {"en": "Some messages could not be recalled (may have timed out).", "zh": "部分消息撤回失败（可能已超时）。"},
}

__plugin_meta__ = PluginMetadata(
    name="withdraw",
    description=t(_S["meta_desc"]),
    usage=t(_S["meta_usage"]),
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class _Config(BaseModel):
    withdraw_max_history: int = 200


from nonebot import get_plugin_config  # noqa: E402

_cfg = get_plugin_config(_Config)

require("service")
from .service import online, register  # noqa: E402

register("withdraw", t(_S["service_desc"]))

driver = get_driver()

# { "group_123" | "private_456" : deque[int] }  (newest → appended to right)
_msg_ids: dict[str, deque[int]] = {}


def _key(msg_type: str, uid: int) -> str:
    return f"{msg_type}_{uid}"


# ---------------------------------------------------------------------------
# Hook: record every message the bot sends
# ---------------------------------------------------------------------------


@Bot.on_called_api
async def _record_sent_msg(
    bot: Bot,
    exc: Exception | None,
    api: str,
    data: dict[str, Any],
    result: Any,
) -> None:
    if exc is not None:
        return
    try:
        if api == "send_msg":
            msg_type = data.get("message_type", "")
            uid = data.get("group_id") if msg_type == "group" else data.get("user_id")
        elif api == "send_group_msg":
            msg_type = "group"
            uid = data.get("group_id")
        elif api == "send_private_msg":
            msg_type = "private"
            uid = data.get("user_id")
        else:
            return

        if uid is None:
            return

        msg_id: int = result.get("message_id")
        if msg_id is None:
            return

        key = _key(msg_type, uid)
        q = _msg_ids.setdefault(key, deque(maxlen=_cfg.withdraw_max_history))
        q.append(msg_id)
    except Exception as exc:
        logger.warning(f"[withdraw] failed to record message: {exc}")


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

_WITHDRAW_CMDS = ("withdraw", "recall", "撤回")

_withdraw = on_message(rule=to_me() & cmd_rule(*_WITHDRAW_CMDS) & online("withdraw"), priority=1, block=True)


@_withdraw.handle()
async def _handle_withdraw(bot: Bot, event: MessageEvent) -> None:
    arg = cmd_arg(event, _WITHDRAW_CMDS)
    # Determine conversation key
    if isinstance(event, GroupMessageEvent):
        msg_type, uid = "group", event.group_id
    elif isinstance(event, PrivateMessageEvent):
        msg_type, uid = "private", event.user_id
    else:
        return

    key = _key(msg_type, uid)

    # Handle reply-to recall
    if event.reply:
        reply_id: int = event.reply.message_id
        await _delete(bot, [reply_id])
        return

    # Parse parameter string
    param = arg.strip()
    ids_to_recall: list[int] = _resolve_indices(key, param)

    if not ids_to_recall:
        await _withdraw.finish(MessageSegment.reply(event.message_id) + t(_S["no_match"]))

    failed = await _delete(bot, ids_to_recall, key)
    if failed:
        await _withdraw.finish(MessageSegment.reply(event.message_id) + t(_S["partial_fail"]))


def _resolve_indices(key: str, param: str) -> list[int]:
    """Translate a parameter string to a list of actual message IDs."""
    q = _msg_ids.get(key)
    if not q:
        return []

    q_list = list(q)  # index 0 = oldest; -1 = newest

    def _get(idx: int) -> int | None:
        # idx=0 → newest; idx=1 → second newest
        pos = -(idx + 1)
        if abs(pos) > len(q_list):
            return None
        return q_list[pos]

    if not param:
        mid = _get(0)
        return [mid] if mid is not None else []

    indices: set[int] = set()

    for token in param.split():
        token = token.strip()
        if token.isdigit():
            indices.add(int(token))
        elif token.startswith("+") and token[1:].isdigit():
            n = int(token[1:])
            indices.update(range(n + 1))
        elif "-" in token:
            parts = token.split("-", 1)
            if parts[0] == "" and parts[1].isdigit():
                # "-N" → indices 0 … N-1
                indices.update(range(int(parts[1])))
            elif parts[1] == "" and parts[0].isdigit():
                # "N-" → N … end
                indices.update(range(int(parts[0]), len(q_list)))
            elif parts[0].isdigit() and parts[1].isdigit():
                start, end = int(parts[0]), int(parts[1])
                indices.update(range(start, end + 1))

    result: list[int] = []
    for idx in sorted(indices):
        mid = _get(idx)
        if mid is not None:
            result.append(mid)
    return result


async def _delete(bot: Bot, msg_ids: list[int], key: str | None = None) -> bool:
    """Attempt to delete each message; return True if any failed."""
    any_failed = False
    for mid in msg_ids:
        try:
            await bot.delete_msg(message_id=mid)
            if key and mid in _msg_ids.get(key, []):
                _msg_ids[key].remove(mid)
            await asyncio.sleep(0.3)
        except Exception as exc:
            logger.warning(f"[withdraw] delete_msg({mid}) failed: {exc}")
            any_failed = True
    return any_failed
