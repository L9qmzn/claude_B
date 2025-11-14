"""Smoke test for reading Codex sessions via the `/codex/*` endpoints."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_app_config() -> dict:
    defaults = {
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
_db_path = Path(CONFIG["sessions_db"])
if not _db_path.is_absolute():
    _db_path = (CONFIG_PATH.parent / _db_path).resolve()
DB_PATH = _db_path


def _default_auth() -> tuple[str, str]:
    users = CONFIG.get("users")
    if isinstance(users, dict):
        for username, password in users.items():
            if isinstance(username, str) and isinstance(password, str):
                return username, password
    return "admin", "642531"


DEFAULT_USERNAME, DEFAULT_PASSWORD = _default_auth()


def _dt_to_str(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def ensure_codex_session_metadata(session_id: str, title: str, cwd: str) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS codex_sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                cwd TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        now = datetime.now(timezone.utc)
        payload = (
            session_id,
            title,
            cwd,
            _dt_to_str(now),
            _dt_to_str(now),
        )

        conn.execute(
            """
            INSERT INTO codex_sessions (session_id, title, cwd, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                title = excluded.title,
                cwd = excluded.cwd,
                updated_at = excluded.updated_at
            """,
            payload,
        )

        conn.commit()
    finally:
        conn.close()


def fetch_codex_session_detail(base_url: str, session_id: str, auth: httpx.Auth) -> dict:
    url = f"{base_url.rstrip('/')}/codex/sessions/{session_id}"
    resp = httpx.get(url, timeout=30.0, auth=auth)
    resp.raise_for_status()
    return resp.json()


def fetch_codex_session_list(base_url: str, auth: httpx.Auth) -> list[dict]:
    url = f"{base_url.rstrip('/')}/codex/sessions"
    resp = httpx.get(url, timeout=30.0, auth=auth)
    resp.raise_for_status()
    return resp.json()


def fetch_default_session_summary(base_url: str, auth: httpx.Auth) -> dict | None:
    sessions = fetch_codex_session_list(base_url, auth)
    if isinstance(sessions, list) and sessions:
        return sessions[0]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8207")
    parser.add_argument("--cwd", default=None, help="默认自动从 /codex/sessions 读取 cwd")
    parser.add_argument("--session-id", default=None, help="默认自动从 /codex/sessions 读取会话 ID")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="HTTP Basic 用户名")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="HTTP Basic 密码")
    parser.add_argument("--title", default="Codex 示例会话")

    args = parser.parse_args()

    auth = httpx.BasicAuth(args.username, args.password)
    session_id = args.session_id
    cwd = args.cwd

    default_session: dict | None = None
    if session_id is None or cwd is None:
        try:
            default_session = fetch_default_session_summary(args.base_url, auth)
        except httpx.HTTPError as exc:
            print(f"⚠️ 无法通过 /codex/sessions 获取默认会话: {exc}")

    if default_session:
        if session_id is None:
            session_id = default_session["session_id"]
            print(f"ℹ️ 默认使用 Codex 会话 {session_id}")
        if cwd is None:
            cwd = default_session["cwd"]
    elif session_id is None:
        print("⚠️ 没有可用 Codex 会话，无法运行测试（请使用 --session-id）")
        return

    if cwd is None:
        cwd = str(PROJECT_ROOT)
        print(f"ℹ️ 未指定 cwd，使用项目根目录: {cwd}")

    ensure_codex_session_metadata(session_id, args.title, cwd)
    print(f"✔️ 已确保 codex_sessions 表中存在元信息: {session_id}")

    detail = fetch_codex_session_detail(args.base_url, session_id, auth)
    print("/codex/sessions/{id} 返回:")
    print(f"  标题: {detail['title']}")
    print(f"  cwd: {detail['cwd']}")
    print(f"  消息条数: {len(detail.get('messages', []))}")
    print("  完整消息 JSON:")
    print(json.dumps(detail, ensure_ascii=False, indent=2))

    session_list = fetch_codex_session_list(args.base_url, auth)
    summary = next((item for item in session_list if item["session_id"] == session_id), None)
    if summary:
        print("/codex/sessions 列表中找到该会话:")
        print(f"  title={summary['title']} message_count={summary['message_count']}")
    else:
        print("⚠️ /codex/sessions 列表里没有找到该会话，请检查服务器日志")


if __name__ == "__main__":
    main()
