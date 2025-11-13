from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Dict

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def _iter_candidate_claude_dirs():
    home = Path.home()

    env_values = [
        os.environ.get("CLAUDE_DIR"),
        os.environ.get("CLAUDE_HOME"),
    ]
    for value in env_values:
        if value:
            yield Path(value).expanduser()

    yield home / ".claude"

    system = platform.system().lower()
    if system == "darwin":
        yield home / "Library" / "Application Support" / "Claude"
    elif system == "windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            yield Path(appdata) / "Claude"
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            yield Path(localappdata) / "Claude"
        yield home / "AppData" / "Roaming" / "Claude"
        yield home / "AppData" / "Local" / "Claude"
    else:
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        if xdg_data_home:
            yield Path(xdg_data_home) / "claude"


def _detect_claude_dir() -> Path:
    fallback = (Path.home() / ".claude").expanduser()
    seen = set()

    for candidate in _iter_candidate_claude_dirs():
        resolved = candidate.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)

        if resolved.exists() or (resolved / "projects").exists():
            return resolved

    return fallback


def load_app_config() -> Dict[str, str]:
    defaults = {
        "claude_dir": "",
        "sessions_db": str(PROJECT_ROOT / "sessions.db"),
    }

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handler:
            data = yaml.safe_load(handler) or {}
    except FileNotFoundError:
        data = {}
    except yaml.YAMLError:
        data = {}

    if not isinstance(data, dict):
        data = {}

    config = defaults.copy()
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            config[key] = value.strip()

    if not config.get("claude_dir"):
        config["claude_dir"] = str(_detect_claude_dir())

    return config


CONFIG = load_app_config()
CLAUDE_ROOT = Path(CONFIG["claude_dir"]).expanduser()
CLAUDE_PROJECTS_DIR = CLAUDE_ROOT / "projects"
_db_path = Path(CONFIG["sessions_db"])
if not _db_path.is_absolute():
    _db_path = (CONFIG_PATH.parent / _db_path).resolve()
DB_PATH = _db_path
