"""
drasl.py – Generate Drasl (Minecraft auth server) invite links on demand.

Responds to the /invite command (alias: 邀请) when addressed to the bot.
Superusers may always use it.  Other users must be in allowed_users or belong
to an allowed_groups group.  Per-user bans are handled by the service controller
(use /svc off drasl @user in the group, or /svc off drasl -u <uid> in PM).

A per-user invite counter is persisted to data/drasl/record.json.  Once a
user reaches DRASL_LIMIT invites they are refused (superusers are exempt).
On a successful API call the invite link is sent back.  If delivery fails
after the code has been created it is automatically revoked.

Config keys (all required unless noted, set in .env):
  DRASL_SERVER=                        # Drasl server base URL  (required)
  DRASL_TOKEN=                         # API bearer token        (required)
  DRASL_ALLOWED_GROUPS=[]             # group IDs allowed to use the command
  DRASL_ALLOWED_USERS=[]              # user IDs allowed in private chat
  DRASL_LIMIT=1                        # max invites per non-superuser (0 = unlimited)
"""

import asyncio
import json

import aiohttp
from nonebot import get_driver, get_plugin_config, on_command, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from pydantic import BaseModel

from ..utils.i18n import t
from ..utils.storage import get_data_dir

require("mute")
from .mute import not_muted  # noqa: E402

require("service")
from .service import online, register  # noqa: E402

_S = {
    "meta_desc": {
        "en": "Generate Drasl Minecraft auth-server invite links",
        "zh": "生成 Drasl Minecraft 认证服务器邀请链接",
    },
    "meta_usage": {
        "en": "/invite – get a Drasl registration invite link (subject to limits and permissions)",
        "zh": "/invite 或 /邀请 – 获取 Drasl 注册邀请链接（受权限与次数限制）",
    },
    "service_desc": {
        "en": "Drasl Minecraft invite command (/invite)",
        "zh": "Drasl Minecraft 邀请命令（/invite）",
    },
    "no_permission": {"en": "You don't have permission to use this command.", "zh": "你没有权限使用这个命令。"},
    "limit_reached": {"en": "You have reached your invite limit.", "zh": "你已经达到邀请上限了。"},
    "api_error": {"en": "Failed to obtain invite link (HTTP {status}).", "zh": "获取邀请链接失败，错误码：{status}。"},
    "unknown_error": {"en": "Failed to obtain invite link; please contact an admin.", "zh": "获取邀请链接失败，请联系管理员处理。"},
    "invite_link": {"en": "Invite link: {link}", "zh": "邀请链接：{link}"},
}

__plugin_meta__ = PluginMetadata(
    name="drasl",
    description=t(_S["meta_desc"]),
    usage=t(_S["meta_usage"]),
)

register("drasl", t(_S["service_desc"]))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class _Config(BaseModel):
    drasl_server: str = ""
    drasl_token: str = ""
    drasl_allowed_groups: list[int] = []
    drasl_allowed_users: list[int] = []
    drasl_limit: int = 1


_cfg = get_plugin_config(_Config)

# ---------------------------------------------------------------------------
# Persistent invite counter
# ---------------------------------------------------------------------------

_DATA_DIR = get_data_dir("drasl")
_RECORD_FILE = _DATA_DIR / "record.json"
_record_lock = asyncio.Lock()

# { str(user_id): count }
_invite_counts: dict[str, int] = {}


def _load_record() -> None:
    global _invite_counts
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _RECORD_FILE.is_file():
        try:
            _invite_counts = json.loads(_RECORD_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[drasl] Could not load invite record: {exc}; starting empty.")
            _invite_counts = {}
    else:
        _invite_counts = {}


def _save_record() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _RECORD_FILE.write_text(
        json.dumps(_invite_counts, indent=2), encoding="utf-8"
    )


def _get_count(user_id: int) -> int:
    return _invite_counts.get(str(user_id), 0)


def _increment(user_id: int) -> None:
    key = str(user_id)
    _invite_counts[key] = _invite_counts.get(key, 0) + 1
    _save_record()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

_session: aiohttp.ClientSession | None = None
_plugin_logger = logger.bind(name="drasl")

driver = get_driver()


@driver.on_startup
async def _startup() -> None:
    global _session
    _session = aiohttp.ClientSession()
    _load_record()


@driver.on_shutdown
async def _shutdown() -> None:
    if _session:
        await _session.close()


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

_invite_cmd = on_command(
    "invite",
    aliases={"邀请"},
    rule=online("drasl") & not_muted() & to_me(),
    priority=5,
    block=True,
)


@_invite_cmd.handle()
async def _handle_invite(bot: Bot, event: PrivateMessageEvent | GroupMessageEvent) -> None:
    # ── permission check ──────────────────────────────────────────────────────
    is_superuser = await SUPERUSER(bot, event)
    allowed = is_superuser

    if not allowed:
        if isinstance(event, PrivateMessageEvent):
            allowed = event.user_id in _cfg.drasl_allowed_users
        elif isinstance(event, GroupMessageEvent):
            allowed = event.group_id in _cfg.drasl_allowed_groups

    if not allowed:
        await _invite_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["no_permission"]))

    # ── limit check ───────────────────────────────────────────────────────────
    if (
        not is_superuser
        and _cfg.drasl_limit > 0
        and _get_count(event.user_id) >= _cfg.drasl_limit
    ):
        await _invite_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["limit_reached"]))

    # ── validate config ───────────────────────────────────────────────────────
    base = _cfg.drasl_server.strip().rstrip("/")
    token = _cfg.drasl_token.strip()
    if not base or not token:
        _plugin_logger.error("[drasl] DRASL_SERVER or DRASL_TOKEN not configured.")
        await _invite_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["unknown_error"]))

    headers = {"Authorization": f"Bearer {token}"}
    code: str = ""

    try:
        async with _session.post(
            f"{base}/drasl/api/v2/invites",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                await _invite_cmd.finish(MessageSegment.reply(event.message_id) + t(_S["api_error"], status=resp.status))
            data = await resp.json()

        code = data.get("code", "")
        invite_link: str = data.get("url", "")
        if not code or not invite_link:
            # Log the full response only to the server log, not to users
            _plugin_logger.warning(f"[drasl] Unexpected API response keys: {list(data.keys())!r}")
            raise ValueError("API response missing 'code' or 'url' fields")

        async with _record_lock:
            _increment(event.user_id)

        await _invite_cmd.finish(
            MessageSegment.reply(event.message_id)
            + t(_S["invite_link"], link=invite_link)
        )

    except MatcherException:
        raise
    except Exception as exc:
        _plugin_logger.error(f"[drasl] Error creating invite: {exc}")
        # Attempt rollback if the code was already created
        if code:
            try:
                async with _session.delete(
                    f"{base}/drasl/api/v2/invites/{code}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as del_resp:
                    if del_resp.status != 200:
                        _plugin_logger.error(
                            f"[drasl] Failed to revoke invite code {code!r}: "
                            f"HTTP {del_resp.status}"
                        )
            except Exception as exc2:
                _plugin_logger.error(
                    f"[drasl] Error revoking invite code {code!r}: {exc2}"
                )
        await _invite_cmd.send(MessageSegment.reply(event.message_id) + t(_S["unknown_error"]))
        raise

