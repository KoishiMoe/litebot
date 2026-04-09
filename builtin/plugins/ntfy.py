"""
ntfy.py – Forward messages from ntfy.sh channels to QQ groups/users.

Listens to configured ntfy channels via WebSocket and relays text and
image/video attachments to QQ groups or private users.

Architecture
------------
The plugin uses a multi-stage async pipeline to fully decouple receiving from
sending and centralise error reporting:

    Channel listener(s)          ── push raw ntfy event → _inbound_queue
    Dispatcher task              ── builds Message (incl. media download),
                                    distributes to per-target send queues
    Target sender workers        ── one task per unique target; applies
                                    service/mute checks before each send;
                                    pushes failures → _error_queue
    Error reporter task          ── drains _error_queue, logs and notifies
                                    superusers via the shared report_to_superusers
                                    helper (see exception_report.py /
                                    ERROR_REPORT_SUPERUSERS config)

Config keys (all optional unless noted, set in .env):
  NTFY_SERVER=https://ntfy.sh          # ntfy server base URL
  NTFY_TOKEN=                          # Bearer token for authenticated servers
  NTFY_RECONNECT_INTERVAL=10           # seconds between reconnect attempts
  NTFY_CACHE_CLEAN_INTERVAL=60         # minutes between media-cache sweeps
  NTFY_TO_QQ_MAPPING=[]               # list of {ntfy_channel, qq_targets} dicts
                                       # qq_targets: "group_<id>" or "user_<id>"
  NTFY_ATTACHMENT_HOST_MAPPING={}      # substitute download hosts for attachments
"""

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import aiohttp
from nonebot import get_driver, get_plugin_config, require
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from pydantic import BaseModel

from ..utils.i18n import t

require("service")
from .service import is_online, register  # noqa: E402

require("mute")
from .mute import is_muted  # noqa: E402

require("exception_report")
from .exception_report import report_to_superusers  # noqa: E402

_S = {
    "meta_desc": {
        "en": "Forward ntfy channel messages to QQ groups/users",
        "zh": "将 ntfy 频道消息转发至 QQ 群组或用户",
    },
    "meta_usage": {
        "en": "Configure NTFY_TO_QQ_MAPPING in .env to set forwarding rules.",
        "zh": "在 .env 中配置 NTFY_TO_QQ_MAPPING 设置转发规则。",
    },
    "service_desc": {
        "en": "ntfy channel → QQ forwarding",
        "zh": "ntfy 频道 → QQ 消息转发",
    },
    "attachment_prefix": {
        "en": "Attachment: ",
        "zh": "附件："
    },
}

__plugin_meta__ = PluginMetadata(
    name="ntfy",
    description=t(_S["meta_desc"]),
    usage=t(_S["meta_usage"]),
)

register("ntfy", t(_S["service_desc"]))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class _Config(BaseModel):
    ntfy_server: str = "https://ntfy.sh"
    ntfy_token: str = ""
    ntfy_reconnect_interval: int = 10
    ntfy_cache_clean_interval: int = 60
    ntfy_to_qq_mapping: list[dict[str, Any]] = []
    ntfy_attachment_host_mapping: dict[str, str] = {}


_cfg = get_plugin_config(_Config)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TARGET_GROUP_PREFIX = "group_"
_TARGET_USER_PREFIX = "user_"

# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------

_plugin_logger = logger.bind(name="ntfy")
_session: aiohttp.ClientSession | None = None
_media_cache_dir: tempfile.TemporaryDirectory | None = None

# Queue items:
#   _inbound_queue : tuple[dict, list[str]]         – (ntfy_data, target_list)
#   _target_queues : tuple[Message, str]            – (message, error_context)
#   _error_queue   : tuple[str, Exception, str]     – (target, exc, error_context)
_inbound_queue: asyncio.Queue[tuple[dict, list[str]]] = asyncio.Queue()
_target_queues: dict[str, asyncio.Queue[tuple[Message, str]]] = {}
_error_queue: asyncio.Queue[tuple[str, Exception, str]] = asyncio.Queue()

_all_tasks: list[asyncio.Task] = []

driver = get_driver()


def _get_bot():
    """Return the first available bot instance, or None if none are connected."""
    bots = driver.bots
    return next(iter(bots.values()), None) if bots else None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@driver.on_startup
