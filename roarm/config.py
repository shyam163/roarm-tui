"""User config (~/.config/roarm/config.json). Holds the arm's Wi-Fi host — never passwords."""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "roarm" / "config.json"
_SECRET_WORDS = ("password", "pawword")


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    clean = {k: v for k, v in cfg.items() if not any(w in k.lower() for w in _SECRET_WORDS)}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(clean, f, indent=2)
    os.replace(tmp, path)
