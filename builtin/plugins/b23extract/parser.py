"""
b23extract/parser.py – URL detection, short-link resolution, and Bilibili API parsers.
"""

import re
from typing import Any, Optional

import aiohttp
from nonebot.log import logger

from bilibili_api import (
    article as bili_article,
    bangumi as bili_bangumi,
    live as bili_live,
    video as bili_video,
)
from bilibili_api import Credential
from bilibili_api.exceptions import NetworkException, ResponseCodeException  # noqa: F401

from ...utils.i18n import t
from .config import _get_credential

_S = {
    "unknown_title": {"en": "Unknown title", "zh": "未知标题"},
}

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

BILI_RE = re.compile(
    r"(b23\.tv|(bili(22|23|33|2233)\.cn))"  # short domains
    r"|live\.bilibili\.com"
    r"|bilibili\.com[/\\](video|read|bangumi)"
    r"|^(av|cv)(\d+)"
    r"|^BV([a-zA-Z0-9]{10})+"
    r"|\[\[QQ小程序\]哔哩哔哩\]"
    r"|QQ小程序.*哔哩哔哩",
    re.I,
)

_SHORT_URL_RE = re.compile(
    r"(b23\.tv|(bili(?:22|23|33|2233)\.cn))\\\\?/[A-Za-z0-9]+", re.I
)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0"
    ),
    "Referer": "https://www.bilibili.com/",
}


async def _resolve_short_url(url: str) -> str:
    """Follow redirects for b23.tv / bili*.cn short links."""
    url = "https://" + url.replace("\\", "")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=_BILI_HEADERS,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                return str(resp.url)
    except Exception as exc:
        logger.warning(f"[b23extract] short-URL resolution failed for {url}: {exc}")
        return url


# ---------------------------------------------------------------------------
# API parsers  –  each returns a unified info dict
# ---------------------------------------------------------------------------
# Common keys:
#   type          : "video" | "live" | "bangumi" | "article"
#   title         : str
#   author        : str
#   author_avatar : str  (URL, may be "")
#   cover_url     : str  (URL, may be "")
#   category      : str
#   tags          : list[str]
#   description   : str  (full, untruncated)
#   url           : str  (canonical URL)


async def _parse_video(text: str, credential: Credential) -> Optional[dict[str, Any]]:
    bvid_m = re.search(r"BV([a-zA-Z0-9]{10})", text, re.I)
    aid_m = re.search(r"av(\d+)", text, re.I)
    try:
        if bvid_m:
            vid = bili_video.Video(bvid=bvid_m.group(0), credential=credential)
        elif aid_m:
            vid = bili_video.Video(aid=int(aid_m.group(1)), credential=credential)
        else:
            return None

        info = await vid.get_info()
        aid = info.get("aid", 0)
        bvid = info.get("bvid", "")
        title = info.get("title", t(_S["unknown_title"]))
        owner = info.get("owner", {})
        up_name = owner.get("name", "")
        up_uid = owner.get("mid", 0) or 0
        up_avatar = owner.get("face", "")
        tname = info.get("tname", "")
        desc = info.get("desc", "")
        cover = info.get("pic", "")

        # Staff videos: credit all contributors
        if info.get("staff"):
            up_name = " / ".join(m.get("name", "") for m in info["staff"])

        tags: list[str] = []
        try:
            raw_tags = await vid.get_tags()
            tags = [t.get("tag_name", "") for t in raw_tags if t.get("tag_name")]
        except Exception as exc:
            logger.debug(f"[b23extract] get_tags failed (non-fatal): {exc}")

        if aid:
            url = f"https://b23.tv/av{aid}"
        elif bvid:
            url = f"https://b23.tv/{bvid}"
        else:
            logger.warning("[b23extract] Video info missing both aid and bvid.")
            return None

        stat = info.get("stat", {})
        return {
            "type": "video",
            "title": title,
            "author": up_name,
            "uploader_uid": up_uid,
            "author_avatar": up_avatar,
            "cover_url": cover,
            "category": tname,
            "tags": tags,
            "description": desc,
            "url": url,
            "stats": {
                "view":     int(stat.get("view",     0)),
                "like":     int(stat.get("like",     0)),
                "coin":     int(stat.get("coin",     0)),
                "favorite": int(stat.get("favorite", 0)),
            },
        }
    except Exception as exc:
        logger.warning(f"[b23extract] Video parse error: {exc}")
        return None


async def _parse_live(room_id: int, credential: Credential) -> Optional[dict[str, Any]]:
    try:
        room = bili_live.LiveRoom(room_id, credential=credential)
        info = await room.get_room_info()
        room_info = info.get("room_info", {})
        anchor = info.get("anchor_info", {}).get("base_info", {})

        title = room_info.get("title", "")
        up_name = anchor.get("uname", "")
        up_uid = anchor.get("uid", 0) or 0
        up_avatar = anchor.get("face", "")
        area = (
            f'{room_info.get("parent_area_name", "")}'
            f'-{room_info.get("area_name", "")}'
        ).strip("-")
        tags_raw = room_info.get("tags", "")
        tags = [t for t in tags_raw.split(",") if t] if tags_raw else []
        desc = room_info.get("description", "")
        cover = room_info.get("cover", "") or room_info.get("keyframe", "")

        url = f"https://live.bilibili.com/{room_id}"
        return {
            "type": "live",
            "title": title,
            "author": up_name,
            "uploader_uid": up_uid,
            "author_avatar": up_avatar,
            "cover_url": cover,
            "category": area,
            "tags": tags,
            "description": desc,
            "url": url,
            "stats": {
                "online": int(room_info.get("online", 0)),
            },
        }
    except Exception as exc:
        logger.warning(f"[b23extract] Live parse error: {exc}")
        return None


