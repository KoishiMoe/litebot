"""
b23extract/filter.py – Content filter logic.
"""

import re
from typing import Any, Optional

from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.log import logger

from .config import _cfg, _driver


def _compile_regex_list(patterns: list[str], field_name: str) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for p in patterns:
        if not p:
            continue
        try:
            compiled.append(re.compile(p, re.I))
        except re.error as exc:
            logger.warning(f"[b23extract] Invalid regex in {field_name}: {p!r} ({exc})")
    return compiled


def _find_literal_match(text: str, literals: list[str]) -> Optional[str]:
    """Return the first matching literal keyword, or ``None``."""
    if not text:
        return None
    low = text.casefold()
    for literal in literals:
        if literal and literal.casefold() in low:
            return literal
    return None


def _find_regex_match(text: str, patterns: list[re.Pattern[str]]) -> Optional[str]:
    """Return the pattern string of the first matching regex, or ``None``."""
    if not text:
        return None
    for p in patterns:
        if p.search(text):
            return p.pattern
    return None


_UPLOADER_NAME_FILTER_RE = _compile_regex_list(
    _cfg.bilibili_filter_uploader_name_regex, "BILIBILI_FILTER_UPLOADER_NAME_REGEX"
)
_UPLOADER_UID_FILTER_SET = set(_cfg.bilibili_filter_uploader_uids)
_TITLE_FILTER_RE = _compile_regex_list(_cfg.bilibili_filter_title_regex, "BILIBILI_FILTER_TITLE_REGEX")
_DESC_FILTER_RE = _compile_regex_list(
    _cfg.bilibili_filter_description_regex, "BILIBILI_FILTER_DESCRIPTION_REGEX"
)
_TAG_FILTER_RE = _compile_regex_list(_cfg.bilibili_filter_tag_regex, "BILIBILI_FILTER_TAG_REGEX")
_CATEGORY_FILTER_RE = _compile_regex_list(
    _cfg.bilibili_filter_category_regex, "BILIBILI_FILTER_CATEGORY_REGEX"
)


def is_filtered(info: dict[str, Any]) -> Optional[str]:
    """Return a human-readable reason string if the content should be filtered,
    or ``None`` if it passes all rules."""
    uploader_name = str(info.get("author", "") or "")
    uploader_uid = int(info.get("uploader_uid", 0) or 0)
    title = str(info.get("title", "") or "")
    description = str(info.get("description", "") or "")
    category = str(info.get("category", "") or "")
    tags = [str(t) for t in (info.get("tags") or []) if t]

    if m := _find_literal_match(uploader_name, _cfg.bilibili_filter_uploader_names):
        return f"uploader_name {uploader_name!r} matched literal {m!r}"
    if m := _find_regex_match(uploader_name, _UPLOADER_NAME_FILTER_RE):
        return f"uploader_name {uploader_name!r} matched regex /{m}/"

    if uploader_uid and uploader_uid in _UPLOADER_UID_FILTER_SET:
        return f"uploader_uid {uploader_uid} in blocklist"

    if m := _find_literal_match(title, _cfg.bilibili_filter_titles):
        return f"title matched literal {m!r}"
    if m := _find_regex_match(title, _TITLE_FILTER_RE):
        return f"title matched regex /{m}/"

    if m := _find_literal_match(description, _cfg.bilibili_filter_descriptions):
        return f"description matched literal {m!r}"
    if m := _find_regex_match(description, _DESC_FILTER_RE):
        return f"description matched regex /{m}/"

    if m := _find_literal_match(category, _cfg.bilibili_filter_categories):
        return f"category {category!r} matched literal {m!r}"
    if m := _find_regex_match(category, _CATEGORY_FILTER_RE):
        return f"category {category!r} matched regex /{m}/"

    for tag in tags:
        if m := _find_literal_match(tag, _cfg.bilibili_filter_tags):
            return f"tag {tag!r} matched literal {m!r}"
        if m := _find_regex_match(tag, _TAG_FILTER_RE):
            return f"tag {tag!r} matched regex /{m}/"

    return None


def sender_bypasses_filter(event: MessageEvent) -> bool:
    uid = event.get_user_id()
    superusers = {str(user_id) for user_id in _driver.config.superusers}
    sender_whitelist = {str(user_id) for user_id in _cfg.bilibili_filter_sender_whitelist}
    return uid in superusers or uid in sender_whitelist
