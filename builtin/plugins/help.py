"""
help.py – /help command to browse plugin documentation.

Lists all loaded plugins that declare __plugin_meta__, or shows the
detailed usage string for a specific plugin by name.

Usage:
    /help              – list all plugins with their descriptions
    /help <name>       – show full usage info for plugin <name>
"""

import nonebot
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from ..utils.i18n import t

__plugin_meta__ = PluginMetadata(
    name="help",
    description=t({"en": "Show available commands and plugin documentation", "zh": "查看可用命令与插件文档"}),
    usage=t({"en": "/help - list all plugins with descriptions\n/help <name> - show detailed usage for a plugin", "zh": "/help - 查看插件列表与简介\n/help <name> - 查看指定插件详细用法"}),
)

_S = {
    "list_header":  {"en": "📋 Plugins:",                             "zh": "📋 插件列表："},
    "list_footer":  {"en": "Use /help <name> for detailed usage.",    "zh": "使用 /help <名称> 查看详细用法。"},
    "no_docs":      {"en": "No plugin documentation available.",      "zh": "暂无插件文档。"},
    "not_found":    {"en": "Plugin '{name}' not found.\nAvailable: {available}", "zh": "未找到插件 '{name}'。\n可用插件：{available}"},
    "usage_label":  {"en": "Usage:",                                  "zh": "用法："},
    "none":         {"en": "(none)",                                  "zh": "（无）"},
    "detail_title": {"en": "📖 {name}",                              "zh": "📖 {name}"},
}

_help = on_command("help", aliases={"帮助"}, priority=1, block=True)


@_help.handle()
async def _handle_help(event: MessageEvent, arg: Message = CommandArg()) -> None:
    name = arg.extract_plain_text().strip()

    plugins = sorted(
        nonebot.get_loaded_plugins(),
        key=lambda p: (p.metadata.name if p.metadata else p.name).lower(),
    )
    meta_plugins = [p for p in plugins if p.metadata]

    if name:
        # Look up a specific plugin by metadata name or module name
        target = next(
            (
                p
                for p in meta_plugins
                if p.metadata.name.lower() == name.lower() or p.name.lower() == name.lower()
            ),
            None,
        )
        if target is None:
            available = ", ".join(p.metadata.name for p in meta_plugins) or t(_S["none"])
            await _help.finish(MessageSegment.reply(event.message_id) + t(_S["not_found"], name=name, available=available))

        meta = target.metadata
        lines = [t(_S["detail_title"], name=meta.name), "", meta.description]
        if meta.usage:
            lines += ["", t(_S["usage_label"]), meta.usage]
        await _help.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
    else:
        if not meta_plugins:
            await _help.finish(MessageSegment.reply(event.message_id) + t(_S["no_docs"]))

        lines = [t(_S["list_header"]), ""]
        for p in meta_plugins:
            lines.append(f"  • {p.metadata.name} – {p.metadata.description}")
        lines += ["", t(_S["list_footer"])]
        await _help.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
