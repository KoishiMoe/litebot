"""
mc_whitelist_sync.py – Bidirectional Minecraft whitelist mirroring + QQ notifications.

Each MC server gets **one persistent WebSocket** used for both:
  - receiving ``minecraft:notification/allowlist/added`` /
    ``minecraft:notification/allowlist/removed`` notifications
  - sending ``allowlist`` / ``allowlist/add`` / ``allowlist/remove`` requests

**Echo-loop prevention**

When the bot calls ``allowlist/add`` on server B on behalf of a notification
from server A, server B will itself broadcast an ``allowlist/added``
notification (the MC protocol unconditionally notifies all WS clients of any
whitelist change, regardless of origin).

Without intervention, this notification from B would trigger another round
of sync — back to A, and so on.

The fix is a ``_synced`` map of ``(name, target_url) → expiry`` for every
add/remove the bot itself performed.  The dispatcher checks this map BEFORE
calling ``_sync_add`` / ``_sync_remove`` — when the echo comes back, the
entry is found, consumed, and the loop terminates with **zero extra RPCs**.
Each entry expires after a TTL so a lost echo cannot suppress a later
legitimate change forever.

**Startup reconciliation**

On first connect, every server's whitelist is compared and a union is computed.
Each server receives only the players it is missing.  Players are never removed.

**QQ commands**

    ``/whitelist add <name>``   – add a player to all servers
    ``/whitelist remove <name>`` – remove a player from all servers (superuser PM only)

    Permission for ``add`` is controlled by ``MC_WL_ADD_PERMISSION``:
    - ``"all"``          – anyone can use /whitelist add  *(default)*
    - ``"group_admin"``  – group admins/owners (or superusers)
    - ``"superuser"``    – superusers only
    Private-chat access always requires superuser (add + remove).

Config keys (set in .env)::

    MC_WL_SERVERS=[{"url":"wss://host:port","token":"...","label":"...","insecure":false}, ...]
                    # per server: token (Bearer auth), label (QQ display name,
                    # defaults to "Server N"), insecure (skip TLS verification
                    # for a self-signed cert; overrides MC_WL_INSECURE_SSL)
    MC_WL_ADD_PERMISSION=all          # all | group_admin | superuser
    MC_WL_SYNC_ON_START=true         # full reconcile on boot  (default: true)
    MC_WL_RECONNECT_INTERVAL=10      # seconds between reconnect attempts
    MC_WL_NOTIFY_GROUPS=[]           # QQ group IDs to notify on whitelist changes
    MC_WL_INSECURE_SSL=false         # global default: skip TLS cert verification
"""

import asyncio
import json
import re
import time
from typing import Any, Literal, Optional

import aiohttp
from nonebot import get_driver, get_plugin_config, on_command, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from pydantic import BaseModel

from ..utils.i18n import t

require("service")
from .service import online, register  # noqa: E402

require("mute")
from .mute import not_muted  # noqa: E402

require("exception_report")

# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------

