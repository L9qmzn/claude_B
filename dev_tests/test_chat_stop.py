"""Smoke test for `/chat` + `/chat/stop` flow with controllable stop timing."""

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
    parser.add_argument("--message", default="列出当前目录都有哪些文件")
    parser.add_argument("--cwd", default=None, help="Working directory for new session")
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument(
        "--stop-delay",
        type=float,
        default=0.5,
        help="Seconds to wait before calling /chat/stop once stop condition is met",
    )
    parser.add_argument(
        "--stop-after-tokens",
        type=int,
        default=0,
        help="If > 0, defer stop until this many `token` events have been seen",
    )
    parser.add_argument(
        "--fallback-stop-delay",
        type=float,
        default=5.0,
        help="Fallback seconds after run start to force stop even if token threshold not reached (<=0 disables)",
    )
    return parser


async def fetch_default_session(base_url: str, auth: httpx.Auth) -> Optional[dict]:
    url = f"{base_url.rstrip('/')}/sessions"
    async with httpx.AsyncClient(timeout=10.0, auth=auth) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        sessions = resp.json()
    if isinstance(sessions, list) and sessions:
        return sessions[0]
    return None


async def stop_run(base_url: str, run_id: str, auth: httpx.Auth) -> dict:
    url = f"{base_url.rstrip('/')}/chat/stop"
    async with httpx.AsyncClient(timeout=10.0, auth=auth) as client:
        resp = await client.post(url, json={"run_id": run_id})
        resp.raise_for_status()
        return resp.json()


async def stream_and_stop(
    *,
    base_url: str,
    message: str,
    cwd: str,
    auth: httpx.Auth,
    stop_delay: float,
    stop_after_tokens: int,
    fallback_stop_delay: float,
) -> None:
    payload = {"message": message, "cwd": cwd}
    url = f"{base_url.rstrip('/')}/chat"

    print(f"[post] {url}")
    print(f"[post] payload = {json.dumps(payload, ensure_ascii=False)}\n")

    run_id: Optional[str] = None
    stop_task: Optional[asyncio.Task] = None
    fallback_task: Optional[asyncio.Task] = None
    stop_requested = False
    token_counter = 0

    async with httpx.AsyncClient(timeout=None, auth=auth) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            header_run_id = resp.headers.get("X-Claude-Run-Id")
            if header_run_id:
                print(f"[info] got run_id from header: {header_run_id}")
                run_id = header_run_id

            print("[stream] events:\n")
            event_type: Optional[str] = None
            stopped_seen = False
            done_seen = False

            async def _request_stop(reason: str) -> None:
                nonlocal stop_requested, fallback_task
                if stop_requested or not run_id:
                    return
                stop_requested = True
                if fallback_task and not fallback_task.done():
                    fallback_task.cancel()
                try:
                    resp_data = await stop_run(base_url, run_id, auth)
                    print(f"[stop:{reason}] response = {json.dumps(resp_data, ensure_ascii=False)}")
                except httpx.HTTPStatusError as exc:
                    print(f"[warn] stop call returned {exc.response.status_code}: {exc}")
                except httpx.HTTPError as exc:
                    print(f"[warn] stop call failed: {exc}")

            def ensure_stop_task(reason: str) -> None:
                nonlocal stop_task
                if not run_id or stop_requested:
                    return
                if stop_task and not stop_task.done():
                    return
                async def runner() -> None:
                    if stop_delay > 0:
                        await asyncio.sleep(stop_delay)
                    await _request_stop(reason)
                stop_task = asyncio.create_task(runner())

            def ensure_fallback_task() -> None:
                nonlocal fallback_task
                if fallback_stop_delay <= 0 or not run_id:
                    return
                if fallback_task and not fallback_task.done():
                    return

                async def fallback_runner() -> None:
                    await asyncio.sleep(fallback_stop_delay)
                    if stop_requested or not run_id:
                        return
                    print(f"[info] fallback stop triggered after {fallback_stop_delay}s")
                    await _request_stop("fallback")

                fallback_task = asyncio.create_task(fallback_runner())

            try:
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

                    if event_type == "run" and isinstance(data_obj, dict):
                        run_id = data_obj.get("run_id") or run_id
                        if stop_after_tokens <= 0:
                            ensure_stop_task("run-event")
                        ensure_fallback_task()
                    elif event_type == "token":
                        token_counter += 1
                        if stop_after_tokens > 0 and token_counter >= stop_after_tokens:
                            print(
                                f"[info] token threshold reached ({token_counter}), scheduling stop"
                            )
                            ensure_stop_task("token-threshold")
                    elif event_type == "stopped":
                        stopped_seen = True
                        print("[info] received stopped event, ending stream")
                        break
                    elif event_type == "done":
                        done_seen = True
                        if stop_task and not stop_task.done():
                            print("[info] run completed before stop could fire, cancel pending stop")
                            stop_task.cancel()
            except httpx.RemoteProtocolError as exc:
                print(f"[warn] SSE stream closed early: {exc}")
                if run_id:
                    try:
                        resp_data = await stop_run(base_url, run_id, auth)
                        print(f"[stop] response after early close = {json.dumps(resp_data, ensure_ascii=False)}")
                        if stop_task and not stop_task.done():
                            stop_task.cancel()
                    except httpx.HTTPError as stop_exc:
                        print(f"[warn] stop call failed after early close: {stop_exc}")
                else:
                    print(f"[warn] headers on early close: {dict(resp.headers)}")

            if stop_task:
                try:
                    await stop_task
                except asyncio.CancelledError:
                    pass
            if fallback_task:
                fallback_task.cancel()

            if stopped_seen:
                print("[info] stop confirmed via SSE")
            elif done_seen:
                print("[info] run finished normally before stop triggered")
            elif not stop_requested:
                print("[warn] no run_id captured; cannot call /chat/stop")


async def main_async() -> None:
    args = build_parser().parse_args()
    auth = httpx.BasicAuth(args.username, args.password)

    cwd = args.cwd
    if cwd is None:
        default_session = await fetch_default_session(args.base_url, auth)
        if default_session and isinstance(default_session.get("cwd"), str):
            cwd = default_session["cwd"]
            print(f"[info] use cwd from existing session: {cwd}")
        else:
            cwd = str(PROJECT_ROOT)
            print(f"[warn] no session found; use project root as cwd: {cwd}")

    await stream_and_stop(
        base_url=args.base_url,
        message=args.message,
        cwd=cwd,
        auth=auth,
        stop_delay=args.stop_delay,
        stop_after_tokens=args.stop_after_tokens,
        fallback_stop_delay=args.fallback_stop_delay,
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
