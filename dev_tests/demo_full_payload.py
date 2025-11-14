"""Demo script to inspect `/chat` SSE stream and session detail payloads."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Optional

import httpx
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def _load_default_auth() -> tuple[str, str]:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handler:
            data = yaml.safe_load(handler) or {}
    except FileNotFoundError:
        data = {}
    except yaml.YAMLError:
        data = {}

    if isinstance(data, dict):
        users = data.get("users")
        if isinstance(users, dict):
            for username, password in users.items():
                if isinstance(username, str) and isinstance(password, str):
                    return username, password

    return "admin", "642531"


DEFAULT_USERNAME, DEFAULT_PASSWORD = _load_default_auth()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8207")
    parser.add_argument("--message", default="当前目录都有哪些文件")
    parser.add_argument(
        "--cwd",
        default=None,
        help="默认自动读取 /sessions 的 cwd（无会话时使用项目根目录）",
    )
    parser.add_argument(
        "--permission-mode",
        default="default",
        choices=["default", "plan", "acceptEdits", "bypassPermissions"],
        help="权限模式（default/plan/acceptEdits/bypassPermissions）",
    )
    parser.add_argument("--session-id", default=None, help="继续对话时使用的 session_id")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="HTTP Basic 用户名")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="HTTP Basic 密码")
    parser.add_argument(
        "--show-session-json",
        action="store_true",
        help="打印 `/sessions/{id}` 的完整 JSON（默认仅打印概要）",
    )
    return parser


async def fetch_default_session(base_url: str, auth: httpx.Auth) -> Optional[dict]:
    url = f"{base_url.rstrip('/')}/sessions"
    async with httpx.AsyncClient(timeout=30.0, auth=auth) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        sessions = resp.json()

    if isinstance(sessions, list) and sessions:
        return sessions[0]
    return None


async def stream_chat(
    *,
    base_url: str,
    message: str,
    cwd: str,
    permission_mode: str,
    session_id: Optional[str],
    auth: httpx.Auth,
) -> str:
    payload = {"message": message, "permission_mode": permission_mode}
    if session_id:
        payload["session_id"] = session_id
    else:
        payload["cwd"] = cwd

    url = f"{base_url.rstrip('/')}/chat"
    print(f"▶️  POST {url}")
    print(f"    payload = {json.dumps(payload, ensure_ascii=False)}\n")

    final_session_id: Optional[str] = session_id
    collected_text: list[str] = []

    async with httpx.AsyncClient(timeout=None, auth=auth) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            print("📥 Streaming events:\n")

            event_type: Optional[str] = None
            async for raw_line in resp.aiter_lines():
                if raw_line is None:
                    continue
                line = raw_line.strip()
                if not line:
                    continue

                if line.startswith("event:"):
                    event_type = line.split("event:", 1)[1].strip()
                    print(f"event: {event_type}")
                    continue

                if not line.startswith("data:"):
                    continue

                data_str = line.split("data:", 1)[1].strip()
                try:
                    data_obj = json.loads(data_str)
                except json.JSONDecodeError:
                    data_obj = data_str

                print("data:", json.dumps(data_obj, ensure_ascii=False, indent=2))

                if event_type == "session":
                    final_session_id = data_obj.get("session_id") or final_session_id
                elif event_type == "token":
                    chunk = data_obj.get("text") or ""
                    collected_text.append(chunk)
                elif event_type == "message":
                    payload_session = data_obj.get("session_id")
                    print(f"   ↳ message payload for session {payload_session}")
                elif event_type == "error":
                    raise RuntimeError(f"服务器返回错误: {data_obj}")

                print()

    if not final_session_id:
        raise RuntimeError("未从 /chat 流中获取 session_id")

    print("✅ 流式对话完成")
    print(f"   session_id = {final_session_id}")
    if collected_text:
        preview = "".join(collected_text)
        print(f"   文本预览（前 200 字符）: {preview[:200]!r}")
    print()

    return final_session_id


async def fetch_session_detail(base_url: str, session_id: str, auth: httpx.Auth) -> dict:
    url = f"{base_url.rstrip('/')}/sessions/{session_id}"
    async with httpx.AsyncClient(timeout=60.0, auth=auth) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def main_async() -> None:
    args = build_parser().parse_args()

    auth = httpx.BasicAuth(args.username, args.password)

    session_id: Optional[str] = args.session_id
    cwd: Optional[str] = args.cwd

    default_session: Optional[dict] = None
    if session_id is None or cwd is None:
        try:
            default_session = await fetch_default_session(args.base_url, auth)
        except httpx.HTTPError as exc:
            print(f"⚠️ 无法从 /sessions 获取默认会话: {exc}")

    if default_session:
        if session_id is None:
            inferred_session_id = default_session.get("session_id")
            if inferred_session_id:
                print(f"ℹ️ 从会话 {inferred_session_id} 推断 cwd")
        if cwd is None:
            cwd = default_session.get("cwd")

    if cwd is None:
        cwd = str(PROJECT_ROOT)
        if not session_id:
            print("⚠️ 未发现可用会话，将使用项目根目录启动新会话。")
        else:
            print(f"ℹ️ 未指定 cwd，使用项目根目录: {cwd}")

    session_id = await stream_chat(
        base_url=args.base_url,
        message=args.message,
        cwd=cwd,
        permission_mode=args.permission_mode,
        session_id=session_id,
        auth=auth,
    )

    detail = await fetch_session_detail(args.base_url, session_id, auth)
    print("📄 /sessions/{id} 概览：")
    print(f"   title = {detail['title']}")
    print(f"   cwd   = {detail['cwd']}")
    print(f"   messages = {len(detail.get('messages', []))} 条\n")

    if args.show_session_json:
        print(json.dumps(detail, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
