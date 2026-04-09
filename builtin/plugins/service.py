"""
service.py – Lightweight service control: enable/disable bot features per group or user.

Design
------
Features are ON by default.  Admins can restrict them for their context; the bot
owner (superuser) has full reach across all groups.

Scope model (all checked; any explicit disable wins):
    global          – everywhere; only superusers can write this
    g<gid>          – everyone in a group; group admin or superuser
    u<uid>          – a user everywhere (cross-group ban); superuser only
    g<gid>u<uid>    – a user in one specific group; group admin or superuser

Integrating a plugin
--------------------
    require("service")
    from .service import online, register

    register("my_plugin", "Short description shown in /svc")
    matcher = on_message(rule=online("my_plugin") & ...)

Commands (all under /svc)
--------------------------
    /svc                          List services and effective status for current context
    /svc @user                    (admin) List effective status for @user in this group
    /svc -g <gid> [-u <uid>]      (superuser, PM) List status for group / user-in-group

    /svc on  <name>               Enable service for current context
    /svc off <name>               Disable service for current context

    Context in group chat:
        no @mention  →  scope = this group  (group admin or superuser required)
        @user        →  scope = that user in this group

    Context in PM (superuser only):
        no flags     →  global
        -g <gid>     →  for that group
        -u <uid>     →  for that user (everywhere)
        -g <gid> -u <uid>  →  for that user in that group

Special: use * as the service name to affect all services at once.
"""

import asyncio
import json
import re
from typing import Optional

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from ..utils.i18n import t
from ..utils.storage import get_data_dir

_S = {
    "meta_desc": {"en": "Enable/disable bot features per group or user", "zh": "按群组或用户启用/禁用机器人功能"},
    "meta_usage": {
        "en": (
            "/svc                    - list services and status\n"
            "/svc on|off <name>      - toggle (group admins: current group; superuser PM: global)\n"
            "/svc on|off <name> @u   - toggle for @mentioned user in this group\n"
            "Superuser PM flags: -g <gid>  -u <uid>"
        ),
        "zh": (
            "/svc                    - 查看服务与状态\n"
            "/svc on|off <name>      - 切换服务（群管理：当前群；超级用户私聊：全局）\n"
            "/svc on|off <name> @u   - 为本群 @用户 切换服务\n"
            "超级用户私聊参数：-g <群号>  -u <用户号>"
        ),
    },
    "reason_off_global": {"en": "off globally", "zh": "全局已关闭"},
    "reason_off_group": {"en": "off in this group", "zh": "本群已关闭"},
    "reason_off_user": {"en": "off for you", "zh": "对你已关闭"},
    "reason_off_user_group": {"en": "off for you here", "zh": "仅本群对你关闭"},
    "list_user_group": {"en": "📋 Services for user {uid} in this group:", "zh": "📋 本群用户 {uid} 的服务状态："},
    "list_group": {"en": "📋 Services in group {gid}:", "zh": "📋 群 {gid} 的服务状态："},
    "hint_toggle_group": {"en": "💡 /svc on|off <name> - toggle for this group", "zh": "💡 /svc on|off <name> - 为当前群切换"},
    "hint_toggle_user": {"en": "   /svc on|off <name> @user - toggle for a specific user", "zh": "   /svc on|off <name> @user - 为指定用户切换"},
    "hint_admin_only": {"en": "Tip: admins can toggle services with /svc on|off <name>", "zh": "提示：管理员可用 /svc on|off <name> 切换服务"},
    "list_user_in_group": {"en": "📋 Services - user {uid} in group {gid}:", "zh": "📋 服务状态 - 群 {gid} 中用户 {uid}："},
    "list_group_pm": {"en": "📋 Services - group {gid}:", "zh": "📋 服务状态 - 群 {gid}："},
    "list_user_pm": {"en": "📋 Services - user {uid}:", "zh": "📋 服务状态 - 用户 {uid}："},
    "list_global": {"en": "📋 Global service status:", "zh": "📋 全局服务状态："},
    "hint_pm_global": {"en": "💡 /svc on|off <name>            - global", "zh": "💡 /svc on|off <name>            - 全局"},
    "hint_pm_group": {"en": "   /svc on|off <name> -g <gid>   - for a group", "zh": "   /svc on|off <name> -g <gid>   - 针对群组"},
    "hint_pm_user": {"en": "   /svc on|off <name> -u <uid>   - for a user", "zh": "   /svc on|off <name> -u <uid>   - 针对用户"},
    "hint_pm_all": {"en": "   (use * as name to affect all services at once)", "zh": "   （name 用 * 可一次作用于全部服务）"},
    "perm_group_admin": {"en": "Only group admins can manage services.", "zh": "只有群管理员可管理服务。"},
    "perm_superuser_pm": {"en": "Only superusers can manage services from private chat.", "zh": "仅超级用户可在私聊中管理服务。"},
    "usage_toggle": {"en": "Usage: /svc on|off <service>\nRun /svc to see available service names.", "zh": "用法：/svc on|off <service>\n发送 /svc 查看可用服务名。"},
    "unknown_service": {"en": "Unknown service: {service!r}\nAvailable: {names}", "zh": "未知服务：{service!r}\n可用服务：{names}"},
    "none_registered": {"en": "(none registered yet)", "zh": "（暂无已注册服务）"},
    "target_user_group": {"en": "user {uid} in this group", "zh": "本群用户 {uid}"},
    "target_this_group": {"en": "this group", "zh": "当前群"},
    "target_user_in_group": {"en": "user {uid} in group {gid}", "zh": "群 {gid} 中用户 {uid}"},
    "target_group": {"en": "group {gid}", "zh": "群 {gid}"},
    "target_user": {"en": "user {uid}", "zh": "用户 {uid}"},
    "target_global": {"en": "everyone (global)", "zh": "所有人（全局）"},
    "action_enabled": {"en": "enabled", "zh": "启用"},
    "action_disabled": {"en": "disabled", "zh": "禁用"},
    "label_all_services": {"en": "All services", "zh": "所有服务"},
    "toggle_done": {"en": "{icon} {label} {action} for {target}.", "zh": "{icon} 已在{target}{action}：{label}。"},
}

