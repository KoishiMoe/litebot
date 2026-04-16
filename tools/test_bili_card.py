#!/usr/bin/env python3
"""
CLI test for the Bilibili preview card generator.

Loads card.py directly via importlib, bypassing the plugin's __init__.py
which requires a running NoneBot instance.

Run from the repository root::

    python tools/test_bili_card.py [options]
    python tools/test_bili_card.py --help
"""

import argparse
import asyncio
import importlib.util
import os
import sys
from typing import Optional

# Ensure the repository root is on sys.path so that builtin.* imports resolve.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Load card.py directly from its file path, bypassing
# builtin/plugins/b23extract/__init__.py (which requires NoneBot).
_spec = importlib.util.spec_from_file_location(
    "builtin.plugins.b23extract.card",
    os.path.join(_REPO_ROOT, "builtin", "plugins", "b23extract", "card.py"),
)
_card_module = importlib.util.module_from_spec(_spec)
sys.modules["builtin.plugins.b23extract.card"] = _card_module
_spec.loader.exec_module(_card_module)  # type: ignore[union-attr]
build_bili_card = _card_module.build_bili_card


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a Bilibili preview card PNG for quick local testing.",
    )
    parser.add_argument(
        "-o", "--output",
        default=os.path.join(os.getcwd(), "tmp", "bili_card_test.png"),
        help="Output PNG path (default: <cwd>/tmp/bili_card_test.png)",
    )
    parser.add_argument("--title", default="测试视频标题 – Test Video Title (BV1xx411c7mD)")
    parser.add_argument("--url", default="https://www.bilibili.com/video/av116283555319003/")
    parser.add_argument("--author", default="测试UP主 / TestUploader")
    parser.add_argument("--author-avatar", default="https://github.com/identicons/litebot.png", metavar="URL",
                        help="Author avatar URL (optional)")
    parser.add_argument("--cover", default="https://i1.hdslb.com/bfs/archive/bba8967a86cbdad4711916e1e2466578f24ad4dd.jpg", metavar="URL",
                        help="Cover image URL (optional)")
    parser.add_argument("--category", default="生活")
    parser.add_argument(
        "--tags",
        default="vlog,这是一个超————————————————————————————————————————————————————————————————————————————————————长的标签，"
                "日常,生活记录,测试,Python,二次元,动漫,游戏,这是另一个超————————————————————————————长的标签,科技,美食,旅行,音乐",
        help="Comma-separated tag list",
    )
    parser.add_argument(
        "--description",
        default=(
            "这是一段示例描述文字，用于测试图片生成效果。\n"
            "This is a sample description for testing the card generator.\n"
            "支持中文、日本語、한국어 等多语言内容。\n"
            "Emoji 测试：🌞☀️1️⃣👋🏽🇺🇸👩‍🚀👩🏽‍💻👨‍👩‍👧‍👦🏴󠁧󠁢󠁳󠁣󠁴󠁿\n"
            "Large description test: " + "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 5
        ),
    )
    parser.add_argument("--font", default="", help="Path to a CJK-capable font file")
    parser.add_argument(
        "--font-weight", default="medium",
        choices=["regular", "medium", "bold"],
        help="Requested font weight for TTC/OTC collections",
    )
    parser.add_argument("--view", type=int, default=None, help="Video view count")
    parser.add_argument("--like", type=int, default=None, help="Video like count")
    parser.add_argument("--coin", type=int, default=None, help="Video coin count")
    parser.add_argument("--favorite", type=int, default=None, help="Video favorite count")
    parser.add_argument("--online", type=int, default=None, help="Live online count")
    parser.add_argument(
        "--post-time",
        default="1915459199",
        help="Post time: unix timestamp (seconds/ms) or datetime string",
    )
    args = parser.parse_args()

    def _parse_post_time(raw: str) -> Optional[int | str]:
        value = (raw or "").strip()
        if not value:
            return None
        if value.isdigit():
            return int(value)
        return value

    stats = {
        k: v
        for k, v in {
            "view": args.view,
            "like": args.like,
            "coin": args.coin,
            "favorite": args.favorite,
            "online": args.online,
        }.items()
        if v is not None
    }

    async def _run() -> None:
        out = args.output
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        png = await build_bili_card(
            title=args.title,
            author=args.author,
            author_avatar_url=getattr(args, "author_avatar", None),
            cover_url=args.cover,
            category=args.category,
            tags=[t.strip() for t in args.tags.split(",") if t.strip()],
            description=args.description,
            url=args.url,
            stats=stats or None,
            post_time=_parse_post_time(args.post_time),
            font_path=args.font,
            font_weight=args.font_weight,
        )
        with open(out, "wb") as f:
            f.write(png)
        print(f"Saved {len(png):,} bytes → {out}")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