async def _parse_bangumi(text: str, credential: Credential) -> Optional[dict[str, Any]]:
    epid_m = re.search(r"ep(\d+)", text, re.I)
    ssid_m = re.search(r"ss(\d+)", text, re.I)
    mdid_m = re.search(r"md(\d+)", text, re.I)
    try:
        if mdid_m:
            mdid = int(mdid_m.group(1))
            bg = bili_bangumi.Bangumi(media_id=mdid, credential=credential)
            meta = await bg.get_meta()
            media = meta.get("media", {})
            title = media.get("title", "")
            ssid = media.get("season_id", 0)
            cover = media.get("cover", "")
            url = media.get("share_url") or f"https://www.bilibili.com/bangumi/media/md{mdid}"
            desc = ""
            if ssid:
                bg2 = bili_bangumi.Bangumi(ssid=ssid, credential=credential)
                ov = await bg2.get_overview()
                desc = ov.get("evaluate", "")
                cover = cover or ov.get("cover", "")
            return {
                "type": "bangumi", "title": title, "author": "",
                "uploader_uid": 0,
                "author_avatar": "", "cover_url": cover, "category": "",
                "tags": [], "description": desc, "url": url,
            }

        elif ssid_m:
            ssid = int(ssid_m.group(1))
            bg = bili_bangumi.Bangumi(ssid=ssid, credential=credential)
            ov = await bg.get_overview()
            title = ov.get("season_title") or ov.get("title", "")
            mdid = ov.get("media_id", 0)
            desc = ov.get("evaluate", "")
            cover = ov.get("cover", "")
            url = (
                f"https://www.bilibili.com/bangumi/media/md{mdid}"
                if mdid
                else f"https://www.bilibili.com/bangumi/play/ss{ssid}"
            )
            return {
                "type": "bangumi", "title": title, "author": "",
                "uploader_uid": 0,
                "author_avatar": "", "cover_url": cover, "category": "",
                "tags": [], "description": desc, "url": url,
            }

        elif epid_m:
            epid = int(epid_m.group(1))
            ep = bili_bangumi.Episode(epid=epid, credential=credential)
            ep_data, _ = await ep.get_episode_info()
            title = ep_data.get("h1Title", "")
            media_info = ep_data.get("mediaInfo", {}) or {}
            desc = media_info.get("evaluate", "")
            cover = media_info.get("cover", "")
            url = f"https://www.bilibili.com/bangumi/play/ep{epid}"
            return {
                "type": "bangumi", "title": title, "author": "",
                "uploader_uid": 0,
                "author_avatar": "", "cover_url": cover, "category": "",
                "tags": [], "description": desc, "url": url,
            }

    except Exception as exc:
        logger.warning(f"[b23extract] Bangumi parse error: {exc}")
    return None


async def _parse_article(text: str, credential: Credential) -> Optional[dict[str, Any]]:
    cvid_m = re.search(r"(?:cv|/read/(?:mobile|native)(?:/|\?id=))(\d+)", text, re.I)
    if not cvid_m:
        return None
    cvid = int(cvid_m.group(1))
    try:
        art = bili_article.Article(cvid=cvid, credential=credential)
        all_data = await art.get_all()
        read_info = all_data.get("readInfo", {}) or {}
        title = read_info.get("title", "")
        author = read_info.get("author_name", "")
        author_uid = read_info.get("author_mid", 0) or 0
        cover = read_info.get("banner_url", "") or ""
        url = f"https://www.bilibili.com/read/cv{cvid}"
        return {
            "type": "article", "title": title, "author": author,
            "uploader_uid": author_uid,
            "author_avatar": "", "cover_url": cover, "category": "",
            "tags": [], "description": "", "url": url,
        }
    except Exception as exc:
        logger.warning(f"[b23extract] Article parse error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


async def extract_info(text: str) -> Optional[dict[str, Any]]:
    """Identify the Bilibili content type in *text* and return its info dict."""
    credential = _get_credential()

    short_m = _SHORT_URL_RE.search(text)
    if short_m:
        text = await _resolve_short_url(short_m.group(0))

    if re.search(r"live\.bilibili\.com", text, re.I):
        room_m = re.search(
            r"live\.bilibili\.com[/\\]+(?:(?:blanc|h5)[/\\]+)?(\d+)", text, re.I
        )
        if room_m:
            return await _parse_live(int(room_m.group(1)), credential)

    if re.search(r"bilibili\.com[/\\]bangumi|ep\d+|ss\d+|md\d+", text, re.I):
        result = await _parse_bangumi(text, credential)
        if result:
            return result

    if re.search(r"bilibili\.com[/\\]read|cv\d+", text, re.I):
        result = await _parse_article(text, credential)
        if result:
            return result

    if re.search(r"BV[a-zA-Z0-9]{10}|av\d+|bilibili\.com[/\\]video", text, re.I):
        return await _parse_video(text, credential)

    return None
