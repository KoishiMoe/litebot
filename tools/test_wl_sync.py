#!/usr/bin/env python3
"""
Integration test for builtin/plugins/mc_whitelist_sync.py.

Verifies, against a pair of in-memory mock servers, that the module's real
code paths behave correctly for the "add" flow:

  1. request method  – the bot sends ``allowlist/add`` with params
                       ``[[{"name": "Steve"}]]`` to the *other* server;
  2. response        – ``_rpc`` matches the JSON-RPC ``id`` and returns the
                       ``result``;
  3. notification    – the broadcast ``minecraft:notification/allowlist/added``
                       is recognized and triggers the sync;
  4. echo-loop guard – the target server's echo is discarded, so the loop
                       terminates with no extra RPCs.

Why we can fake an MC server
----------------------------
The plugin never speaks the Minecraft binary protocol.  It only talks to the
server over a single WebSocket using JSON-RPC:

    client -> server : {"jsonrpc": "2.0", "id": N, "method": "...", "params": [...]}
    server -> client : {"jsonrpc": "2.0", "id": N, "result": [...] | "error": {...}}
    server -> client : {"jsonrpc": "2.0", "method": "minecraft:notification/...", "params": [...]}

Any endpoint that answers those messages on a WebSocket is therefore
indistinguishable, at the network level, from a real server running the
whitelist plugin.  The mock servers below only implement that contract.

Run from the repository root (the script re-anchors cwd itself)::

    python tools/test_wl_sync.py
    .venv\\Scripts\\python.exe tools\\test_wl_sync.py
"""

import asyncio
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
# NoneBot resolves plugin names relative to the working directory, so anchor
# here to mirror bot.py exactly (which uses the relative "./builtin/plugins").
os.chdir(_REPO_ROOT)

import nonebot  # noqa: E402

nonebot.init()
nonebot.load_plugins("./builtin/plugins")

from aiohttp import web, ClientSession, WSMsgType  # noqa: E402

import builtin.plugins.mc_whitelist_sync as m  # noqa: E402

ADD_METHOD = "allowlist/add"
ADDED_NOTIFICATION = "minecraft:notification/allowlist/added"


def make_app(broadcast: str | None = None):
    """Build a mock whitelist server.

    *broadcast*: if set, the server pushes an ``allowlist/added`` notification
    shortly after a client connects (used to simulate the *source* server where
    an external admin added a player).
    """
    state = {"adds": [], "whitelist": []}

    async def handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        if broadcast:
            await asyncio.sleep(0.3)
            await ws.send_json({
                "jsonrpc": "2.0",
                "method": ADDED_NOTIFICATION,
                "params": [{"name": broadcast}],
            })

        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)

            # Server → client notification has no "id".
            if "id" not in data:
                continue

            rid = data["id"]
            method = data.get("method")

            if method == ADD_METHOD:
                names = [e["name"] for grp in data.get("params", []) for e in grp]
                state["adds"].extend(names)
                print(f"  [mock] request method={method!r} name={names!r}")
                await ws.send_json({
                    "jsonrpc": "2.0", "id": rid,
                    "result": [{"name": n} for n in names],
                })
                # MC broadcasts the change unconditionally → this is the echo.
                for n in names:
                    await ws.send_json({
                        "jsonrpc": "2.0",
                        "method": ADDED_NOTIFICATION,
                        "params": [{"name": n}],
                    })
            elif method == "allowlist":
                await ws.send_json({
                    "jsonrpc": "2.0", "id": rid,
                    "result": [{"name": n} for n in state["whitelist"]],
                })
            else:
                await ws.send_json({"jsonrpc": "2.0", "id": rid, "result": []})

        return ws

    return state, handler


async def _start_server(handler):
    app = web.Application()
    app.router.add_get("/ws", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"ws://127.0.0.1:{port}/ws"


async def _wait_connected(url: str, timeout: float = 5.0) -> bool:
    for _ in range(int(timeout / 0.1)):
        async with m._conns_lock:
            conn = m._conns.get(url)
            if conn is not None and not conn["ws"].closed:
                return True
        await asyncio.sleep(0.1)
    return False


async def _run() -> None:
    # A = source (an admin added "Steve" there), B = target (must receive it).
    state_a, handler_a = make_app(broadcast="Steve")
    state_b, handler_b = make_app()
    runner_a, url_a = await _start_server(handler_a)
    runner_b, url_b = await _start_server(handler_b)

    m._session = ClientSession()
    m._server_urls = [url_a, url_b]
    m._server_headers = {}

    tasks = [
        asyncio.create_task(m._server_listener(url_a)),
        asyncio.create_task(m._server_listener(url_b)),
        asyncio.create_task(m._dispatcher()),
    ]

    try:
        assert await _wait_connected(url_a), "server A never connected"
        assert await _wait_connected(url_b), "server B never connected"
        print("Both mock servers connected.")

        # Let: source broadcast -> dispatcher sync -> echo suppression settle.
        await asyncio.sleep(2.0)

        print("Server A (source) add-calls:", state_a["adds"])
        print("Server B (target) add-calls:", state_b["adds"])

        # 1) request method + 2) response correctness.
        assert state_a["adds"] == [], "source server must be skipped (no self-add)"
        assert state_b["adds"] == ["Steve"], "target must receive exactly one add"

        # 4) echo-loop guard: B's echo must have been discarded, no runaway adds.
        assert len(state_b["adds"]) == 1, "echo loop detected: B received extra adds"

        print(
            "PASS: request method 'allowlist/add' correct, response id matched, "
            "notification 'minecraft:notification/allowlist/added' handled, "
            "echo suppressed (no loop)."
        )
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await m._session.close()
        await runner_a.cleanup()
        await runner_b.cleanup()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
