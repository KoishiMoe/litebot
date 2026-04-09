#!/usr/bin/env python3
"""
CLI test for the Minecraft server-status card generator.

Loads card.py directly via importlib, bypassing the plugin's __init__.py
which requires a running NoneBot instance.

Run from the repository root::

    python tools/test_mc_card.py [options]
    python tools/test_mc_card.py --help
"""

import argparse
import importlib.util
import os
import sys

# Ensure the repository root is on sys.path so that builtin.* imports resolve.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Load card.py directly from its file path, bypassing
# builtin/plugins/mcping/__init__.py (which requires NoneBot).
_spec = importlib.util.spec_from_file_location(
    "builtin.plugins.mcping.card",
    os.path.join(_REPO_ROOT, "builtin", "plugins", "mcping", "card.py"),
)
_card_module = importlib.util.module_from_spec(_spec)
sys.modules["builtin.plugins.mcping.card"] = _card_module
_spec.loader.exec_module(_card_module)  # type: ignore[union-attr]
build_mc_card = _card_module.build_mc_card


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a Minecraft server-status card PNG for local testing.",
    )
    parser.add_argument(
        "-o", "--output",
        default=os.path.join(os.getcwd(), "tmp", "mc_card_test.png"),
        help="Output PNG path (default: <cwd>/tmp/mc_card_test.png)",
    )
    parser.add_argument("--name",    default="mc.example.com")
    parser.add_argument("--motd",    default=(
        "§aA §bMinecraft §eServer\n"
        "§7§oItalic §lBold §nUnderline §mStrike §r§fNormal\n"
        "§kSuper Super Secret"
    ))
    parser.add_argument("--version", default="Python 3.14")
    parser.add_argument("--online",  type=int, default=5)
    parser.add_argument("--max",     type=int, default=100)
    parser.add_argument("--latency", type=float, default=42.0)
    parser.add_argument("--favicon", default="")
    parser.add_argument("--bedrock", action="store_true")
    parser.add_argument("--sample",  default="Steve,Alex,Notch,Herobrine,Dinnerbone",
                        help="Comma-separated player sample names")
    parser.add_argument("--font",        default="")
    parser.add_argument("--font-weight", default="medium",
                        choices=["regular", "medium", "bold"])
    args = parser.parse_args()

    try:
        from mcstatus.motd import Motd as _Motd
        motd_obj = _Motd.parse(args.motd, bedrock=args.bedrock)
        motd_parsed = motd_obj.parsed
        motd_plain = motd_obj.to_plain()
    except ImportError:
        motd_parsed = None
        motd_plain = args.motd

    out = args.output
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sample = [s.strip() for s in args.sample.split(",") if s.strip()]
    png = build_mc_card(
        display_name=args.name,
        motd_parsed=motd_parsed,
        motd_plain=motd_plain,
        version_str=args.version,
        players_online=args.online,
        players_max=args.max,
        latency=args.latency,
        favicon=args.favicon,
        is_bedrock=args.bedrock,
        player_sample=sample,
        font_path=args.font,
        font_weight=args.font_weight,
    )
    with open(out, "wb") as f:
        f.write(png)
    print(f"Saved to {out} ({len(png):,} bytes)")


if __name__ == "__main__":
    main()
