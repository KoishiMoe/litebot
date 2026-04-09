"""
mcping – Query Minecraft server status and reply with text or image cards.

Supports both Java Edition (default port 25565) and Bedrock Edition (default
port 19132).

Commands
--------
``/mcping [name_or_address] [je|be]``
    Query the named server alias, the group's default server, or the given
    address.  The ``je`` / ``be`` hint overrides auto-detection when an
    address is given directly.

``/mc add <alias> <address[:port]> [je|be]``
    Save a server alias for the current group.  Requires group admin/owner or
    superuser.  The port is optional; defaults to 25565 (JE) or 19132 (BE).

``/mc del <alias>``
    Remove a saved alias.  Requires group admin/owner or superuser.

``/mc default <alias>``
    Set the group's default server.  Requires group admin/owner or superuser.

``/mclist``
    List all saved aliases for the current group (any member can run this).

Config keys (all optional, set in .env)
-----------------------------------------
  MCPING_IMAGE_MODE=auto   # auto (image when favicon available) | on | off
  CARD_FONT=               # shared CJK-capable font path
  CARD_FONT_WEIGHT=medium  # regular | medium | bold (TTC/OTC only)
"""

from __future__ import annotations

from typing import Optional

from nonebot import require, on_message, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from ...utils.command import cmd_arg, cmd_rule
from ...utils.i18n import t
from .config import _cfg
from .formatter import build_image, format_text_bedrock, format_text_java, want_image
from .protocol import autodetect_edition, parse_address, query_bedrock, query_java
from .storage import ServerRecord

require("mute")
from ..mute import not_muted  # noqa: E402

require("service")
from ..service import online, register  # noqa: E402

_S = {
    "service_desc": {"en": "Query Minecraft Java/Bedrock server status", "zh": "查询 Minecraft Java/Bedrock 服务器状态"},
    "meta_desc": {"en": "Query Minecraft Java/Bedrock server status with text or image cards", "zh": "查询 Minecraft Java/Bedrock 服务器状态（文本或图片卡片）"},
    "meta_usage": {
        "en": (
            "/mcping [server] [je|be]  - query status\n"
            "/mc add <alias> <addr[:port]> [je|be]  - save alias (admin)\n"
            "  IPv6: [::addr]:port  or  ::addr (no port)\n"
            "/mc del <alias>  - remove alias (admin)\n"
            "/mc default <alias>  - set group default (admin)\n"
            "/mclist  - list saved aliases"
        ),
        "zh": (
            "/mcping [服务器] [je|be]  - 查询状态\n"
            "/mc add <别名> <地址[:端口]> [je|be]  - 保存别名（管理员）\n"
            "  IPv6: [::addr]:port 或 ::addr（无端口）\n"
            "/mc del <别名>  - 删除别名（管理员）\n"
            "/mc default <别名>  - 设为本群默认（管理员）\n"
            "/mclist  - 查看已保存别名"
        ),
    },
    "need_address": {"en": "Please provide a server address.", "zh": "请提供服务器地址。"},
    "no_default": {"en": "No default server configured for this group.", "zh": "本群未配置默认服务器。"},
    "unreachable": {"en": "Cannot reach {name} - server may be offline or the address is wrong.", "zh": "无法连接 {name} - 服务器可能离线或地址错误。"},
    "query_failed": {"en": "Failed to query {kind} server {name}: {error}", "zh": "查询 {kind} 服务器 {name} 失败：{error}"},
    "kind_bedrock": {"en": "Bedrock", "zh": "基岩版"},
    "kind_java": {"en": "Java", "zh": "Java 版"},
    "usage_manage": {"en": "Usage:\n  mc add <alias> <addr[:port]> [je|be]\n  mc del <alias>\n  mc default <alias>\nIPv6: use [::addr]:port (with port) or ::addr (without port).", "zh": "用法：\n  mc add <别名> <地址[:端口]> [je|be]\n  mc del <别名>\n  mc default <别名>\nIPv6：带端口用 [::addr]:port，不带端口用 ::addr。"},
    "unknown_op": {"en": "Unknown operation {op!r}. Valid operations: add, del, default", "zh": "未知操作 {op!r}。可用操作：add, del, default"},
    "usage_add": {"en": "Usage: mc add <alias> <addr[:port]> [je|be]\nIPv6: [::addr]:port (with port) or ::addr (without port).", "zh": "用法：mc add <别名> <地址[:端口]> [je|be]\nIPv6：带端口用 [::addr]:port，不带端口用 ::addr。"},
    "usage_alias": {"en": "Usage: mc {op} <alias>", "zh": "用法：mc {op} <别名>"},
    "group_only": {"en": "This command can only be used in a group.", "zh": "此命令只能在群聊中使用。"},
    "detecting_for": {"en": "Detecting edition for {addr}...", "zh": "正在检测 {addr} 的版本..."},
    "cannot_detect": {"en": "Cannot reach the server to auto-detect its edition. Please append 'je' or 'be' explicitly.", "zh": "无法连接服务器进行自动版本检测，请显式追加 'je' 或 'be'。"},
    "added_alias": {"en": "Added alias '{alias}' -> {addr} ({kind}).", "zh": "已添加别名 '{alias}' -> {addr}（{kind}）。"},
    "alias_exists": {"en": "Alias '{alias}' already exists.", "zh": "别名 '{alias}' 已存在。"},
    "alias_removed": {"en": "Removed alias '{alias}'.", "zh": "已删除别名 '{alias}'。"},
    "alias_not_found": {"en": "Alias '{alias}' not found.", "zh": "未找到别名 '{alias}'。"},
    "set_default": {"en": "Set '{alias}' as the default server.", "zh": "已将 '{alias}' 设为默认服务器。"},
}