_S = {
    "meta_desc": {
        "en": "Bidirectional Minecraft whitelist mirror with QQ notifications",
        "zh": "服务器白名单双向同步 + QQ 群通知",
    },
    "meta_usage": {
        "en": "Configure MC_WL_SERVERS in .env",
        "zh": "在 .env 中设置 MC_WL_SERVERS",
    },
    "service_desc": {
        "en": "MC whitelist mirror (bidirectional sync)",
        "zh": "MC 白名单同步（双向）",
    },
    "wl_added": {
        "en": "⬜ {name} was added to the whitelist on {server}.",
        "zh": "⬜ {name} 已被添加到 {server} 白名单。",
    },
    "wl_removed": {
        "en": "⬜ {name} was removed from the whitelist on {server}.",
        "zh": "⬜ {name} 已被从 {server} 白名单移除。",
    },
    "cmd_no_permission": {
        "en": "You don't have permission to use this command.",
        "zh": "你没有权限使用这个命令。",
    },
    "cmd_not_connected": {
        "en": "No MC server connections available. Please try again later.",
        "zh": "当前没有可用的 MC 服务器连接，请稍后再试。",
    },
    "cmd_add_ok": {
        "en": "✅ {name} has been added to the whitelist on all servers.",
        "zh": "✅ {name} 已被添加到所有服务器的白名单。",
    },
    "cmd_remove_ok": {
        "en": "✅ {name} has been removed from the whitelist on all servers.",
        "zh": "✅ {name} 已被从所有服务器的白名单移除。",
    },
    "cmd_add_failed": {
        "en": "❌ Failed to add {name} to some servers: {failed}",
        "zh": "❌ 未能将 {name} 添加到以下服务器：{failed}",
    },
    "cmd_remove_failed": {
        "en": "❌ Failed to remove {name} from some servers: {failed}",
        "zh": "❌ 未能将 {name} 从以下服务器移除：{failed}",
    },
    "cmd_invalid_name": {
        "en": "❌ Invalid player name {name!r}. Names must be 1-16 characters of letters, digits, or underscores.",
        "zh": "❌ 玩家名 {name!r} 无效。名称须为 1-16 位的字母、数字或下划线。",
    },
}

__plugin_meta__ = PluginMetadata(
    name="mc_whitelist_sync",
    description=t(_S["meta_desc"]),
    usage=t(_S["meta_usage"]),
)

register("mc_whitelist_sync", t(_S["service_desc"]))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class _Config(BaseModel):
    mc_wl_servers: list[dict] = []
    mc_wl_add_permission: Literal["all", "group_admin", "superuser"] = "all"
    mc_wl_sync_on_start: bool = True
    mc_wl_reconnect_interval: int = 10
    mc_wl_notify_groups: list[int] = []
    mc_wl_insecure_ssl: bool = False


_cfg = get_plugin_config(_Config)

# Extract URL list and per-server auth info from the config
_server_urls: list[str] = []
_server_headers: dict[str, dict[str, str]] = {}   # url → headers dict
_server_labels: dict[str, str] = {}               # url → display label for QQ
_server_insecure: set[str] = set()                # urls using self-signed TLS

for _entry in _cfg.mc_wl_servers:
    _url = _entry.get("url", "")
    if not _url:
        continue
    _server_urls.append(_url)
    _token = _entry.get("token", "")
    if _token:
        _server_headers[_url] = {"Authorization": f"Bearer {_token}"}
    _label = _entry.get("label", "")
    if _label:
        _server_labels[_url] = _label
    # Skip TLS certificate verification per server (or globally) for
    # self-signed ``wss://`` endpoints.
    if _entry.get("insecure", _cfg.mc_wl_insecure_ssl):
        _server_insecure.add(_url)

# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------

_plugin_logger = logger.bind(name="mc_whitelist_sync")
_session: aiohttp.ClientSession | None = None

# Shared monotonic JSON-RPC request-id counter
_rid = 0
_rid_lock = asyncio.Lock()

# Per-URL live connection: {url: {"ws": ..., "pending": {rid: Future}}}
_conns: dict[str, dict[str, Any]] = {}
_conns_lock = asyncio.Lock()

# Queue: notifications from every listener → dispatcher
# Items: (method: str, params: list[dict], from_url: str)
_dispatch_queue: asyncio.Queue[tuple[str, list[dict], str]] = asyncio.Queue()

# Echo-loop prevention: (name, target_url) → expiry timestamp for every
# add/remove the bot itself performed.  When the target MC server echoes the
# change back as a notification, the dispatcher finds the entry here and skips
# the sync — terminating the loop with zero extra network calls.
#
# Entries carry a TTL: if the echo is lost (e.g. the connection dropped right
# after a successful write, or the server is idempotent and does not broadcast
# for an already-present player), the entry expires and a later *legitimate*
# change on that server is no longer misidentified as an echo.
_synced: dict[tuple[str, str], float] = {}
_SYNCED_TTL = 60.0  # seconds before a stale echo-suppression entry is ignored