__plugin_meta__ = PluginMetadata(
    name="service",
    description=t(_S["meta_desc"]),
    usage=t(_S["meta_usage"]),
)

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_DATA_DIR = get_data_dir()
_DATA_FILE = _DATA_DIR / "service.json"
_write_lock = asyncio.Lock()

# Nested scope structure.  Only False values are stored; absent key → enabled.
#
#   {
#     "global": { service: false },
#     "group":  { gid: { service: false } },
#     "user":   { uid: { service: false } },
#     "member": { gid: { uid: { service: false } } }
#   }
_data: dict = {}

driver = get_driver()


@driver.on_startup
async def _load() -> None:
    global _data
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _DATA_FILE.is_file():
        try:
            _data = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
            # Count individual disable rules across all nesting levels
            def _count_rules(d: dict, depth: int = 0) -> int:
                if depth >= 2:  # reached service→bool leaf level
                    return len(d)
                return sum(_count_rules(v, depth + 1) if isinstance(v, dict) else 1 for v in d.values())
            total = _count_rules(_data)
            logger.info(f"[service] loaded {total} rule(s) from {_DATA_FILE}")
        except Exception as exc:
            logger.warning(f"[service] could not load {_DATA_FILE}: {exc}; starting empty")
            _data = {}
    else:
        _data = {}