async def _startup() -> None:
    global _session, _media_cache_dir
    _session = aiohttp.ClientSession()
    _media_cache_dir = tempfile.TemporaryDirectory()

    if not _cfg.ntfy_to_qq_mapping:
        _plugin_logger.info("[ntfy] No NTFY_TO_QQ_MAPPING configured; listener inactive.")
        return

    # Collect all unique targets to create per-target queues and worker tasks
    unique_targets: dict[str, None] = {}
    for mapping in _cfg.ntfy_to_qq_mapping:
        for target in mapping.get("qq_targets", []):
            unique_targets[target] = None

    for target in unique_targets:
        _target_queues[target] = asyncio.Queue()
        _all_tasks.append(
            asyncio.create_task(_target_sender(target), name=f"ntfy-sender-{target}")
        )

    # One listener task per configured channel
    for mapping in _cfg.ntfy_to_qq_mapping:
        channel = mapping.get("ntfy_channel", "")
        targets = mapping.get("qq_targets", [])
        if channel and targets:
            _all_tasks.append(
                asyncio.create_task(
                    _channel_listener(channel, targets),
                    name=f"ntfy-listener-{channel}",
                )
            )

    _all_tasks.append(asyncio.create_task(_dispatcher(), name="ntfy-dispatcher"))
    _all_tasks.append(asyncio.create_task(_error_reporter(), name="ntfy-error-reporter"))
    _all_tasks.append(asyncio.create_task(_cache_cleaner(), name="ntfy-cache-cleaner"))

    _plugin_logger.info(
        f"[ntfy] Started {sum(1 for t in _all_tasks if 'listener' in t.get_name())} "
        f"listener(s), {len(unique_targets)} sender worker(s)."
    )


@driver.on_shutdown
async def _shutdown() -> None:
    for task in _all_tasks:
        task.cancel()
    if _session:
        await _session.close()
    if _media_cache_dir:
        _media_cache_dir.cleanup()


# ---------------------------------------------------------------------------
# Stage 1 – WebSocket channel listeners
# ---------------------------------------------------------------------------