# Track background tasks for clean shutdown (matching ntfy.py pattern).
_tasks: list[asyncio.Task] = []

driver = get_driver()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_label(url: str) -> str:
    """``wss://10.0.0.2:8080/ws`` → ``10.0.0.2``"""
    s = url.rstrip("/").rsplit("/", 1)[-1]
    s = s.split("@", 1)[-1]
    s = s.replace("wss://", "").replace("ws://", "")
    return s.split(":")[0] if ":" in s else s


def _display_label(url: str) -> str:
    """Return a safe label for QQ-facing messages.

    Uses the configured ``label`` field if present, otherwise falls back
    to a generic ``Server N`` based on the server's index.
    """
    if url in _server_labels:
        return _server_labels[url]
    try:
        idx = _server_urls.index(url)
        return f"Server {idx + 1}"
    except ValueError:
        return "Unknown"


def _get_bot():
    bots = driver.bots
    return next(iter(bots.values()), None) if bots else None


# Minecraft usernames are at most 16 characters of letters, digits and
# underscores.  The minimum length is kept at 1 (rather than 3) so short
# offline-mode names still work, while control characters, whitespace and
# other unusable input are rejected.
_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")


def _is_valid_name(name: str) -> bool:
    """Return True when *name* looks like a valid Minecraft player name."""
    return bool(_NAME_RE.match(name))


# ---------------------------------------------------------------------------
# JSON-RPC call — reuses the persistent WS connection
# ---------------------------------------------------------------------------


async def _rpc(url: str, method: str, params: list) -> Optional[list[dict]]:
    """Send a JSON-RPC request over the persistent connection to *url* and
    wait for the matching response.  Returns ``result`` or ``None``.
    """
    global _rid

    future: asyncio.Future = asyncio.get_running_loop().create_future()

    async with _rid_lock:
        _rid += 1
        rid = _rid

    # Hold _conns_lock while retrieving conn AND writing to its pending
    # dict so that a concurrent reconnect cannot replace _conns[url]
    # between the lookup and the write.
    async with _conns_lock:
        conn = _conns.get(url)
        if conn is None or conn["ws"].closed:
            _plugin_logger.warning(
                f"[mc_whitelist_sync] _rpc({method}): not connected to "
                f"{_short_label(url)}"
            )
            return None
        conn["pending"][rid] = future
        write_lock = conn["wlock"]

    payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}

    try:
        # Serialize writes per connection.  Several tasks may call _rpc on the
        # same URL concurrently (dispatcher, reconciliation, QQ command
        # handlers) and aiohttp does not guarantee safe concurrent send_* on a
        # single WebSocket, so each connection gets its own asyncio.Lock.
        # Only the send is serialized — responses still match by request id,
        # so a slow RPC cannot head-of-line block other requests.
        async with write_lock:
            await conn["ws"].send_json(payload)
        return await asyncio.wait_for(future, timeout=15)
    except asyncio.TimeoutError:
        _plugin_logger.error(
            f"[mc_whitelist_sync] _rpc({method}) to {_short_label(url)} "
            "timed out"
        )
        return None
    except Exception as exc:
        _plugin_logger.error(
            f"[mc_whitelist_sync] _rpc({method}) to {_short_label(url)} "
            f"failed: {exc}"
        )
        return None
    finally:
        conn["pending"].pop(rid, None)


# ---------------------------------------------------------------------------
# Sync actions — replay an add/remove to every OTHER server
# ---------------------------------------------------------------------------


