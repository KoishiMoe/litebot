"""
mcping/config.py – Configuration model, data directory, and plugin config loading.
"""

from nonebot import get_plugin_config
from nonebot.log import logger
from pydantic import BaseModel, field_validator

from ...utils.storage import get_data_dir


class _Config(BaseModel):
    mcping_image_mode: str = "auto"    # "auto" | "on" | "off"
    card_font: str = ""                # shared CJK font path
    card_font_weight: str = "medium"   # TTC/OTC face weight
    card_font_lang: str = "sc"        # CJK TTC variant: "sc" | "tc" | "jp" | "kr" | "hk" | ""

    @field_validator("mcping_image_mode")
    @classmethod
    def _validate_image_mode(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("auto", "on", "off"):
            logger.warning(
                f"[mcping] Invalid MCPING_IMAGE_MODE={v!r}; defaulting to 'auto'."
            )
            return "auto"
        return v

    @field_validator("card_font_weight")
    @classmethod
    def _validate_font_weight(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("regular", "medium", "bold"):
            logger.warning(
                f"[mcping] Invalid CARD_FONT_WEIGHT={v!r}; defaulting to 'medium'."
            )
            return "medium"
        return v


_cfg = get_plugin_config(_Config)
DATA_DIR = get_data_dir("mcping")
DATA_DIR.mkdir(parents=True, exist_ok=True)
