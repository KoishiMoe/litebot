"""
group_notice.py – Send welcome/farewell messages in group chats.

When a user joins a group the bot is in, it sends a configurable welcome
message that @mentions the new member.  When a user leaves (or is kicked),
it sends a configurable farewell message.

The bot's own join/leave events are silently ignored.

Placeholders in message templates:
    #userid   – numeric QQ ID of the member
    #username – display name (group card, or nickname as fallback)

Per-group templates are managed with the /notice command and stored in
data/group_notice.json.  When no group-specific template is set, the
global defaults (GROUP_JOIN_MSG / GROUP_LEAVE_MSG) are used.

Global config (in .env):
    GROUP_JOIN_MSG   – default join template  (empty = disabled)
    GROUP_LEAVE_MSG  – default leave template (empty = disabled)

/notice command:
    /notice                       – show current templates for this group
    /notice join <template>       – set join template (empty = disable)
    /notice leave <template>      – set leave template (empty = disable)
    /notice reset                 – revert to global defaults
    Superuser in PM: add -g <gid> before the subcommand

Integration:
    Registers the "group_notice" service; toggle per group with /svc.
    Respects the bot-mute state from the mute plugin.
"""

import asyncio
import json
import re

from nonebot import get_driver, on_command, on_notice, require
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupDecreaseNoticeEvent,
    GroupIncreaseNoticeEvent,
    MessageEvent,
    MessageSegment,
)
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from pydantic import BaseModel

from ..utils.i18n import t
from ..utils.storage import get_data_dir

__plugin_meta__ = PluginMetadata(
    name="group_notice",
    description=t({"en": "Send welcome/farewell messages when members join or leave a group", "zh": "成员入群/退群时发送欢迎与告别消息"}),
    usage=t({
        "en": (
            "Runs automatically - no command needed.\n"
            "Manage per-group templates with /notice:\n"
            "  /notice                       - show current templates\n"
            "  /notice join <template>       - set join template (empty = disable)\n"
            "  /notice leave <template>      - set leave template (empty = disable)\n"
            "  /notice reset                 - revert to global defaults\n"
            "  Superuser PM: /notice -g <gid> [subcommand]\n"
            "Placeholders: #userid  #username\n"
            "Use /svc off group_notice to disable entirely for a group."
        ),
        "zh": (
            "自动运行 - 无需命令触发。\n"
            "使用 /notice 管理群模板：\n"
            "  /notice                       - 查看当前模板\n"
            "  /notice join <模板>           - 设置入群模板（留空=禁用）\n"
            "  /notice leave <模板>          - 设置退群模板（留空=禁用）\n"
            "  /notice reset                 - 重置为全局默认\n"
            "  超级用户私聊：/notice -g <群号> [子命令]\n"
            "占位符：#userid  #username\n"
            "可用 /svc off group_notice 为某群整体禁用。"
        ),
    }),
)

_S = {
    "no_group":     {"en": "This command can only be used in a group, or with -g <gid> in PM.",
                     "zh": "此命令只能在群聊中使用，或在私聊中加 -g <群号>。"},
    "no_perm":      {"en": "Only group admins or superusers can configure notice templates.",
                     "zh": "只有群管理员或超级用户才能配置通知模板。"},
    "show_header":  {"en": "📋 Notice templates for group {gid}", "zh": "📋 群 {gid} 的通知模板"},
    "label_join":   {"en": "Join",  "zh": "入群"},
    "label_leave":  {"en": "Leave", "zh": "退群"},
    "src_default":  {"en": "default", "zh": "默认"},
    "src_custom":   {"en": "custom",  "zh": "已自定义"},
    "disabled":     {"en": "(disabled)", "zh": "（已禁用）"},
    "show_footer":  {"en": ("Subcommands: join <template>  leave <template>  reset\n"
                            "Placeholders: #userid  #username\n"
                            "Empty template = disabled."),
                     "zh": ("子命令：join <模板>  leave <模板>  reset\n"
                            "占位符：#userid  #username\n"
                            "留空模板 = 禁用通知。")},
    "set_join_ok":  {"en": "✅ Join template updated for group {gid}.",
                     "zh": "✅ 已更新群 {gid} 的入群通知模板。"},
    "set_leave_ok": {"en": "✅ Leave template updated for group {gid}.",
                     "zh": "✅ 已更新群 {gid} 的退群通知模板。"},
    "reset_ok":     {"en": "✅ Templates reset to defaults for group {gid}.",
                     "zh": "✅ 已将群 {gid} 的模板重置为默认值。"},
    "usage":        {"en": ("Usage:\n"
                            "  /notice                 – show current templates\n"
                            "  /notice join <template> – set join template\n"
                            "  /notice leave <template>– set leave template\n"
                            "  /notice reset           – reset to global defaults\n"
                            "  Superuser PM: add -g <gid> before subcommand"),
                     "zh": ("用法：\n"
                            "  /notice                 – 查看当前模板\n"
                            "  /notice join <模板>     – 设置入群通知模板\n"
                            "  /notice leave <模板>    – 设置退群通知模板\n"
                            "  /notice reset           – 重置为默认模板\n"
                            "  超级用户私聊：在子命令前加 -g <群号>")},
}


