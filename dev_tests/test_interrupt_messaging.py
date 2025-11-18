"""
Test sending a new message while the first message is still streaming.
This tests the TRUE continuous messaging capability.
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


async def test_interrupt():
    """Test interrupting a streaming response with a new message."""
    auth = httpx.BasicAuth(DEFAULT_USERNAME, DEFAULT_PASSWORD)
    cwd = str(PROJECT_ROOT)

    print("="*70)
    print("TEST: Interrupting Streaming with New Message")
    print("="*70)
    print(f"Working directory: {cwd}")
    print()

    url = "http://127.0.0.1:8207/chat"

    # First message: ask for a long response
    payload1 = {
        "message": "Count from 1 to 20, explaining each number in detail.",
        "cwd": cwd
    }

    session_id = None
    run_id_1 = None
    token_count_1 = 0
    second_message_sent = False

    async with httpx.AsyncClient(timeout=None, auth=auth) as client:
        print("[STEP 1] Sending first message (expecting long response)...")
        print("-"*70)

        async with client.stream("POST", url, json=payload1) as resp1:
            resp1.raise_for_status()
            run_id_1 = resp1.headers.get("X-Claude-Run-Id")
            print(f"Run ID: {run_id_1}\n")

            event_type = None
            async for raw_line in resp1.aiter_lines():
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
                    session_id = data_obj.get("session_id")
                    print(f"[session] ID={session_id}\n")

                elif event_type == "token" and isinstance(data_obj, dict):
                    text = data_obj.get("text", "")
                    print(text, end="", flush=True)
                    token_count_1 += 1

                    # After receiving some tokens, send second message!
                    if not second_message_sent and token_count_1 >= 30:
                        second_message_sent = True
                        print("\n\n" + "*"*70)
                        print("* [STEP 2] INTERRUPTING! Sending second message NOW!")
                        print("*"*70 + "\n")

                        # Send second message in parallel
                        payload2 = {
                            "message": "Stop counting! Just tell me what is 7 x 8?",
                            "session_id": session_id
                        }

                        asyncio.create_task(send_second_message(client, url, payload2))

                elif event_type == "done":
                    print(f"\n\n[done] First stream complete. Tokens: {token_count_1}")
                    break

                elif event_type == "error":
                    print(f"\n[error] {data_obj.get('message') if isinstance(data_obj, dict) else data_obj}")
                    break

    print("\n" + "="*70)
    print("[SUCCESS] Test completed!")
    print(f"Session ID: {session_id}")
    print(f"First stream tokens before interrupt: {token_count_1}")
    print("="*70)


async def send_second_message(client: httpx.AsyncClient, url: str, payload: dict):
    """Send the second message and monitor its stream."""
    print("[conn-2] Sending second message...\n")

    try:
        async with client.stream("POST", url, json=payload) as resp2:
            resp2.raise_for_status()
            run_id = resp2.headers.get("X-Claude-Run-Id")
            print(f"[conn-2] Run ID: {run_id}")
            print(f"[conn-2] Streaming response:\n")

            event_type = None
            token_count = 0

            async for raw_line in resp2.aiter_lines():
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

                if event_type == "token" and isinstance(data_obj, dict):
                    text = data_obj.get("text", "")
                    print(text, end="", flush=True)
                    token_count += 1

                elif event_type == "done":
                    print(f"\n\n[conn-2:done] Second stream complete! Tokens: {token_count}\n")
                    break

                elif event_type == "error":
                    print(f"\n[conn-2:error] {data_obj.get('message') if isinstance(data_obj, dict) else data_obj}\n")
                    break

    except Exception as e:
        print(f"\n[conn-2:exception] {e}\n")


if __name__ == "__main__":
    asyncio.run(test_interrupt())