register("mcping", t(_S["service_desc"]))

__plugin_meta__ = PluginMetadata(
    name="mcping",
    description=t(_S["meta_desc"]),
    usage=t(_S["meta_usage"]),
)

# ---------------------------------------------------------------------------
# Command name sets
# ---------------------------------------------------------------------------

_MCPING_CMDS = ("mcping", "mcstatus", "服务器状态")
_MC_MANAGE_CMDS = ("mc", "minecraft")
_MCLIST_CMDS = ("mclist", "服务器列表")


# ---------------------------------------------------------------------------
# /mcping handler
# ---------------------------------------------------------------------------
# priority=1 matches the default used by on_command so these matchers run
# alongside other command-level handlers at the same tier.  block=True
# prevents lower-priority (or same-priority) matchers from processing the
# same event once a match succeeds.

mcping = on_message(
    rule=cmd_rule(*_MCPING_CMDS) & not_muted() & online("mcping"),
    priority=1,
    block=True,
)


@mcping.handle()
async def _mcping_handler(bot: Bot, event: MessageEvent) -> None:
    arg = cmd_arg(event, _MCPING_CMDS)

    address: str = ""
    port: int = 0
    is_be: Optional[bool] = None   # None = auto-detect
    display_name: str = ""

    if not arg:
        # No argument: try the group default server
        if not isinstance(event, GroupMessageEvent):
            await mcping.finish(MessageSegment.reply(event.message_id) + t(_S["need_address"]))
        rec_store = ServerRecord(event.group_id)
        rec = rec_store.get()
        if rec is None:
            await mcping.finish(MessageSegment.reply(event.message_id) + t(_S["no_default"]))
        address = rec["address"]
        port = rec.get("port", 0)
        is_be = rec.get("is_be", False)
        display_name = rec_store.default_alias
    else:
        params = arg.split()
        if len(params) >= 1:
            # First param: either an alias or a host[:port]
            if isinstance(event, GroupMessageEvent):
                rec_store = ServerRecord(event.group_id)
                rec = rec_store.get(params[0])
                if rec is not None:
                    address = rec["address"]
                    port = rec.get("port", 0)
                    is_be = rec.get("is_be", False)
                    display_name = params[0]

            if not address:
                # Treat as raw host[:port]
                address, port = parse_address(params[0])
                display_name = params[0]

        if len(params) >= 2:
            hint = params[1].lower()
            if hint in ("be", "pe", "bedrock"):
                is_be = True
            elif hint in ("je", "java"):
                is_be = False

    # Auto-detect edition if still unknown
    if is_be is None:
        edition = await autodetect_edition(address, port)
        if edition is None:
            await mcping.finish(
                MessageSegment.reply(event.message_id) + t(_S["unreachable"], name=display_name)
            )
        is_be = not edition

    # Query the server
    try:
        if is_be:
            status = await query_bedrock(address, port)
        else:
            status = await query_java(address, port)
    except Exception as exc:
        logger.warning(f"[mcping] query error for {display_name}: {exc}")
        kind = t(_S["kind_bedrock"]) if is_be else t(_S["kind_java"])
        await mcping.finish(
            MessageSegment.reply(event.message_id) + t(_S["query_failed"], kind=kind, name=display_name, error=exc)
        )

    # Build response
    if want_image(status, is_be):
        try:
            png = await build_image(display_name, status, is_be)
            await bot.send(event=event, message=MessageSegment.reply(event.message_id) + MessageSegment.image(png))
            return
        except Exception as exc:
            logger.warning(f"[mcping] image build failed: {exc}")
            # Fall back to text
    # Text response
    if is_be:
        text = format_text_bedrock(display_name, status)
    else:
        text = format_text_java(display_name, status)
    await mcping.finish(MessageSegment.reply(event.message_id) + text)