async def _sync_add(name: str, from_url: str) -> set[str]:
    """Add *name* to every server except ``from_url``.

    Returns the set of server URLs the add actually succeeded on, so the
    caller can mark exactly those (and only those) as ``_synced`` to suppress
    their echo notifications.  Failed servers stay unmarked so a future
    legitimate change on them is not incorrectly discarded.
    """
    succeeded: set[str] = set()
    for url in _server_urls:
        if url == from_url:
            continue
        _plugin_logger.info(
            f"[mc_whitelist_sync] → add {name!r} to {_short_label(url)}"
        )
        if await _rpc(url, "allowlist/add", [[{"name": name}]]) is not None:
            succeeded.add(url)
    return succeeded


async def _sync_remove(name: str, from_url: str) -> set[str]:
    """Remove *name* from every server except ``from_url``.

    Same contract as ``_sync_add`` regarding the returned success set.
    """
    succeeded: set[str] = set()
    for url in _server_urls:
        if url == from_url:
            continue
        _plugin_logger.info(
            f"[mc_whitelist_sync] → remove {name!r} from {_short_label(url)}"
        )
        if await _rpc(url, "allowlist/remove", [[{"name": name}]]) is not None:
            succeeded.add(url)
    return succeeded


async def _add_to_all(name: str) -> dict[str, bool]:
    """Add *name* to every connected server.  Returns ``{url: True/False}``
    indicating success or failure per server.
    """
    results: dict[str, bool] = {}
    for url in _server_urls:
        result = await _rpc(url, "allowlist/add", [[{"name": name}]])
        results[url] = result is not None
    return results


async def _remove_from_all(name: str) -> dict[str, bool]:
    """Remove *name* from every connected server."""
    results: dict[str, bool] = {}
    for url in _server_urls:
        result = await _rpc(url, "allowlist/remove", [[{"name": name}]])
        results[url] = result is not None
    return results



# ---------------------------------------------------------------------------
# QQ group notification
# ---------------------------------------------------------------------------


async def _notify_qq(action: str, name: str, from_url: str) -> None:
    """Send a whitelist change notice to all configured QQ groups."""
    if not _cfg.mc_wl_notify_groups:
        return

    bot = _get_bot()
    if not bot:
        _plugin_logger.warning(
            "[mc_whitelist_sync] Cannot send QQ notification: no bot connected."
        )
        return

    key = "wl_added" if action == "added" else "wl_removed"
    msg = t(_S[key], name=name, server=_display_label(from_url))

    for gid in _cfg.mc_wl_notify_groups:
        try:
            await bot.send_group_msg(group_id=gid, message=msg)
            await asyncio.sleep(0.5)
        except Exception as exc:
            _plugin_logger.error(
                f"[mc_whitelist_sync] Failed to notify group {gid}: {exc}"
            )


# ---------------------------------------------------------------------------
# Persistent server listener — one per MC server, bidirectional
# ---------------------------------------------------------------------------


