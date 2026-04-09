"""
b23extract/config.py – Configuration model, credential factory, and proxy setup.
"""

from nonebot import get_driver, get_plugin_config
from nonebot.log import logger
from pydantic import BaseModel, field_validator

from bilibili_api import Credential
from bilibili_api import request_settings as bili_settings


class _Config(BaseModel):
    bilibili_sessdata: str = ""
    bilibili_bili_jct: str = ""
    bilibili_buvid3: str = ""
    bilibili_proxy: str = ""
    bilibili_desc_max_len: int = 180
    bilibili_image_mode: str = "auto"          # "auto" | "on" | "off"
    card_font: str = ""                        # path to CJK-capable font file (shared)
    card_font_weight: str = "medium"          # TTC/OTC face: "regular" | "medium" | "bold"
    card_font_lang: str = "sc"               # CJK TTC variant: "sc" | "tc" | "jp" | "kr" | "hk" | ""
    bilibili_image_desc_max_lines: int = 12    # 0 = unlimited
    bilibili_filter_uploader_names: list[str] = []
    bilibili_filter_uploader_name_regex: list[str] = []
    bilibili_filter_uploader_uids: list[int] = []
    bilibili_filter_sender_whitelist: list[int] = []
    bilibili_filter_titles: list[str] = []
    bilibili_filter_title_regex: list[str] = []
    bilibili_filter_descriptions: list[str] = []
    bilibili_filter_description_regex: list[str] = []
    bilibili_filter_tags: list[str] = []
    bilibili_filter_tag_regex: list[str] = []
    bilibili_filter_categories: list[str] = []
    bilibili_filter_category_regex: list[str] = []
    bilibili_filter_reject_text: str = ""
    bilibili_filter_reject_image: str = ""

    @field_validator("bilibili_image_mode")
    @classmethod
    def _validate_image_mode(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("auto", "on", "off"):
            logger.warning(
                f"[b23extract] Invalid BILIBILI_IMAGE_MODE={v!r}; defaulting to 'auto'."
            )
            return "auto"
        return v

    @field_validator("card_font_weight")
    @classmethod
    def _validate_font_weight(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("regular", "medium", "bold"):
            logger.warning(
                f"[b23extract] Invalid CARD_FONT_WEIGHT={v!r}; defaulting to 'medium'."
            )
            return "medium"
        return v


_cfg = get_plugin_config(_Config)
_driver = get_driver()

# Apply proxy setting once at module load
if _cfg.bilibili_proxy:
    bili_settings.set_proxy(_cfg.bilibili_proxy)


def _get_credential() -> Credential:
    return Credential(
        sessdata=_cfg.bilibili_sessdata or None,
        bili_jct=_cfg.bilibili_bili_jct or None,
        buvid3=_cfg.bilibili_buvid3 or None,
    )
