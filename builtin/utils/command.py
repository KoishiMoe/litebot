"""
command.py – Prefix-optional command helpers.

Some commands are specific enough that requiring the COMMAND_START prefix is
unnecessary friction.  The helpers here build matchers that accept both the
bare command (e.g. ``mcping``) and any configured COMMAND_START prefix
(e.g. ``/mcping``), while remaining word-boundary-aware.
"""

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.rule import Rule

# Characters that may follow a command word and still count as a boundary.
_CMD_BOUNDARY = " \t\n\r"


def cmd_rule(*cmds: str) -> Rule:
    """Return a :class:`~nonebot.rule.Rule` that matches messages starting
    with any of *cmds*.

    Matching is word-boundary-aware: the command word must be followed by
    whitespace or end-of-message.  Both the bare command and any
    ``COMMAND_START``-prefixed variant are accepted.
    """
    cmds_lower = tuple(c.lower() for c in cmds)

    async def _check(event: MessageEvent) -> bool:
        msg = str(event.message).strip().lower()
        prefixes: set[str] = set(get_driver().config.command_start)
        prefixes.add("")  # always allow bare (prefix-free) invocation
        for prefix in prefixes:
            for cmd in cmds_lower:
                full = prefix + cmd
                if msg.startswith(full):
                    rest = msg[len(full):]
                    if not rest or rest[0] in _CMD_BOUNDARY:
                        return True
        return False

    return Rule(_check)


def cmd_arg(event: MessageEvent, cmds: tuple[str, ...]) -> str:
    """Strip the command name (with optional prefix) from the message text.

    Returns the remainder of the message (the argument string), or ``""``
    if no matching command prefix is found.  Longer prefixes are tried first
    so that e.g. ``/`` does not shadow ``//``.
    """
    msg = str(event.message).strip()
    msg_lower = msg.lower()
    prefixes: set[str] = set(get_driver().config.command_start)
    prefixes.add("")
    for prefix in sorted(prefixes, key=len, reverse=True):
        for cmd in cmds:
            full = prefix + cmd
            if msg_lower.startswith(full.lower()):
                rest = msg[len(full):]
                if not rest or rest[0] in _CMD_BOUNDARY:
                    return rest.lstrip()
    return ""