# ---------------------------------------------------------------------------
# Global default config
# ---------------------------------------------------------------------------


class _Config(BaseModel):
    """Global default templates applied when no per-group override is set.

    Per-group overrides take precedence and are managed with /notice.
    Setting a template to empty string disables that notice type.
    """

    group_join_msg: str = t({"en": "Welcome, #username!", "zh": "欢迎，#username！"})
    group_leave_msg: str = t({"en": "#username has left.", "zh": "#username 退群了。"})


from nonebot import get_plugin_config  # noqa: E402

_cfg = get_plugin_config(_Config)

# ---------------------------------------------------------------------------
# Per-group persistence
# ---------------------------------------------------------------------------

_DATA_DIR = get_data_dir()
_DATA_FILE = _DATA_DIR / "group_notice.json"
_write_lock = asyncio.Lock()

# { "group_id": {"join_msg": str | None, "leave_msg": str | None} }
# None means "use global default"; "" means "disabled".
_group_data: dict[str, dict[str, str | None]] = {}

driver = get_driver()


@driver.on_startup
async def _load() -> None:
    global _group_data
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _DATA_FILE.is_file():
        try:
            _group_data = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
            logger.info(f"[group_notice] loaded templates for {len(_group_data)} group(s)")
        except Exception as exc:
            logger.warning(f"[group_notice] could not load {_DATA_FILE}: {exc}; starting empty")
            _group_data = {}
    else:
        _group_data = {}