async def _server_listener(url: str) -> None:
    """Maintain a single persistent WebSocket to *url*.

    Bidirectional — the same connection is used for:

    * **receiving** notifications → pushed to ``_dispatch_queue``.
    * **sending** RPC requests issued by ``_rpc``; responses are matched
      via ``pending[rid]``.

    Exponential backoff on disconnect (max 60 s), heartbeat every 30 s.
    """
    label = _short_label(url)
    delay = _cfg.mc_wl_reconnect_interval if _cfg.mc_wl_reconnect_interval > 0 else 10
    assert _session is not None  # set by _startup before this task runs

    while True:
        try:
            async with _session.ws_connect(
                url,
                headers=_server_headers.get(url, {}),
                ssl=(False if url in _server_insecure else True),
                timeout=aiohttp.ClientTimeout(
                    connect=30,
                    sock_connect=30,
                    sock_read=None,   # no per-read deadline; heartbeat covers liveness
                ),
                heartbeat=30,
            ) as ws:
                pending: dict[int, asyncio.Future] = {}
                async with _conns_lock:
                    _conns[url] = {
                        "ws": ws,
                        "pending": pending,
                        "wlock": asyncio.Lock(),
                    }

                _plugin_logger.info(
                    f"[mc_whitelist_sync] Connected to {label}"
                )
                delay = (
                    _cfg.mc_wl_reconnect_interval
                    if _cfg.mc_wl_reconnect_interval > 0
                    else 10
                )

                try:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except (json.JSONDecodeError, ValueError) as exc:
                                _plugin_logger.warning(
                                    f"[mc_whitelist_sync] Non-JSON message from "
                                    f"{label} ignored: {exc}"
                                )
                                continue

                            if "id" in data:
                                # ── RPC response ──────────────────────────
                                rid = data["id"]
                                fut = pending.pop(rid, None)
                                if fut and not fut.done():
                                    if "result" in data:
                                        fut.set_result(data["result"])
                                    elif "error" in data:
                                        _plugin_logger.error(
                                            f"[mc_whitelist_sync] RPC error from "
                                            f"{label}: {data['error']}"
                                        )
                                        fut.set_result(None)

                            elif "method" in data:
                                # ── Notification ──────────────────────────
                                _dispatch_queue.put_nowait((
                                    data["method"],
                                    data.get("params", []),
                                    url,
                                ))

                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            _plugin_logger.error(
                                f"[mc_whitelist_sync] WS error from {label}: "
                                f"{ws.exception()}"
                            )
                            break

                finally:
                    # ── connection lost — clean up ───────────────────────
                    # Resolve any in-flight RPC futures fast (so _rpc callers
                    # don't stall for the full timeout) and deregister, on
                    # every exit path (clean close, error, or cancellation).
                    for fut in pending.values():
                        if not fut.done():
                            fut.set_result(None)
                    async with _conns_lock:
                        _conns.pop(url, None)

                _plugin_logger.warning(
                    f"[mc_whitelist_sync] {label} closed; "
                    f"reconnecting in {delay}s ..."
                )

        except asyncio.CancelledError:
            _plugin_logger.info(
                f"[mc_whitelist_sync] Listener for {label} cancelled."
            )
            async with _conns_lock:
                _conns.pop(url, None)
            return

        except Exception as exc:
            _plugin_logger.error(
                f"[mc_whitelist_sync] {label} error: {exc}; "
                f"retrying in {delay}s ..."
            )
            async with _conns_lock:
                _conns.pop(url, None)

        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


# ---------------------------------------------------------------------------
# Dispatcher — drain Queue → echo-检查 → sync + notify
# ---------------------------------------------------------------------------


async def _dispatcher() -> None:
    """Drain ``_dispatch_queue`` serially.

    On every notification, first checks ``_synced`` — if this name+server
    pair was just written by us, it's an echo, skip.  Otherwise, sync to
    all other servers and notify QQ.

    All business logic lives here:
    ``allowlist/added`` → ``_sync_add`` + ``_notify_qq``
    ``allowlist/removed`` → ``_sync_remove`` + ``_notify_qq``
    """
    _last_purge = 0.0
    while True:
        try:
            # Opportunistically drop expired echo-suppression entries so they
            # don't accumulate when echoes are never received.
            now = time.monotonic()
            if now - _last_purge >= 30.0:
                _last_purge = now
                stale = _purge_synced()
                if stale:
                    _plugin_logger.debug(
                        f"[mc_whitelist_sync] purged {stale} stale echo "
                        "suppression entry/ies"
                    )

            method, params, from_url = await _dispatch_queue.get()
            try:
                for entry in params:
                    name = entry.get("name", "")
                    if not name:
                        continue

                    # ── echo-loop guard ───────────────────────────────────
                    # If the bot itself wrote this name to this server within
                    # the TTL window, the resulting broadcast is an echo — skip
                    # it.  Expired/missing entries are treated as real changes.
                    if _consume_synced(name, from_url):
                        _plugin_logger.debug(
                            f"[mc_whitelist_sync] echo skipped: "
                            f"{name!r} on {_short_label(from_url)}"
                        )
                        continue

                    if method == "minecraft:notification/allowlist/added":
                        _plugin_logger.info(
                            f"[mc_whitelist_sync] ← added {name!r} "
                            f"on {_short_label(from_url)}"
                        )
                        # Perform sync, then mark only the servers that
                        # actually received the add so their echoes are
                        # ignored; failed servers stay unmarked.
                        _mark_synced(name, await _sync_add(name, from_url))
                        await _notify_qq("added", name, from_url)

                    elif method == "minecraft:notification/allowlist/removed":
                        _plugin_logger.info(
                            f"[mc_whitelist_sync] ← removed {name!r} "
                            f"on {_short_label(from_url)}"
                        )
                        _mark_synced(name, await _sync_remove(name, from_url))
                        await _notify_qq("removed", name, from_url)

            finally:
                _dispatch_queue.task_done()

        except asyncio.CancelledError:
            _plugin_logger.info("[mc_whitelist_sync] Dispatcher cancelled.")
            return
        except Exception as exc:
            _plugin_logger.error(
                f"[mc_whitelist_sync] Dispatcher error: {exc}"
            )


