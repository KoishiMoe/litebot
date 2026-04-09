"""
login_notice.py – Notify superusers when the QQ account logs in or out on any device.

Listens for the `client_status` notice from the OneBot v11 adapter and forwards
a plain-text message to every configured superuser.
"""

import asyncio

from nonebot import on_notice, get_driver
from nonebot.adapters.onebot.v11 import Bot, NoticeEvent
from nonebot.log import logger
from nonebot.matcher import Matcher

from ..utils.i18n import t

_S = {
    "unknown_device": {"en": "Unknown device", "zh": "未知设备"},
    "action_login": {"en": "logged in", "zh": "登录"},
    "action_logout": {"en": "logged out", "zh": "登出"},
    "notice": {
        "en": "Notice: your account {action} on {device}{name}.{tail}",
        "zh": "通知：你的账号在 {device}{name}{action}。{tail}",
    },
    "name_wrap": {"en": " ({name})", "zh": "（{name}）"},
    "tail_login": {"en": "", "zh": ""},
    "tail_logout": {
        "en": " If this was not you, consider changing your password.",
        "zh": " 如非本人操作，请尽快修改密码。",
    },
}

driver = get_driver()

_login_notice = on_notice(block=False, priority=100)


@_login_notice.handle()
async def _handle_login(bot: Bot, event: NoticeEvent, matcher: Matcher) -> None:
    if getattr(event, "notice_type", None) != "client_status":
        return

    is_online: bool = getattr(event, "online", False)
    client: dict = getattr(event, "client", {}) or {}
    device_kind: str = client.get("device_kind", t(_S["unknown_device"]))
    device_name: str = client.get("device_name", "")

    action = t(_S["action_login"]) if is_online else t(_S["action_logout"])
    logger.info(f"[login_notice] account {action} on {device_kind} ({device_name})")

    name_part = t(_S["name_wrap"], name=device_name) if device_name else ""
    tail = t(_S["tail_login"]) if is_online else t(_S["tail_logout"])
    msg = t(_S["notice"], action=action, device=device_kind, name=name_part, tail=tail)

    for su in driver.config.superusers:
        try:
            await bot.send_private_msg(user_id=int(su), message=msg)
            await asyncio.sleep(0.5)
        except Exception as exc:
            logger.warning(f"[login_notice] failed to notify superuser {su}: {exc}")
