"""
mcping/formatter.py – Text and image response formatters.
"""

from typing import Optional

from ...utils.i18n import t
from .config import _cfg

_S = {
    "label_version": {"en": "Version", "zh": "版本"},
    "label_players": {"en": "Players", "zh": "在线人数"},
    "label_latency": {"en": "Latency", "zh": "延迟"},
    "label_motd": {"en": "MOTD", "zh": "简介"},
    "label_online": {"en": "Online", "zh": "在线玩家"},
    "label_map": {"en": "Map", "zh": "地图"},
    "label_mode": {"en": "Mode", "zh": "模式"},
}


def format_text_java(address: str, status) -> str:
    motd = status.motd.to_plain().strip()
    lines = [
        f"[JE] {address}",
        f"{t(_S['label_version'])}: {status.version.name}",
        f"{t(_S['label_players'])}: {status.players.online}/{status.players.max}",
        f"{t(_S['label_latency'])}: {status.latency:.0f} ms",
    ]
    if motd:
        lines.insert(1, f"{t(_S['label_motd'])}: {motd}")
    sample = getattr(status.players, "sample", None)
    if sample:
        names = ", ".join(p.name for p in sample[:10])
        if len(sample) > 10:
            names += f" … (+{len(sample) - 10})"
        lines.append(f"{t(_S['label_online'])}: {names}")
    return "\n".join(lines)


def format_text_bedrock(address: str, status) -> str:
    motd = status.motd.to_plain().strip()
    lines = [
        f"[BE] {address}",
        f"{t(_S['label_version'])}: {status.version.brand} {status.version.name}",
        f"{t(_S['label_players'])}: {status.players.online}/{status.players.max}",
        f"{t(_S['label_latency'])}: {status.latency:.0f} ms",
    ]
    if motd:
        lines.insert(1, f"{t(_S['label_motd'])}: {motd}")
    if status.map_name:
        lines.append(f"{t(_S['label_map'])}: {status.map_name}")
    if status.gamemode:
        lines.append(f"{t(_S['label_mode'])}: {status.gamemode}")
    return "\n".join(lines)


async def build_image(display_name: str, status, is_be: bool) -> bytes:
    from .card import build_mc_card

    motd_parsed = None
    motd_plain = ""
    version_str = ""
    players_online = 0
    players_max = 0
    favicon: Optional[str] = None
    extra_info = ""

    try:
        motd_parsed = status.motd.parsed
        motd_plain = status.motd.to_plain()
    except Exception:
        pass

    player_sample: Optional[list[str]] = None

    if is_be:
        version_str = f"{status.version.brand} {status.version.name}"
        players_online = status.players.online
        players_max = status.players.max
        if status.map_name:
            extra_info = f"{t(_S['label_map'])}: {status.map_name}"
        if status.gamemode:
            extra_info = (extra_info + f"  {t(_S['label_mode'])}: {status.gamemode}").strip()
    else:
        version_str = status.version.name
        players_online = status.players.online
        players_max = status.players.max
        favicon = getattr(status, "icon", None)
        try:
            sample = status.players.sample
            if sample:
                player_sample = [p.name for p in sample]
        except Exception:
            pass

    return build_mc_card(
        display_name=display_name,
        motd_parsed=motd_parsed,
        motd_plain=motd_plain,
        version_str=version_str,
        players_online=players_online,
        players_max=players_max,
        latency=status.latency,
        favicon=favicon,
        is_bedrock=is_be,
        extra_info=extra_info,
        player_sample=player_sample,
        font_path=_cfg.card_font,
        font_weight=_cfg.card_font_weight,
    )


def want_image(status, is_be: bool) -> bool:
    """Decide whether to produce an image based on MCPING_IMAGE_MODE."""
    mode = _cfg.mcping_image_mode
    if mode == "off":
        return False
    if mode == "on":
        return True
    # "auto": generate image when the server provides a favicon (JE) or always for BE
    if is_be:
        return True
    return bool(getattr(status, "icon", None))