async def _save() -> None:
    async with _write_lock:
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            _DATA_FILE.write_text(
                json.dumps(_group_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"[group_notice] failed to persist {_DATA_FILE}: {exc}")


def _get_join_msg(gid: str) -> str:
    """Return the effective join template for a group (falls back to global default)."""
    val = _group_data.get(gid, {}).get("join_msg")
    return _cfg.group_join_msg if val is None else val


def _get_leave_msg(gid: str) -> str:
    """Return the effective leave template for a group (falls back to global default)."""
    val = _group_data.get(gid, {}).get("leave_msg")
    return _cfg.group_leave_msg if val is None else val


async def _set_template(gid: str, key: str, value: str | None) -> None:
    """Set (or clear) a single template key for a group and persist."""
    if value is None:
        # Remove override → fall back to global default
        if gid in _group_data:
            _group_data[gid].pop(key, None)
            if not _group_data[gid]:
                del _group_data[gid]
    else:
        _group_data.setdefault(gid, {})[key] = value
    await _save()


# ---------------------------------------------------------------------------
# Service integration
# ---------------------------------------------------------------------------

require("service")
from .service import online, register  # noqa: E402

require("mute")
from .mute import not_muted  # noqa: E402

register("group_notice", t({"en": "Join/leave notices in group chats", "zh": "群聊入群/退群通知"}))

# ---------------------------------------------------------------------------
# Notice event handlers
# ---------------------------------------------------------------------------

_join_notice = on_notice(rule=online("group_notice") & not_muted(), block=False)
_leave_notice = on_notice(rule=online("group_notice") & not_muted(), block=False)


def _render(template: str, user_id: int, username: str) -> str:
    return template.replace("#userid", str(user_id)).replace("#username", username)


@_join_notice.handle()
async def _handle_join(bot: Bot, event: GroupIncreaseNoticeEvent) -> None:
    if event.user_id == int(bot.self_id):
        return

    template = _get_join_msg(str(event.group_id))
    if not template:
        return

    username = str(event.user_id)
    try:
        info = await bot.get_group_member_info(group_id=event.group_id, user_id=event.user_id)
        username = info.get("card") or info.get("nickname") or username
    except Exception as exc:
        logger.warning(f"[group_notice] could not get member info for {event.user_id}: {exc}")

    try:
        await bot.send_group_msg(
            group_id=event.group_id,
            message=MessageSegment.at(event.user_id) + " " + _render(template, event.user_id, username),
        )
    except Exception as exc:
        logger.warning(f"[group_notice] could not send join notice to group {event.group_id}: {exc}")


@_leave_notice.handle()
async def _handle_leave(bot: Bot, event: GroupDecreaseNoticeEvent) -> None:
    if event.user_id == int(bot.self_id):
        return

    template = _get_leave_msg(str(event.group_id))
    if not template:
        return

    username = str(event.user_id)
    try:
        info = await bot.get_stranger_info(user_id=event.user_id)
        username = info.get("nickname") or username
    except Exception as exc:
        logger.warning(f"[group_notice] could not get stranger info for {event.user_id}: {exc}")

    try:
        await bot.send_group_msg(
            group_id=event.group_id,
            message=_render(template, event.user_id, username),
        )
    except Exception as exc:
        logger.warning(f"[group_notice] could not send leave notice to group {event.group_id}: {exc}")


# ---------------------------------------------------------------------------
# /notice – management command
# ---------------------------------------------------------------------------

_notice_cmd = on_command("notice", priority=1, block=True)

_GROUP_FLAG_RE = re.compile(r"-g\s+(\d+)")


@_notice_cmd.handle()
async def _handle_notice(bot: Bot, event: MessageEvent, arg: Message = CommandArg()) -> None:
    is_su = await SUPERUSER(bot, event)
    arg_text = arg.extract_plain_text().strip()

    from nonebot.adapters.onebot.v11 import GroupMessageEvent  # local import avoids circular

    if isinstance(event, GroupMessageEvent):
        gid = str(event.group_id)
        is_admin = is_su or await GROUP_ADMIN(bot, event) or await GROUP_OWNER(bot, event)
    else:
        # Private message – superuser only, must supply -g <gid>
        if not is_su:
            await _notice_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["no_perm"]))
        m = _GROUP_FLAG_RE.search(arg_text)
        if not m:
            await _notice_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["no_group"]))
        gid = m.group(1)
        arg_text = _GROUP_FLAG_RE.sub("", arg_text).strip()
        is_admin = True

    tokens = arg_text.split(None, 1)
    sub = tokens[0].lower() if tokens else ""
    rest = tokens[1].strip() if len(tokens) > 1 else ""

    if sub == "join":
        if not is_admin:
            await _notice_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["no_perm"]))
        await _set_template(gid, "join_msg", rest if rest else "")
        await _notice_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["set_join_ok"], gid=gid))

    elif sub == "leave":
        if not is_admin:
            await _notice_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["no_perm"]))
        await _set_template(gid, "leave_msg", rest if rest else "")
        await _notice_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["set_leave_ok"], gid=gid))

    elif sub == "reset":
        if not is_admin:
            await _notice_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["no_perm"]))
        # Remove any per-group overrides so global defaults take over
        _group_data.pop(gid, None)
        await _save()
        await _notice_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["reset_ok"], gid=gid))

    else:
        # Show current effective templates
        join_tmpl = _get_join_msg(gid)
        leave_tmpl = _get_leave_msg(gid)
        is_custom_join = gid in _group_data and _group_data[gid].get("join_msg") is not None
        is_custom_leave = gid in _group_data and _group_data[gid].get("leave_msg") is not None

        join_disp = join_tmpl if join_tmpl else t(_S["disabled"])
        leave_disp = leave_tmpl if leave_tmpl else t(_S["disabled"])
        join_src = t(_S["src_custom"]) if is_custom_join else t(_S["src_default"])
        leave_src = t(_S["src_custom"]) if is_custom_leave else t(_S["src_default"])

        lines = [t(_S["show_header"], gid=gid), ""]
        lines.append(f"  {t(_S['label_join'])}: {join_disp}  [{join_src}]")
        lines.append(f"  {t(_S['label_leave'])}: {leave_disp}  [{leave_src}]")
        lines += ["", t(_S["show_footer"])]
        await _notice_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