# ---------------------------------------------------------------------------
# /mc management handler (add / del / default)
# ---------------------------------------------------------------------------

# use on_command because `mc` and `minecraft` are common words that might appear in casual chat
mc_manage = on_command(
    "mc",
    aliases={"minecraft"},
    rule=not_muted() & online("mcping"),
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
)


@mc_manage.handle()
async def _mc_manage_handler(
    bot: Bot, event: MessageEvent, cmd_arg: Message = CommandArg()
) -> None:
    arg = cmd_arg.extract_plain_text().strip()
    params = arg.split()

    if not params:
        await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["usage_manage"]))

    op = params[0].lower()
    ADD = op in ("add", "添加", "bind", "绑定", "new")
    DEL = op in ("del", "delete", "remove", "删除", "解绑")
    DEFAULT = op in ("default", "默认", "设置默认")

    if not (ADD or DEL or DEFAULT):
        await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["unknown_op"], op=op))

    if ADD and len(params) < 3:
        await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["usage_add"]))
    if (DEL or DEFAULT) and len(params) < 2:
        await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["usage_alias"], op=op))

    if not isinstance(event, GroupMessageEvent):
        await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["group_only"]))

    gid = event.group_id
    store = ServerRecord(gid)
    alias = params[1]

    if ADD:
        raw_addr = params[2]
        address, port = parse_address(raw_addr)

        # Edition: explicit hint or auto-detect
        if len(params) >= 4:
            hint = params[3].lower()
            is_be = hint in ("be", "pe", "bedrock")
        else:
            await mc_manage.send(MessageSegment.reply(event.message_id) + t(_S["detecting_for"], addr=raw_addr))
            edition = await autodetect_edition(address, port)
            if edition is None:
                await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["cannot_detect"]))
            is_be = not edition  # edition=True means JE

        if store.add(alias, address, port, is_be):
            kind = t(_S["kind_bedrock"]) if is_be else t(_S["kind_java"])
            await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["added_alias"], alias=alias, addr=raw_addr, kind=kind))
        else:
            await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["alias_exists"], alias=alias))

    elif DEL:
        if store.remove(alias):
            await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["alias_removed"], alias=alias))
        else:
            await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["alias_not_found"], alias=alias))

    elif DEFAULT:
        if store.set_default(alias):
            await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["set_default"], alias=alias))
        else:
            await mc_manage.finish(MessageSegment.reply(event.message_id) + t(_S["alias_not_found"], alias=alias))


# ---------------------------------------------------------------------------
# /mclist handler
# ---------------------------------------------------------------------------

mclist = on_message(
    rule=cmd_rule(*_MCLIST_CMDS) & not_muted() & online("mcping"),
    priority=1,
    block=True,
)


@mclist.handle()
async def _mclist_handler(bot: Bot, event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        await mclist.finish(MessageSegment.reply(event.message_id) + t(_S["group_only"]))

    store = ServerRecord(event.group_id)
    await mclist.finish(MessageSegment.reply(event.message_id) + store.list_all())