def _mark_synced_entry(name: str, url: str) -> None:
    """Mark a single (name, url) pair as bot-initiated, with a TTL."""
    _synced[(name, url)] = time.monotonic() + _SYNCED_TTL


def _mark_synced(name: str, urls: set[str]) -> None:
    """Record that *name* was written to each of *urls* by the bot itself.

    The echo notification from each of these target servers will then be
    ignored, terminating the sync loop.  Only actually-updated servers are
    marked, so a later legitimate change on an untouched server is preserved.
    """
    for url in urls:
        _mark_synced_entry(name, url)


def _consume_synced(name: str, url: str) -> bool:
    """Pop the echo-suppression entry for (name, url); return True if it is
    still within its TTL (i.e. this notification is a live echo)."""
    expiry = _synced.pop((name, url), None)
    return expiry is not None and expiry >= time.monotonic()


def _purge_synced() -> int:
    """Drop expired echo-suppression entries; return how many were removed."""
    now = time.monotonic()
    stale = [key for key, expiry in _synced.items() if expiry < now]
    for key in stale:
        _synced.pop(key, None)
    return len(stale)


# ---------------------------------------------------------------------------
# Startup reconciliation
# ---------------------------------------------------------------------------


async def _reconcile_when_ready(
    min_conns: int = 2,
    max_wait: float = 30.0,
    poll_interval: float = 1.0,
) -> None:
    """Wait until at least *min_conns* servers are connected, then reconcile.

    A fixed startup delay risks running before slow servers connect (skipping
    them).  Instead, poll the live connection table until enough servers are
    up, bounded by *max_wait* seconds so a permanently-down server cannot
    stall startup indefinitely.  ``_full_reconcile`` itself aborts gracefully
    if fewer than two servers are ultimately reachable.
    """
    elapsed = 0.0
    while elapsed < max_wait:
        async with _conns_lock:
            connected = [
                u for u in _server_urls
                if u in _conns and not _conns[u]["ws"].closed
            ]
        if len(connected) >= min_conns or len(connected) >= len(_server_urls):
            break
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    await _full_reconcile()


async def _full_reconcile() -> None:
    """Fetch every server's whitelist, compute the union, add missing
    players.  Only adds — never removes.
    """
    try:
        await _full_reconcile_impl()
    except Exception as exc:
        _plugin_logger.error(
            f"[mc_whitelist_sync] Full reconciliation failed: {exc}"
        )


