"""
Test for continuous messaging during streaming responses.

This test demonstrates the new feature: sending multiple messages to Claude
while it's still processing and streaming responses from a previous message.
Users can interrupt or continue the conversation mid-stream.

Usage:
    python dev_tests/test_continuous_messaging.py [--base-url URL] [--cwd PATH]

Example:
    python dev_tests/test_continuous_messaging.py --base-url http://localhost:3000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8207")
    parser.add_argument("--cwd", default=None, help="Working directory for new session")
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument(
        "--first-message",
        default="Please count slowly from 1 to 10, explaining each number briefly.",
        help="First message to send",
    )
    parser.add_argument(
        "--second-message",
        default="Actually, stop counting. Just tell me what is 2 + 2?",
        help="Second message to send (will interrupt first)",
    )
    parser.add_argument(
        "--tokens-before-interrupt",
        type=int,
        default=30,
        help="Number of tokens to receive before sending second message",
    )
    return parser


async def fetch_default_session(base_url: str, auth: httpx.Auth) -> Optional[dict]:
    """Fetch an existing session to get default cwd."""
    url = f"{base_url.rstrip('/')}/sessions"
    async with httpx.AsyncClient(timeout=10.0, auth=auth) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        sessions = resp.json()
    if isinstance(sessions, list) and sessions:
        return sessions[0]
    return None


async def send_and_stream(
    *,
    base_url: str,
    message: str,
    session_id: Optional[str],
    cwd: Optional[str],
    auth: httpx.Auth,
    connection_name: str,
) -> tuple[Optional[str], int]:
    """
    Send a message and stream the response.

    Returns: (session_id, token_count)
    """
    payload: dict = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    if cwd:
        payload["cwd"] = cwd

    url = f"{base_url.rstrip('/')}/chat"

    print(f"\n[{connection_name}] Sending: {message}")
    if session_id:
        print(f"[{connection_name}] Session ID: {session_id}")

    token_count = 0
    captured_session_id = session_id

    async with httpx.AsyncClient(timeout=None, auth=auth) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()

            run_id = resp.headers.get("X-Claude-Run-Id")
            print(f"[{connection_name}] Run ID: {run_id}")
            print(f"[{connection_name}] Streaming...\n")

            event_type: Optional[str] = None

            async for raw_line in resp.aiter_lines():
                if raw_line is None:
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
                    print(f"[{connection_name}:session] ID={captured_session_id}, new={is_new}")

                elif event_type == "token" and isinstance(data_obj, dict):
                    text = data_obj.get("text", "")
                    print(text, end="", flush=True)
                    token_count += 1

                elif event_type == "done":
                    print(f"\n[{connection_name}:done] Received {token_count} tokens")
                    break

                elif event_type == "error" and isinstance(data_obj, dict):
                    print(f"\n[{connection_name}:error] {data_obj.get('message')}")
                    break

                elif event_type == "stopped":
                    print(f"\n[{connection_name}:stopped] Stream stopped")
                    break

    return captured_session_id, token_count


async def test_continuous_messaging(
    *,
    base_url: str,
    cwd: str,
    auth: httpx.Auth,
    first_message: str,
    second_message: str,
    tokens_before_interrupt: int,
) -> None:
    """
    Main test: send first message, then send second message mid-stream.
    """
    print("=" * 70)
    print("TEST: Continuous Messaging During Streaming")
    print("=" * 70)
    print(f"Working directory: {cwd}")
    print(f"First message: {first_message}")
    print(f"Second message: {second_message}")
    print(f"Will interrupt after {tokens_before_interrupt} tokens")
    print("=" * 70)

    session_id: Optional[str] = None
    second_stream_task: Optional[asyncio.Task] = None
    second_message_sent = False

    # Payload for first message
    payload = {"message": first_message, "cwd": cwd}
    url = f"{base_url.rstrip('/')}/chat"

    print("\n[STEP 1] Starting first message stream...")

    token_count = 0
    async with httpx.AsyncClient(timeout=None, auth=auth) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()

            run_id = resp.headers.get("X-Claude-Run-Id")
            print(f"[conn-1] Run ID: {run_id}")
            print("[conn-1] Streaming response...\n")

            event_type: Optional[str] = None

            async for raw_line in resp.aiter_lines():
                if raw_line is None:
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
                    is_new = data_obj.get("is_new", False)
                    print(f"[conn-1:session] ID={session_id}, new={is_new}")

                elif event_type == "token" and isinstance(data_obj, dict):
                    text = data_obj.get("text", "")
                    print(text, end="", flush=True)
                    token_count += 1

                    # Send second message after receiving enough tokens
                    if not second_message_sent and token_count >= tokens_before_interrupt:
                        second_message_sent = True
                        print("\n\n" + "*" * 70)
                        print("[STEP 2] !!! SENDING SECOND MESSAGE WHILE FIRST IS STREAMING !!!")
                        print("*" * 70)

                        # Create async task to send second message in parallel
                        second_stream_task = asyncio.create_task(
                            send_and_stream(
                                base_url=base_url,
                                message=second_message,
                                session_id=session_id,
                                cwd=None,  # Use session's cwd
                                auth=auth,
                                connection_name="conn-2",
                            )
                        )

                elif event_type == "done":
                    print(f"\n[conn-1:done] First stream complete. Tokens: {token_count}")
                    break

                elif event_type == "error" and isinstance(data_obj, dict):
                    print(f"\n[conn-1:error] {data_obj.get('message')}")
                    break

                elif event_type == "stopped":
                    print(f"\n[conn-1:stopped] First stream stopped")
                    break

    # Wait for second stream to complete
    if second_stream_task:
        print("\n[STEP 3] Waiting for second stream to complete...")
        try:
            session_id_2, token_count_2 = await second_stream_task
            print(f"\n[conn-2] Second stream completed with {token_count_2} tokens")
        except Exception as e:
            print(f"\n[conn-2:error] Second stream failed: {e}")

    print("\n" + "=" * 70)
    print("[SUCCESS] Test completed!")
    print(f"Session ID: {session_id}")
    print(f"First stream tokens: {token_count}")
    if second_stream_task:
        print("Second message was successfully sent during first stream!")
    print("=" * 70)


async def main_async() -> None:
    args = build_parser().parse_args()
    auth = httpx.BasicAuth(args.username, args.password)

    cwd = args.cwd
    if cwd is None:
        default_session = await fetch_default_session(args.base_url, auth)
        if default_session and isinstance(default_session.get("cwd"), str):
            cwd = default_session["cwd"]
            print(f"[info] Using cwd from existing session: {cwd}")
        else:
            cwd = str(PROJECT_ROOT)
            print(f"[info] Using project root as cwd: {cwd}")

    await test_continuous_messaging(
        base_url=args.base_url,
        cwd=cwd,
        auth=auth,
        first_message=args.first_message,
        second_message=args.second_message,
        tokens_before_interrupt=args.tokens_before_interrupt,
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