async def _channel_listener(channel: str, targets: list[str]) -> None:
    """Receive ntfy events and push raw data onto the inbound queue."""
    url = f"{_cfg.ntfy_server.rstrip('/')}/{channel}/ws"
    headers = (
        {"Authorization": f"Bearer {_cfg.ntfy_token}"} if _cfg.ntfy_token else {}
    )
    while True:
        try:
            async with _session.ws_connect(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as ws:
                _plugin_logger.info(f"[ntfy] Connected to channel: {channel}")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        _plugin_logger.debug(f"[ntfy] Received from {channel}: {data}")
                        _inbound_queue.put_nowait((data, targets))
        except asyncio.CancelledError:
            _plugin_logger.info(f"[ntfy] Listener for {channel} cancelled.")
            return
        except Exception as exc:
            _plugin_logger.error(
                f"[ntfy] Listener error for {channel}: {exc}; "
                f"retrying in {_cfg.ntfy_reconnect_interval}s"
            )
            await asyncio.sleep(_cfg.ntfy_reconnect_interval)


# ---------------------------------------------------------------------------
# Stage 2 – Dispatcher: build message, distribute to target queues
# ---------------------------------------------------------------------------

# Suppress ntfy keepalive messages and generic share notifications
_SKIP_CONTENT_RE = re.compile(
    r"^(\s*|An? \w+ was shared with you|Files were shared with you|You received a file: .+)$",
    re.IGNORECASE,
)


async def _dispatcher() -> None:
    """Build Message objects from raw ntfy data and fan-out to target queues."""
    while True:
        try:
            data, targets = await _inbound_queue.get()
            try:
                message = await _build_message(data)
                if not message:
                    continue
                # Build a concise error context string for use in error reports
                content = data.get("message", "") or ""
                attachment = data.get("attachment") or {}
                context_parts = [f"content={content!r}"]
                if attachment:
                    context_parts.append(f"attachment_url={attachment.get('url')!r}")
                context = "  ".join(context_parts)
                for target in targets:
                    if target in _target_queues:
                        _target_queues[target].put_nowait((message, context))
            finally:
                _inbound_queue.task_done()
        except asyncio.CancelledError:
            _plugin_logger.info("[ntfy] Dispatcher cancelled.")
            return
        except Exception as exc:
            _plugin_logger.error(f"[ntfy] Dispatcher error: {exc}")


async def _build_message(data: dict[str, Any]) -> Message | None:
    """Build a NoneBot Message from a ntfy event dict.  Returns None to skip."""
    segments = Message()

    content: str = data.get("message", "") or ""
    if content and not _SKIP_CONTENT_RE.match(content):
        segments.append(MessageSegment.text(content))

    attachment: dict = data.get("attachment") or {}
    if attachment:
        url: str = attachment.get("url", "")
        mime: str = attachment.get("type", "")
        if url:
            if mime.startswith("image/") or mime.startswith("video/"):
                file_path = await _download_media(url)
                if file_path:
                    if mime.startswith("image/"):
                        with open(file_path, "rb") as _f:
                            segments.append(MessageSegment.image(_f.read()))
                    else:
                        with open(file_path, "rb") as _f:
                            segments.append(MessageSegment.video(_f.read()))
                else:
                    segments.append(MessageSegment.text(f"{t(_S["attachment_prefix"])}{url}"))
            else:
                segments.append(MessageSegment.text(f"{t(_S["attachment_prefix"])}{url}"))

    return segments if segments else None


# ---------------------------------------------------------------------------
# Stage 3 – Target sender workers (one per target)
# ---------------------------------------------------------------------------


async def _target_sender(target: str) -> None:
    """Drain the per-target queue and deliver messages, respecting service/mute state."""
    queue = _target_queues[target]
    while True:
        try:
            message, context = await queue.get()
            try:
                # Resolve target type and apply service / mute checks
                if target.startswith(_TARGET_GROUP_PREFIX):
                    gid_int = int(target[len(_TARGET_GROUP_PREFIX):])
                    if not is_online("ntfy", gid=str(gid_int)):
                        _plugin_logger.info(
                            f"[ntfy] Service 'ntfy' disabled for group {gid_int}; skipping."
                        )
                        continue
                    if is_muted(gid_int):
                        _plugin_logger.info(
                            f"[ntfy] Bot is muted in group {gid_int}; skipping."
                        )
                        continue
                elif target.startswith(_TARGET_USER_PREFIX):
                    uid_int = int(target[len(_TARGET_USER_PREFIX):])
                    if not is_online("ntfy", uid=str(uid_int)):
                        _plugin_logger.info(
                            f"[ntfy] Service 'ntfy' disabled for user {uid_int}; skipping."
                        )
                        continue
                else:
                    _plugin_logger.warning(f"[ntfy] Unknown target format: {target!r}; skipping.")
                    continue

                bot = _get_bot()
                if not bot:
                    _plugin_logger.error("[ntfy] No bot instances available.")
                    continue

                if target.startswith(_TARGET_GROUP_PREFIX):
                    await bot.send_group_msg(group_id=gid_int, message=message)
                else:
                    await bot.send_private_msg(user_id=uid_int, message=message)

                await asyncio.sleep(1)  # brief pause to avoid flooding

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _error_queue.put((target, exc, context))
            finally:
                queue.task_done()

        except asyncio.CancelledError:
            _plugin_logger.info(f"[ntfy] Sender for {target} cancelled.")
            return


# ---------------------------------------------------------------------------
# Stage 4 – Error reporter (centralised)
# ---------------------------------------------------------------------------


async def _error_reporter() -> None:
    """Drain the error queue, log each failure, and optionally notify superusers."""
    while True:
        try:
            target, exc, context = await _error_queue.get()
            try:
                _plugin_logger.error(f"[ntfy] Failed to send to {target}: {exc}  ({context})")

                bot = _get_bot()
                if bot:
                    await report_to_superusers(
                        bot,
                        f"[ntfy] Failed to send to {target}: {exc}\n{context}",
                    )
            finally:
                _error_queue.task_done()

        except asyncio.CancelledError:
            _plugin_logger.info("[ntfy] Error reporter cancelled.")
            return
        except Exception as exc:
            _plugin_logger.error(f"[ntfy] Unexpected error in error reporter: {exc}")


# ---------------------------------------------------------------------------
# Media helpers
# ---------------------------------------------------------------------------


async def _download_media(url: str) -> str | None:
    if not _media_cache_dir:
        return None
    for src, dst in _cfg.ntfy_attachment_host_mapping.items():
        if url.startswith(src):
            url = url.replace(src, dst, 1)
            break
    try:
        # Strip query string, take only the final path component, and remove any
        # directory separators to prevent path traversal out of the cache dir.
        raw_name = os.path.basename(url.split("?")[0])
        file_name = re.sub(r"[/\\]", "_", raw_name) or "attachment"
        cache_root = Path(_media_cache_dir.name).resolve()
        dest = (cache_root / file_name).resolve()
        # Guard: ensure the resolved destination is inside the cache directory
        dest.relative_to(cache_root)
        async with _session.get(
            url, timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            if resp.status != 200:
                _plugin_logger.error(
                    f"[ntfy] Failed to download {url}: HTTP {resp.status}"
                )
                return None
            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(65536):
                    f.write(chunk)
        return str(dest)
    except Exception as exc:
        _plugin_logger.error(f"[ntfy] Error downloading {url}: {exc}")
        return None


async def _cache_cleaner() -> None:
    interval_seconds = _cfg.ntfy_cache_clean_interval * 60
    while True:
        await asyncio.sleep(interval_seconds)
        if not _media_cache_dir:
            return
        cache = Path(_media_cache_dir.name)
        removed = 0
        for item in cache.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                    removed += 1
            except Exception as exc:
                _plugin_logger.error(f"[ntfy] Error removing cached file {item}: {exc}")
        _plugin_logger.info(f"[ntfy] Cache sweep complete; removed {removed} file(s).")