async def _full_reconcile_impl() -> None:
    """Inner implementation of full reconciliation.  Separated so the outer
    wrapper can catch and log any unexpected errors without crashing the
    background task silently.
    """
    _plugin_logger.info("[mc_whitelist_sync] Full reconciliation starting ...")

    all_lists: dict[str, list[dict]] = {}
    for url in _server_urls:
        result = await _rpc(url, "allowlist", [])
        if result is not None:
            all_lists[url] = result
            _plugin_logger.info(
                f"[mc_whitelist_sync]   {_short_label(url)}: "
                f"{len(result)} player(s)"
            )

    if len(all_lists) < 2:
        _plugin_logger.warning(
            "[mc_whitelist_sync] Reconciliation aborted: fewer than 2 "
            "servers reachable."
        )
        return

    union: set[str] = set()
    for url, entries in all_lists.items():
        try:
            union.update(e["name"] for e in entries)
        except (KeyError, TypeError) as exc:
            _plugin_logger.error(
                f"[mc_whitelist_sync] Unexpected allowlist response "
                f"from {_short_label(url)}: {exc}.  Skipping this server."
            )
            continue

    _plugin_logger.info(
        f"[mc_whitelist_sync] Union: {len(union)} unique player(s)"
    )

    for url, entries in all_lists.items():
        try:
            server_names = {e["name"] for e in entries}
        except (KeyError, TypeError) as exc:
            _plugin_logger.error(
                f"[mc_whitelist_sync] Unexpected allowlist response "
                f"from {_short_label(url)}: {exc}.  Skipping this server."
            )
            continue
        missing: set[str] = union - server_names
        if not missing:
            continue
        _plugin_logger.info(
            f"[mc_whitelist_sync]   {_short_label(url)} missing "
            f"{len(missing)}: {sorted(missing)}"
        )
        for name in sorted(missing):
            if await _rpc(url, "allowlist/add", [[{"name": name}]]) is not None:
                # Suppress the echo only when the add actually succeeded.
                _mark_synced_entry(name, url)

    _plugin_logger.info("[mc_whitelist_sync] Full reconciliation complete.")


# ---------------------------------------------------------------------------
# QQ commands — /whitelist add|remove <name>
# ---------------------------------------------------------------------------

_wl_cmd = on_command(
    "whitelist",
    aliases={"白名单"},
    rule=online("mc_whitelist_sync") & not_muted() & to_me(),
    priority=5,
    block=True,
)


@_wl_cmd.handle()
async def _handle_whitelist(
    bot: Bot,
    event: MessageEvent,
    cmd_arg = CommandArg(),
) -> None:
    """Handle ``/whitelist add <name>`` and ``/whitelist remove <name>``.

    * ``add`` — available in groups and private chat with permission check.
    * ``remove`` — superuser only, private chat only.
    """
    # ── connection check ────────────────────────────────────────────────
    async with _conns_lock:
        connected_urls = [
            u for u in _server_urls
            if u in _conns and not _conns[u]["ws"].closed
        ]
    if not connected_urls:
        await _wl_cmd.finish(
            MessageSegment.reply(event.message_id)
            + t(_S["cmd_not_connected"])
        )

    # ── parse sub-command ───────────────────────────────────────────────
    tokens = cmd_arg.extract_plain_text().strip().split(None, 1)
    if not tokens:
        return

    sub = tokens[0].lower()
    name = tokens[1].strip() if len(tokens) > 1 else ""

    if sub == "add":
        await _handle_add(bot, event, name)

    elif sub == "remove":
        await _handle_remove(bot, event, name)

    # unknown sub-command → silent


