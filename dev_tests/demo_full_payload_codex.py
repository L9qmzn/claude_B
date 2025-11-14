"""Demo script for `/codex/chat` SSE streams (ASCII-only output)."""

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
    except (FileNotFoundError, yaml.YAMLError):
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
    parser.add_argument("--message", default="列出当前目录的文件并说明用途")
    parser.add_argument("--cwd", default=None, help="默认自动读取 /codex/sessions 的 cwd")
    parser.add_argument("--session-id", default=None, help="继续 Codex 会话时使用的 session_id")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="HTTP Basic 用户名")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="HTTP Basic 密码")
    parser.add_argument("--approval-policy", default=None, choices=["never", "on-request", "on-failure", "untrusted"])
    parser.add_argument("--sandbox-mode", default=None, choices=["read-only", "workspace-write", "danger-full-access"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--model-reasoning-effort", default=None, choices=["minimal", "low", "medium", "high"])
    parser.add_argument("--network-access", action="store_true", help="为 Codex 开启网络访问")
    parser.add_argument("--web-search", action="store_true", help="为 Codex 开启 Web 搜索")
    parser.add_argument("--skip-git-repo-check", action="store_true", help="跳过 Codex 的 Git 仓库检查")
    parser.add_argument("--show-session-json", action="store_true", help="打印 `/codex/sessions/{id}` 的完整 JSON")
    return parser


async def fetch_default_codex_session(base_url: str, auth: httpx.Auth) -> Optional[dict]:
    url = f"{base_url.rstrip('/')}/codex/sessions"
    async with httpx.AsyncClient(timeout=30.0, auth=auth) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        sessions = resp.json()
    if isinstance(sessions, list) and sessions:
        return sessions[0]
    return None


def _append_optional(payload: dict, key: str, value) -> None:
    if value is not None:
        payload[key] = value


async def stream_codex_chat(
    *,
    base_url: str,
    message: str,
    cwd: str,
    session_id: Optional[str],
    approval_policy: Optional[str],
    sandbox_mode: Optional[str],
    model: Optional[str],
    model_reasoning_effort: Optional[str],
    network_access: bool,
    web_search: bool,
    skip_git_repo_check: bool,
    auth: httpx.Auth,
) -> str:
    payload: dict[str, object] = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    else:
        payload["cwd"] = cwd

    _append_optional(payload, "approval_policy", approval_policy)
    _append_optional(payload, "sandbox_mode", sandbox_mode)
    _append_optional(payload, "model", model)
    _append_optional(payload, "model_reasoning_effort", model_reasoning_effort)
    if network_access:
        payload["network_access_enabled"] = True
    if web_search:
        payload["web_search_enabled"] = True
    if skip_git_repo_check:
        payload["skip_git_repo_check"] = True

    url = f"{base_url.rstrip('/')}/codex/chat"
    print(f"[post] {url}")
    print(f"[post] payload = {json.dumps(payload, ensure_ascii=False)}\n")

    final_session_id: Optional[str] = session_id
    collected_text: list[str] = []

    async with httpx.AsyncClient(timeout=None, auth=auth) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            print("[stream] streaming events:\n")

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
                elif event_type == "error":
                    raise RuntimeError(f"服务器返回错误: {data_obj}")
                print()

    if not final_session_id:
        raise RuntimeError("未从 /codex/chat 流中获取 session_id")

    print("[done] Codex 流式对话完成")
    print(f"   session_id = {final_session_id}")
    if collected_text:
        preview = "".join(collected_text)
        print(f"   文本预览（前 200 字符）: {preview[:200]!r}")
    print()

    return final_session_id


async def fetch_codex_session_detail(base_url: str, session_id: str, auth: httpx.Auth) -> dict:
    url = f"{base_url.rstrip('/')}/codex/sessions/{session_id}"
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
            default_session = await fetch_default_codex_session(args.base_url, auth)
        except httpx.HTTPError as exc:
            print(f"[warn] 无法通过 /codex/sessions 获取默认会话: {exc}")

    if default_session and cwd is None and isinstance(default_session.get("cwd"), str):
        cwd = default_session["cwd"]
        print(f"[info] 推断 cwd = {cwd}（将启动新的 Codex 会话）")

    if cwd is None:
        cwd = str(PROJECT_ROOT)
        if session_id:
            print(f"[info] 未指定 cwd，使用项目根目录: {cwd}")
        else:
            print("[warn] 未发现可用会话，使用项目根目录启动新会话")

    session_id = await stream_codex_chat(
        base_url=args.base_url,
        message=args.message,
        cwd=cwd,
        session_id=session_id,
        approval_policy=args.approval_policy,
        sandbox_mode=args.sandbox_mode,
        model=args.model,
        model_reasoning_effort=args.model_reasoning_effort,
        network_access=args.network_access,
        web_search=args.web_search,
        skip_git_repo_check=args.skip_git_repo_check,
        auth=auth,
    )

    detail = await fetch_codex_session_detail(args.base_url, session_id, auth)
    print("[summary] /codex/sessions/{id} 概览:")
    print(f"   title = {detail['title']}")
    print(f"   cwd   = {detail['cwd']}")
    print(f"   messages = {len(detail.get('messages', []))} 条\n")

    if args.show_session_json:
        print(json.dumps(detail, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
