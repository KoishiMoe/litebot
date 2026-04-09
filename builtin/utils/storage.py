"""
storage.py – Shared persistent data-directory resolver.

Plugins should call ``get_data_dir(plugin)`` instead of hard-coding
``Path("data") / plugin``.  The root data directory is controlled by
the ``DATA_DIR`` environment variable (default: ``"data"`` relative
to the working directory).

Usage in a plugin:
    from ..utils.storage import get_data_dir

    _DATA_DIR = get_data_dir("my_plugin")   # → <DATA_DIR>/my_plugin/
"""

import os
from pathlib import Path


def get_data_dir(plugin: str = "") -> Path:
    """Return the data directory path for *plugin*.

    The root directory is taken from the ``DATA_DIR`` environment variable,
    defaulting to ``"data"`` relative to the current working directory.
    If *plugin* is non-empty, a dedicated sub-directory for that plugin is
    returned; otherwise the root data directory itself is returned.
    """
    base = Path(os.getenv("DATA_DIR", "data"))
    return (base / plugin) if plugin else base