async def _save() -> None:
    async with _write_lock:
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            _DATA_FILE.write_text(
                json.dumps(_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"[service] failed to persist {_DATA_FILE}: {exc}")


# ---------------------------------------------------------------------------
# Service registry
# ---------------------------------------------------------------------------

# name → description  (order = insertion order for display)
_services: dict[str, str] = {}


def register(service: str, description: str = "") -> None:
    """Register a service so it appears in /svc listings and can be toggled.

    Call once at module load:
        register("my_plugin", "Short description of what it does")
    """
    if not service or not re.match(r"^[A-Za-z0-9_-]+$", service):
        raise ValueError(
            f"[service] invalid service name {service!r}: "
            "must be non-empty and contain only letters, digits, hyphens, or underscores"
        )
    if service in _services:
        logger.warning(
            f"[service] duplicate registration for {service!r}; "
            "check for conflicting plugins"
        )
    _services[service] = description


# ---------------------------------------------------------------------------
# Core state API
# ---------------------------------------------------------------------------


def _get_scope_dict(scope_type: str, gid: Optional[str] = None, uid: Optional[str] = None) -> dict:
    """Return the service→bool mapping for the given scope (read-only view; may be empty)."""
    if scope_type == "global":
        return _data.get("global", {})
    if scope_type == "group":
        return _data.get("group", {}).get(gid or "", {})
    if scope_type == "user":
        return _data.get("user", {}).get(uid or "", {})
    if scope_type == "member":
        return _data.get("member", {}).get(gid or "", {}).get(uid or "", {})
    return {}


def _is_blocked(scope_type: str, service: str, gid: Optional[str] = None, uid: Optional[str] = None) -> bool:
    """True if this scope explicitly disables '*' (all) or the named service."""
    d = _get_scope_dict(scope_type, gid=gid, uid=uid)
    return d.get("*") is False or d.get(service) is False


def _resolve_scope_data(scope_type: str, gid: Optional[str], uid: Optional[str]) -> tuple[dict, list[tuple[dict, str]]]:
    """Return (scope_dict, [(parent_dict, key), ...]) for the given scope.

    scope_dict is the leaf mapping of service→bool (created if absent).
    The list of (parent, key) pairs is ordered from innermost to outermost,
    used to prune empty dicts after a modification.
    """
    if scope_type == "global":
        scope_data = _data.setdefault("global", {})
        parents = [(_data, "global")]
    elif scope_type == "group":
        groups = _data.setdefault("group", {})
        scope_data = groups.setdefault(gid, {})
        parents = [(groups, gid), (_data, "group")]
    elif scope_type == "user":
        users = _data.setdefault("user", {})
        scope_data = users.setdefault(uid, {})
        parents = [(users, uid), (_data, "user")]
    elif scope_type == "member":
        members = _data.setdefault("member", {})
        group_members = members.setdefault(gid, {})
        scope_data = group_members.setdefault(uid, {})
        parents = [(group_members, uid), (members, gid), (_data, "member")]
    else:
        raise ValueError(f"unknown scope_type: {scope_type!r}")
    return scope_data, parents


async def _set(scope_type: str, service: str, enabled: bool, gid: Optional[str] = None, uid: Optional[str] = None) -> None:
    """Apply an override and persist.  Enabling removes the restriction."""
    scope_data, parents = _resolve_scope_data(scope_type, gid, uid)
    if enabled:
        scope_data.pop(service, None)
    else:
        scope_data[service] = False
    # Prune empty dicts bottom-up
    for parent, key in parents:
        if not parent.get(key):
            parent.pop(key, None)

    await _save()


# ---------------------------------------------------------------------------
# Rule factory (public API for other plugins)
# ---------------------------------------------------------------------------


def is_online(
    service: str,
    gid: Optional[str] = None,
    uid: Optional[str] = None,
) -> bool:
    """Programmatic service check for plugins that act without an incoming event.

    Useful for proactive senders (e.g. ntfy forwarder) that need to decide whether
    to deliver to a particular group or user without holding a real Event object.

    Checked in order; first explicit disable wins:
        1. global  2. group (if gid given, else skipped)
        3. member (if both gid and uid given, else skipped)
        4. user (if uid given, else skipped)

    Args:
        service: The service name to check.
        gid:     Group ID string (e.g. "123456"), or None to skip group checks.
        uid:     User ID string (e.g. "654321"), or None to skip user checks.
    """
    if _is_blocked("global", service):
        return False
    if gid and _is_blocked("group", service, gid=gid):
        return False
    if gid and uid and _is_blocked("member", service, gid=gid, uid=uid):
        return False
    if uid and _is_blocked("user", service, uid=uid):
        return False
    return True


def online(service: str) -> Rule:
    """Rule that passes only when *service* is enabled for the incoming event.

    Checked in order; first explicit disable wins:
        1. global  2. group (group events)
        3. member (group events)  4. user (everywhere)
    """

    async def _check(bot: Bot, event: Event) -> bool:
        if _is_blocked("global", service):
            return False

        # Use isinstance for the synchronous fast-path instead of awaiting GROUP.
        if isinstance(event, GroupMessageEvent):
            gid = str(event.group_id)
            if _is_blocked("group", service, gid=gid):
                return False
            try:
                if _is_blocked("member", service, gid=gid, uid=event.get_user_id()):
                    return False
            except (ValueError, AttributeError):
                pass

        try:
            if _is_blocked("user", service, uid=event.get_user_id()):
                return False
        except (ValueError, AttributeError):
            pass

        return True

    return Rule(_check)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AT_RE = re.compile(r"\[CQ:at,qq=(\d+)\]")


def _extract_ats(text: str) -> list[str]:
    """Return QQ user IDs from any CQ:at segments in text."""
    return _AT_RE.findall(text)


def _strip_cq(text: str) -> str:
    """Remove all CQ codes from text."""
    return re.sub(r"\[CQ:[^\]]+\]", "", text).strip()


def _parse_flags(text: str) -> dict[str, str]:
    """Extract -g <val> and -u <val> flags from a token stream."""
    flags: dict[str, str] = {}
    parts = text.split()
    i = 0
    while i < len(parts):
        if parts[i] in ("-g", "-u") and i + 1 < len(parts):
            flags[parts[i][1:]] = parts[i + 1]
            i += 2
        else:
            i += 1
    return flags


def _valid_id(value: str) -> bool:
    """True for a numeric ID (no wildcard — '*' is only valid as a service name)."""
    return bool(value) and value.isdigit()


def _block_reason(service: str, gid: Optional[str], uid: Optional[str]) -> str:
    """Human-readable reason why service is blocked in this context, or ''."""
    if _is_blocked("global", service):
        return t(_S["reason_off_global"])
    if gid and _is_blocked("group", service, gid=gid):
        return t(_S["reason_off_group"])
    if uid and _is_blocked("user", service, uid=uid):
        return t(_S["reason_off_user"])
    if gid and uid and _is_blocked("member", service, gid=gid, uid=uid):
        return t(_S["reason_off_user_group"])
    return ""


# ---------------------------------------------------------------------------
# /svc – unified command
# ---------------------------------------------------------------------------

_svc = on_command("svc", aliases={"service", "services"}, priority=1, block=True)


@_svc.handle()
async def _handle(bot: Bot, event: MessageEvent, cmd_arg: Message = CommandArg()) -> None:
    full_args = str(cmd_arg).strip()
    at_targets = _extract_ats(full_args)
    clean = _strip_cq(full_args)
    tokens = clean.split()

    sub = tokens[0].lower() if tokens else ""
    if sub in ("on", "off"):
        await _do_toggle(bot, event, enable=(sub == "on"), tokens=tokens[1:], ats=at_targets)
    else:
        await _do_list(bot, event, args=clean, ats=at_targets)


# -- List / status view -------------------------------------------------------


async def _do_list(
    bot: Bot, event: MessageEvent, args: str, ats: list[str]
) -> None:
    is_su = await SUPERUSER(bot, event)

    if isinstance(event, GroupMessageEvent):
        gid = str(event.group_id)
        invoker_uid = str(event.user_id)
        is_admin = is_su or await GROUP_ADMIN(bot, event) or await GROUP_OWNER(bot, event)

        if ats and is_admin:
            # Admin asked about a specific user → show that user's effective status
            target = ats[0]
            lines = [t(_S["list_user_group"], uid=target), ""]
            for svc, desc in _services.items():
                reason = _block_reason(svc, gid, target)
                icon = "❌" if reason else "✅"
                hint = f"  ({reason})" if reason else ""
                lines.append(f"  {icon} {svc}{hint}")
        else:
            # Default: invoker's own effective status in this group
            lines = [t(_S["list_group"], gid=gid), ""]
            for svc, desc in _services.items():
                reason = _block_reason(svc, gid, invoker_uid)
                icon = "❌" if reason else "✅"
                desc_str = f"  — {desc}" if desc else ""
                hint = f"  ({reason})" if reason else ""
                lines.append(f"  {icon} {svc}{desc_str}{hint}")

        lines.append("")
        if is_admin:
            lines.append(t(_S["hint_toggle_group"]))
            lines.append(t(_S["hint_toggle_user"]))
        else:
            lines.append(t(_S["hint_admin_only"]))

    else:
        # PM
        flags = _parse_flags(args)
        g_flag = flags.get("g", "")
        u_flag = flags.get("u", "")

        if is_su and _valid_id(g_flag) and _valid_id(u_flag):
            lines = [t(_S["list_user_in_group"], uid=u_flag, gid=g_flag), ""]
            for svc, desc in _services.items():
                reason = _block_reason(svc, g_flag, u_flag)
                icon = "❌" if reason else "✅"
                hint = f"  ({reason})" if reason else ""
                lines.append(f"  {icon} {svc}{hint}")

        elif is_su and _valid_id(g_flag):
            lines = [t(_S["list_group_pm"], gid=g_flag), ""]
            for svc, desc in _services.items():
                reason = _block_reason(svc, g_flag, None)
                icon = "❌" if reason else "✅"
                hint = f"  ({reason})" if reason else ""
                lines.append(f"  {icon} {svc}{hint}")

        elif is_su and _valid_id(u_flag):
            lines = [t(_S["list_user_pm"], uid=u_flag), ""]
            for svc, desc in _services.items():
                reason = _block_reason(svc, None, u_flag)
                icon = "❌" if reason else "✅"
                hint = f"  ({reason})" if reason else ""
                lines.append(f"  {icon} {svc}{hint}")

        else:
            lines = [t(_S["list_global"]), ""]
            for svc, desc in _services.items():
                blocked = _is_blocked("global", svc)
                icon = "❌" if blocked else "✅"
                suffix = f"  — {desc}" if desc else ""
                lines.append(f"  {icon} {svc}{suffix}")
            if is_su:
                lines += [
                    "",
                    t(_S["hint_pm_global"]),
                    t(_S["hint_pm_group"]),
                    t(_S["hint_pm_user"]),
                    t(_S["hint_pm_all"]),
                ]

    await _svc.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# -- Toggle -------------------------------------------------------------------


async def _do_toggle(
    bot: Bot,
    event: MessageEvent,
    enable: bool,
    tokens: list[str],
    ats: list[str],
) -> None:
    is_su = await SUPERUSER(bot, event)

    # Permission gate
    if isinstance(event, GroupMessageEvent):
        is_admin = is_su or await GROUP_ADMIN(bot, event) or await GROUP_OWNER(bot, event)
        if not is_admin:
            await _svc.finish(MessageSegment.reply(event.message_id) + t(_S["perm_group_admin"]))
    else:
        if not is_su:
            await _svc.finish(MessageSegment.reply(event.message_id) + t(_S["perm_superuser_pm"]))

    # Service name
    if not tokens:
        await _svc.finish(MessageSegment.reply(event.message_id) + t(_S["usage_toggle"]))
    service = tokens[0]
    valid_names = set(_services) | {"*"}
    if service not in valid_names:
        names = "  ".join(_services) or t(_S["none_registered"])
        await _svc.finish(MessageSegment.reply(event.message_id) + t(_S["unknown_service"], service=service, names=names))

    # Determine scope
    if isinstance(event, GroupMessageEvent):
        gid = str(event.group_id)
        if ats:
            scope_type = "member"
            scope_gid: Optional[str] = gid
            scope_uid: Optional[str] = ats[0]
            target_desc = t(_S["target_user_group"], uid=ats[0])
        else:
            scope_type = "group"
            scope_gid = gid
            scope_uid = None
            target_desc = t(_S["target_this_group"])
    else:
        flags = _parse_flags(" ".join(tokens[1:]))
        g_flag = flags.get("g", "")
        u_flag = flags.get("u", "")
        if _valid_id(g_flag) and _valid_id(u_flag):
            scope_type = "member"
            scope_gid = g_flag
            scope_uid = u_flag
            target_desc = t(_S["target_user_in_group"], uid=u_flag, gid=g_flag)
        elif _valid_id(g_flag):
            scope_type = "group"
            scope_gid = g_flag
            scope_uid = None
            target_desc = t(_S["target_group"], gid=g_flag)
        elif _valid_id(u_flag):
            scope_type = "user"
            scope_gid = None
            scope_uid = u_flag
            target_desc = t(_S["target_user"], uid=u_flag)
        else:
            scope_type = "global"
            scope_gid = None
            scope_uid = None
            target_desc = t(_S["target_global"])

    await _set(scope_type, service, enable, gid=scope_gid, uid=scope_uid)
    action = t(_S["action_enabled"]) if enable else t(_S["action_disabled"])
    label = t(_S["label_all_services"]) if service == "*" else f"'{service}'"
    icon = "✅" if enable else "❌"
    await _svc.finish(MessageSegment.reply(event.message_id) + t(_S["toggle_done"], icon=icon, label=label, action=action, target=target_desc))
