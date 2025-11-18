"""
Simple test for continuous messaging - manually sends two messages.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import yaml

# Fix Windows console encoding issues
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

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


async def send_message(client: httpx.AsyncClient, message: str, session_id: str | None, cwd: str | None):
    """Send a message and listen to SSE stream."""
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    if cwd:
        payload["cwd"] = cwd

    print(f"\n{'='*70}")
    print(f"Sending: {message}")
    if session_id:
        print(f"Session: {session_id}")
    print('='*70)

    url = "http://127.0.0.1:8207/chat"
    captured_session_id = session_id
    token_count = 0

    async with client.stream("POST", url, json=payload) as resp:
        resp.raise_for_status()
        run_id = resp.headers.get("X-Claude-Run-Id")
        print(f"Run ID: {run_id}\n")

        event_type = None
        async for raw_line in resp.aiter_lines():
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("event:"):
                event_type = line.split("event:", 1)[1].strip()
                continue

            if not line.startswith("data:"):
                continue

            data_str = line.split("data:", 1)[1].strip()
            try:
                data_obj = json.loads(data_str)
            except json.JSONDecodeError:
                data_obj = data_str

            if event_type == "session" and isinstance(data_obj, dict):
                captured_session_id = data_obj.get("session_id")
                is_new = data_obj.get("is_new", False)
                print(f"[session] ID={captured_session_id}, new={is_new}")

            elif event_type == "token" and isinstance(data_obj, dict):
                text = data_obj.get("text", "")
                print(text, end="", flush=True)
                token_count += 1

            elif event_type == "done":
                print(f"\n\n[done] Tokens: {token_count}")
                break

            elif event_type == "error":
                print(f"\n[error] {data_obj.get('message') if isinstance(data_obj, dict) else data_obj}")
                break

    return captured_session_id


async def main():
    auth = httpx.BasicAuth(DEFAULT_USERNAME, DEFAULT_PASSWORD)
    cwd = str(PROJECT_ROOT)

    print("="*70)
    print("SIMPLE CONTINUOUS MESSAGING TEST")
    print("="*70)
    print(f"Working directory: {cwd}")
    print()

    async with httpx.AsyncClient(timeout=None, auth=auth) as client:
        # Send first message
        print("\n[STEP 1] Sending first message...")
        session_id = await send_message(
            client,
            "Count from 1 to 3, with brief explanations.",
            None,
            cwd
        )

        # Wait a bit to ensure first message is processing
        await asyncio.sleep(2)

        # Send second message to SAME session while it might still be processing
        print("\n[STEP 2] Sending second message to SAME session...")
        await send_message(
            client,
            "Now tell me: what is 5 + 5?",
            session_id,
            None
        )

        print("\n" + "="*70)
        print("[SUCCESS] Test completed!")
        print(f"Session ID: {session_id}")
        print("="*70)


if __name__ == "__main__":
    asyncio.run(main())
