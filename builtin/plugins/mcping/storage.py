"""
mcping/storage.py – Per-group server alias registry backed by JSON files.
"""

import json
from typing import Optional

from nonebot.log import logger

from ...utils.i18n import t
from .config import DATA_DIR

_S = {
    "no_servers": {"en": "No servers saved.", "zh": "暂无已保存服务器。"},
    "default_label": {"en": "Default", "zh": "默认"},
    "none": {"en": "(none)", "zh": "（无）"},
}


class ServerRecord:
    """Persistent per-group server alias registry backed by a JSON file."""

    def __init__(self, group_id: int) -> None:
        self._gid = group_id
        self._path = DATA_DIR / f"{group_id}.json"
        self._data: dict = self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if self._path.is_file():
            try:
                return json.loads(self._path.read_bytes())
            except Exception as exc:
                logger.warning(f"[mcping] Failed to load {self._path}: {exc}")
        return {"default": "", "servers": {}}

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))

    # ── public interface ──────────────────────────────────────────────────────

    def add(self, alias: str, address: str, port: int, is_be: bool) -> bool:
        """Add an alias.  Returns ``False`` if the alias already exists."""
        if alias in self._data["servers"]:
            return False
        self._data["servers"][alias] = {"address": address, "port": port, "is_be": is_be}
        if not self._data["default"]:
            self._data["default"] = alias
        self._save()
        return True

    def remove(self, alias: str) -> bool:
        """Remove an alias.  Returns ``False`` when not found."""
        if alias not in self._data["servers"]:
            return False
        del self._data["servers"][alias]
        if self._data["default"] == alias:
            self._data["default"] = next(iter(self._data["servers"]), "")
        self._save()
        return True

    def set_default(self, alias: str) -> bool:
        """Set the default alias.  Returns ``False`` when *alias* not found."""
        if alias not in self._data["servers"]:
            return False
        self._data["default"] = alias
        self._save()
        return True

    def get(self, alias: str = "") -> Optional[dict]:
        """Return the server record for *alias* or the group default."""
        if alias:
            return self._data["servers"].get(alias)
        default = self._data["default"]
        if not default:
            return None
        return self._data["servers"].get(default)

    def list_all(self) -> str:
        """Return a human-readable listing of all saved servers."""
        servers: dict = self._data["servers"]
        default: str = self._data["default"]
        if not servers:
            return t(_S["no_servers"])
        lines = [f"{t(_S['default_label'])}: {default or t(_S['none'])}"]
        for i, (alias, rec) in enumerate(servers.items(), 1):
            kind = "BE" if rec.get("is_be") else "JE"
            port = rec.get("port", 0)
            addr = rec["address"]
            if port:
                addr = f"{addr}:{port}"
            lines.append(f"{i}. {alias}  [{kind}]  {addr}")
        return "\n".join(lines)

    @property
    def aliases(self) -> set[str]:
        return set(self._data["servers"].keys())

    @property
    def default_alias(self) -> str:
        return self._data["default"]