async def _handle_add(bot: Bot, event: MessageEvent, name: str) -> None:
    """Add a player to all servers.

    Group-chat permission is governed by ``MC_WL_ADD_PERMISSION``:
      ``"all"``          – anyone
      ``"group_admin"``  – group admin/owner or superuser  *(default)*
      ``"superuser"``    – superuser only
    Private chat always requires superuser.
    """
    is_superuser = await SUPERUSER(bot, event)

    if isinstance(event, PrivateMessageEvent):
        # Private-chat add requires superuser
        if not is_superuser:
            await _wl_cmd.finish(
                MessageSegment.reply(event.message_id)
                + t(_S["cmd_no_permission"])
            )
    elif isinstance(event, GroupMessageEvent):
        perm = _cfg.mc_wl_add_permission
        allowed = True  # "all"
        if perm == "group_admin":
            allowed = is_superuser or await GROUP_ADMIN(bot, event) or await GROUP_OWNER(bot, event)
        elif perm == "superuser":
            allowed = is_superuser
        if not allowed:
            await _wl_cmd.finish(
                MessageSegment.reply(event.message_id)
                + t(_S["cmd_no_permission"])
            )

    if not name:
        return
    if not _is_valid_name(name):
        await _wl_cmd.finish(
            MessageSegment.reply(event.message_id)
            + t(_S["cmd_invalid_name"], name=name)
        )

    results = await _add_to_all(name)

    # Only mark successful servers — failed ones still need sync on
    # future manual additions.
    for url, ok in results.items():
        if ok:
            _mark_synced_entry(name, url)

    failed = [_display_label(u) for u, ok in results.items() if not ok]
    if failed:
        await _wl_cmd.finish(
            MessageSegment.reply(event.message_id)
            + t(_S["cmd_add_failed"], name=name, failed=", ".join(failed))
        )

    _plugin_logger.info(
        f"[mc_whitelist_sync] QQ command: added {name!r} to all servers"
    )
    await _wl_cmd.finish(
        MessageSegment.reply(event.message_id)
        + t(_S["cmd_add_ok"], name=name)
    )


async def _handle_remove(bot: Bot, event: MessageEvent, name: str) -> None:
    """Remove a player from all servers.  Superuser only, private chat only."""
    # Remove is not available in groups at all — silently ignore
    if isinstance(event, GroupMessageEvent):
        return

    # Private-chat superuser check
    if not await SUPERUSER(bot, event):
        await _wl_cmd.finish(
            MessageSegment.reply(event.message_id)
            + t(_S["cmd_no_permission"])
        )

    if not name:
        return
    if not _is_valid_name(name):
        await _wl_cmd.finish(
            MessageSegment.reply(event.message_id)
            + t(_S["cmd_invalid_name"], name=name)
        )

    results = await _remove_from_all(name)

    # Only mark successful servers — failed ones still need sync on
    # future manual removals.
    for url, ok in results.items():
        if ok:
            _mark_synced_entry(name, url)

    failed = [_display_label(u) for u, ok in results.items() if not ok]
    if failed:
        await _wl_cmd.finish(
            MessageSegment.reply(event.message_id)
            + t(_S["cmd_remove_failed"], name=name, failed=", ".join(failed))
        )

    _plugin_logger.info(
        f"[mc_whitelist_sync] QQ command: removed {name!r} from all servers"
    )
    await _wl_cmd.finish(
        MessageSegment.reply(event.message_id)
        + t(_S["cmd_remove_ok"], name=name)
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@driver.on_startup
async def _startup() -> None:
    global _session

    if not _server_urls:
        _plugin_logger.info(
            "[mc_whitelist_sync] MC_WL_SERVERS is empty; sync disabled."
        )
        return

    _session = aiohttp.ClientSession()

    for url in _server_urls:
        _tasks.append(
            asyncio.create_task(
                _server_listener(url),
                name=f"mc-wl-listener-{_short_label(url)}",
            )
        )

    _tasks.append(asyncio.create_task(_dispatcher(), name="mc-wl-dispatcher"))

    _plugin_logger.info(
        f"[mc_whitelist_sync] Listening on {len(_server_urls)} "
        f"server(s): {_server_urls}"
    )

    if _cfg.mc_wl_notify_groups:
        _plugin_logger.info(
            f"[mc_whitelist_sync] QQ notifications → groups: "
            f"{_cfg.mc_wl_notify_groups}"
        )

    if _cfg.mc_wl_sync_on_start:
        _tasks.append(
            asyncio.create_task(_reconcile_when_ready(), name="mc-wl-full-sync")
        )


@driver.on_shutdown
async def _shutdown() -> None:
    for task in _tasks:
        task.cancel()
    if _session:
        await _session.close()
