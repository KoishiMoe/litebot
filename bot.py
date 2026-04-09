#! /usr/bin/env python3
import json
import os
from os import getenv

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter
from dotenv import load_dotenv

# 初始化 NoneBot
load_dotenv()
nonebot.init()

# ── On-disk logging ────────────────────────────────────────────────────────
# Loguru file sink with configurable rotation and retention.
# Config keys (in .env):
#   LOG_DIR=logs            – directory for log files
#   LOG_ROTATION=00:00      – rotation schedule (time or size; loguru format)
#   LOG_RETENTION=30 days   – how long to keep old log files
#   LOG_LEVEL_FILE=INFO     – minimum log level written to disk
from loguru import logger as _logger  # same object as nonebot.log.logger

_log_dir = getenv("LOG_DIR", "logs")
_log_rotation = getenv("LOG_ROTATION", "00:00")
_log_retention = getenv("LOG_RETENTION", "30 days")
_log_level_file = getenv("LOG_LEVEL_FILE", "INFO")

os.makedirs(_log_dir, exist_ok=True)
_logger.add(
    os.path.join(_log_dir, "litebot_{time:YYYY-MM-DD}.log"),
    rotation=_log_rotation,
    retention=_log_retention,
    level=_log_level_file,
    encoding="utf-8",
    enqueue=True,
    compression="gz",
)
_logger.info(f"Logger initialized. Log files will be saved to '{_log_dir}' with rotation='{_log_rotation}', "
             f"retention='{_log_retention}', and minimum level='{_log_level_file}'.")
# ──────────────────────────────────────────────────────────────────────────

# 注册适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)

# 在这里加载插件
# nonebot.load_builtin_plugins("echo")  # 内置插件
nonebot.load_plugins("./builtin/plugins")
nonebot.load_plugins("./custom/plugins")

# find custom plugins in .env:CUSTOM_PLUGINS=["a", "b"]
custom_plugins = getenv("CUSTOM_PLUGINS", "[]")
try:
    custom_plugins = json.loads(custom_plugins)
except (json.JSONDecodeError, ValueError):
    custom_plugins = []
if isinstance(custom_plugins, list):
    for item in custom_plugins:
        if isinstance(item, str) and (plugin := item.strip()):
            nonebot.load_plugin(plugin)


if __name__ == "__main__":
    nonebot.run()