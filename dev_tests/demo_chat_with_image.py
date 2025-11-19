"""Demo script for sending a chat message with an image to the Claude backend."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
import yaml

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Warning: PIL/Pillow not installed. Using fallback image generation.")
    Image = None

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


def create_test_image() -> bytes:
    """Create a simple test image with PIL or return a minimal PNG."""
    if Image is None:
        # Minimal 1x1 red PNG (if PIL not available)
        # This is a valid 1x1 red PNG in base64
        minimal_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
        )
        return minimal_png

    # Create a 400x300 image with a gradient and text
    img = Image.new("RGB", (400, 300), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)

    # Draw a gradient background
    for y in range(300):
        color = int(255 * (y / 300))
        draw.rectangle([(0, y), (400, y + 1)], fill=(100, 150, color))

    # Draw some shapes
    draw.ellipse([50, 50, 150, 150], fill=(255, 100, 100), outline=(200, 50, 50))
    draw.rectangle([200, 100, 350, 200], fill=(100, 255, 100), outline=(50, 200, 50))

    # Add text
    try:
        # Try to use a default font
        font = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        # Fall back to default font
        font = ImageFont.load_default()

    draw.text((50, 220), "Test Image for Claude", fill=(0, 0, 0), font=font)

    # Convert to bytes
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def image_to_base64(image_bytes: bytes) -> str:
    """Convert image bytes to base64 string."""
    return base64.b64encode(image_bytes).decode("utf-8")


def load_image_from_file(image_path: str) -> bytes:
    """Load an image from a file."""
    with open(image_path, "rb") as f:
        return f.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8207")
    parser.add_argument(
        "--text",
        default="请描述这张图片中的内容",
        help="Text message to send with the image",
    )
    parser.add_argument(
        "--image",
        default=None,
        help="Path to an image file. If not provided, a test image will be generated.",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory (defaults to project root)",
    )
    parser.add_argument("--session-id", default=None, help="Session ID to continue")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="HTTP Basic username")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="HTTP Basic password")
    parser.add_argument(
        "--permission-mode",
        default="default",
        choices=["default", "plan", "acceptEdits", "bypassPermissions"],
        help="Permission mode",
    )
    return parser


async def stream_chat_with_image(
    *,
    base_url: str,
    text: str,
    image_base64: str,
    cwd: str,
    permission_mode: str,
    session_id: Optional[str],
    auth: httpx.Auth,
) -> str:
    """Send a chat message with an image and stream the response."""
    # Build message content with both text and image
    message_content = [
        {"type": "text", "text": text},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": image_base64,
            },
        },
    ]

    payload = {
        "message": message_content,
        "permission_mode": permission_mode,
    }

    if session_id:
        payload["session_id"] = session_id
    else:
        payload["cwd"] = cwd

    url = f"{base_url.rstrip('/')}/chat"
    print(f"[POST] {url}")
    print(f"    Sending message with text and image")
    print(f"    Text: {text}")
    print(f"    Image size: {len(image_base64)} bytes (base64)")
    print(f"    Payload keys: {list(payload.keys())}")
    print(f"    Message type: {type(payload['message'])}")
    print(f"    Message length: {len(payload['message'])}\n")

    final_session_id: Optional[str] = session_id
    collected_text: list[str] = []

    async with httpx.AsyncClient(timeout=None, auth=auth) as client:
        async with client.stream("POST", url, json=payload) as resp:
            if resp.status_code != 200:
                error_text = await resp.aread()
                print(f"[ERROR] Status: {resp.status_code}")
                print(f"[ERROR] Response: {error_text.decode('utf-8', errors='replace')}")
                resp.raise_for_status()
            print("[STREAM] Streaming events:\n")

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

                if event_type == "session":
                    final_session_id = data_obj.get("session_id") or final_session_id
                    print(f"data: {json.dumps(data_obj, ensure_ascii=False)}")
                elif event_type == "token":
                    chunk = data_obj.get("text") or ""
                    collected_text.append(chunk)
                    print(chunk, end="", flush=True)
                elif event_type == "message":
                    # Skip detailed message payload for cleaner output
                    payload_session = data_obj.get("session_id")
                    print(f"\n   -> message for session {payload_session}")
                elif event_type == "done":
                    print(f"\ndata: {json.dumps(data_obj, ensure_ascii=False)}")
                elif event_type == "error":
                    print(f"\ndata: {json.dumps(data_obj, ensure_ascii=False)}")
                    raise RuntimeError(f"Server error: {data_obj}")
                elif event_type == "run":
                    print(f"data: {json.dumps(data_obj, ensure_ascii=False)}")

                if event_type not in ("token", "message"):
                    print()

    if not final_session_id:
        raise RuntimeError("Did not receive session_id from /chat stream")

    print("\n[OK] Stream completed")
    print(f"   session_id = {final_session_id}")
    if collected_text:
        full_text = "".join(collected_text)
        print(f"   Total response length: {len(full_text)} characters")
    print()

    return final_session_id


async def main_async() -> None:
    args = build_parser().parse_args()

    auth = httpx.BasicAuth(args.username, args.password)

    # Load or generate image
    if args.image:
        print(f"[IMAGE] Loading image from: {args.image}")
        image_bytes = load_image_from_file(args.image)
    else:
        print("[IMAGE] Generating test image...")
        image_bytes = create_test_image()

    image_base64 = image_to_base64(image_bytes)
    print(f"   Image encoded to base64: {len(image_base64)} characters\n")

    # Determine working directory
    cwd = args.cwd or str(PROJECT_ROOT)

    session_id = await stream_chat_with_image(
        base_url=args.base_url,
        text=args.text,
        image_base64=image_base64,
        cwd=cwd,
        permission_mode=args.permission_mode,
        session_id=args.session_id,
        auth=auth,
    )

    print(f"Session ID: {session_id}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
