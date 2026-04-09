# LiteBot

基于 [NoneBot2](https://nonebot.dev/) 和 OneBot v11 协议的轻量 QQ 机器人。

[English](README.en.md)

> [!WARNING]
> 本仓库中的几乎所有代码均由 AI 代理生成，可能包含错误、安全问题或纯粹的胡言乱语。
> 虽然我在合并前会手动审查和测试所有代码，但我无法保证其正确性或安全性。
> 请自行承担风险，并在运行前务必审查代码。

---

## 功能模块

| 插件 | 命令 | 说明 |
|---|---|---|
| **service** | `/svc` | 按群组 / 用户启用或禁用各功能 |
| **ping** | `/ping` | 检查机器人在线状态与事件投递延迟 |
| **help** | `/help` | 查看已加载插件的文档 |
| **mute** | `/mute` | 检查机器人在当前上下文是否被禁言，以避免浪费资源 |
| **withdraw** | `/withdraw` | 撤回机器人最近发送的消息 |
| **login_notice** | — | 机器人登录时通知超级用户 |
| **group_notice** | `/notice` | 自定义入群 / 退群通知消息 |
| **anti_miniapp** | — | 从小程序消息中提取链接 |
| **exception_report** | — | 将未处理的异常转发给指定超级用户 |
| **ntfy** | — | 将 [ntfy.sh](https://ntfy.sh) 推送通知转发至 QQ 群或私聊 |
| **drasl** | `/invite` | 生成 [Drasl](https://github.com/unmojang/drasl) Minecraft 验证服务器邀请链接 |
| **b23extract** | — | 解析哔哩哔哩链接并回复富信息卡片 |
| **mcping** | `/mcping` | Ping Minecraft 服务器并显示玩家 / 版本信息 |

---

## 环境要求

- Python ≥ 3.14
- [uv](https://github.com/astral-sh/uv)（推荐）或其他支持 PEP 517 的安装工具
- 一个运行中的 OneBot v11 实现

---

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/KoishiMoe/litebot.git
cd litebot

# 2. 使用 uv 安装依赖
uv sync

# 3. 复制并编辑配置文件
cp .env.example .env
$EDITOR .env          # 设置 SUPERUSERS、凭据等

# 4. 运行
uv run python bot.py
```

将 OneBot 实现的上报地址指向 `http://<主机>:8080`（默认端口，可通过 `.env` 中的 `PORT=` 修改）。

---

## 配置说明

所有配置项均位于 `.env`（从 `.env.example` 复制而来），常用选项：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HOST` / `PORT` | `0.0.0.0` / `8080` | HTTP 服务器地址 |
| `COMMAND_START` | `["/"]` | 命令前缀 |
| `SUPERUSERS` | `[]` | 拥有超级用户权限的 QQ 号 |
| `BOT_LANGUAGE` | `zh` | 界面语言（`en` 或 `zh`） |
| `DATA_DIR` | `data` | 插件持久化数据根目录 |
| `LOG_DIR` | `logs` | 日志文件目录 |
| `CARD_FONT` | *(自动)* | 图片卡片使用的 CJK 字体路径 |
| `BILIBILI_SESSDATA` / `BILIBILI_BILI_JCT` / `BILIBILI_BUVID3` | *(留空)* | 可选的 Bilibili 登录凭据 |
| `DRASL_SERVER` / `DRASL_TOKEN` | *(留空)* | Drasl 服务器地址和 API 令牌 |
| `NTFY_SERVER` / `NTFY_TOKEN` | `https://ntfy.sh` / *(留空)* | ntfy 服务器及认证令牌 |

完整配置项及说明请参阅 `.env.example`。

---

## 图片卡片

`b23extract` 和 `mcping` 插件可以生成富文本图片卡片，均依赖：

- **共享字体系统** — 自动从系统常见路径检测字体（推荐安装 `fonts-noto-cjk`）。可通过 `.env` 中的 `CARD_FONT=` 手动指定。
- **内置 Twemoji** — 卡片中的 Emoji 由内置的 [Twemoji v17.0.2](https://github.com/jdecked/twemoji) PNG 资源渲染，无需额外安装 Emoji 字体。

---

## 服务管理器

`service` 插件为所有其他插件提供细粒度的功能开关。

```
/svc                          查看当前上下文的功能状态
/svc on  <名称>               启用某个功能
/svc off <名称>               禁用某个功能
/svc on  <名称> @用户         为指定用户启用/禁用

# 超级用户（私聊中使用）：
/svc on|off <名称>                   全局开关
/svc on|off <名称> -g <群号>         针对指定群
/svc on|off <名称> -u <QQ号>         针对指定用户（所有群）
/svc on|off * [-g …] [-u …]          通配符：同时操作所有功能
```

---

## 自定义插件

将自定义插件放在 `custom/plugins/` 目录下（包目录或单文件均可），或在 `.env` 中列出：

```
CUSTOM_PLUGINS=["my_pypi_plugin", "another_plugin"]
```

---

## 许可证

LiteBot 源代码采用 **AGPLv3-or-later** 授权，详见 [LICENSE](LICENSE)。

内置 [Twemoji](https://github.com/jdecked/twemoji) 图形资源（`builtin/assets/twemoji/`）采用 **CC BY 4.0** 授权，详见 [`builtin/assets/twemoji/LICENSE-GRAPHICS`](builtin/assets/twemoji/LICENSE-GRAPHICS)。
