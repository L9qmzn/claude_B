from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_app_config() -> Dict[str, str]:
    defaults = {
        "claude_dir": str(Path.home() / ".claude"),
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

    return config


CONFIG = load_app_config()
CLAUDE_ROOT = Path(CONFIG["claude_dir"]).expanduser()
CLAUDE_PROJECTS_DIR = CLAUDE_ROOT / "projects"
_db_path = Path(CONFIG["sessions_db"])
if not _db_path.is_absolute():
    _db_path = (CONFIG_PATH.parent / _db_path).resolve()
DB_PATH = _db_path
