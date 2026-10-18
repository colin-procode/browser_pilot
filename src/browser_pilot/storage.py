from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def data_home() -> Path:
    """Return the local data directory, honoring XDG and an override."""
    override = os.environ.get("BROWSER_PILOT_HOME")
    if override:
        root = Path(override).expanduser()
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "browser-pilot"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def profile_name() -> str:
    name = os.environ.get("BROWSER_PILOT_PROFILE", "default")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", name):
        raise ValueError("Profile name may contain letters, numbers, underscores, and hyphens")
    return name


def profile_dir(name: str | None = None) -> Path:
    path = data_home() / "profiles" / (name or profile_name())
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def workflow_dir() -> Path:
    path = data_home() / "workflows"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback


def write_json(path: Path, value: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if private:
        temp.chmod(0o600)
    temp.replace(path)
