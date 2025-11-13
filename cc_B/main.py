from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Literal

import yaml

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    SystemMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
)

app = FastAPI(title="Claude Agent SDK Chat Backend (Streaming + Sessions + CWD)")

# =========================
# 内存中的会话结构
# =========================


class Session(BaseModel):
    session_id: str
    title: str
    cwd: str
    created_at: datetime
    updated_at: datetime
    messages: List[Dict[str, Any]] = Field(default_factory=list)


@dataclass
class SessionFileMetadata:
    session_id: str
    title: str
    cwd: str
    created_at: datetime
    updated_at: datetime
    parent_session_id: Optional[str] = None
    is_agent_run: bool = False


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


@contextmanager
def db_connection() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                cwd TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_sessions (
                agent_id TEXT PRIMARY KEY,
                parent_session_id TEXT,
                title TEXT NOT NULL,
                cwd TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(parent_session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
            )
            """
        )


def _dt_to_str(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _str_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_iso_timestamp(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)


def _cwd_to_project_slug(cwd: str) -> str:
    normalized = str(Path(cwd).resolve())
    return re.sub(r"[^0-9A-Za-z]", "-", normalized)


def _session_file_path(cwd: str, session_id: str) -> Path:
    slug = _cwd_to_project_slug(cwd)
    return CLAUDE_PROJECTS_DIR / slug / f"{session_id}.jsonl"


def _render_message_content(raw_content: object) -> str:
    if isinstance(raw_content, str):
        return raw_content

    if isinstance(raw_content, list):
        parts: List[str] = []
        for item in raw_content:
            if isinstance(item, str):
                parts.append(item)
                continue

            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "text":
                    parts.append(item.get("text", ""))
                elif item_type == "tool_use":
                    name = item.get("name", "tool")
                    payload = json.dumps(item.get("input"), ensure_ascii=False)
                    parts.append(f"[tool_use:{name}] {payload}")
                elif item_type == "tool_result":
                    content = item.get("content")
                    if isinstance(content, list):
                        parts.append("\n".join(str(child) for child in content))
                    elif content is not None:
                        parts.append(f"[tool_result] {content}")
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))

        return "\n".join(part for part in parts if part)

    if isinstance(raw_content, dict):
        if raw_content.get("type") == "text":
            return raw_content.get("text", "")
        return json.dumps(raw_content, ensure_ascii=False)

    if raw_content is None:
        return ""

    return str(raw_content)


def _jsonify(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonify(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonify(item) for item in value]
    if is_dataclass(value):
        return _jsonify(asdict(value))
    if hasattr(value, "__dict__"):
        data = {
            key: val
            for key, val in vars(value).items()
            if not key.startswith("_")
        }
        if data:
            return {key: _jsonify(val) for key, val in data.items()}
    return str(value)


def _serialize_content_block(block: Any) -> Dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {
            "type": "thinking",
            "thinking": block.thinking,
            "signature": block.signature,
        }
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": _jsonify(block.input),
        }
    if isinstance(block, ToolResultBlock):
        payload: Dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
        }
        if block.content is not None:
            payload["content"] = _jsonify(block.content)
        if block.is_error is not None:
            payload["is_error"] = block.is_error
        return payload
    if isinstance(block, dict):
        return {str(key): _jsonify(val) for key, val in block.items()}
    serialized = _jsonify(block)
    if isinstance(serialized, dict):
        return serialized
    return {
        "type": "unknown",
        "value": serialized,
    }


def _serialize_sdk_message(message: Any) -> Optional[Dict[str, Any]]:
    if isinstance(message, SystemMessage):
        payload: Dict[str, Any] = {
            "type": "system",
            "subtype": message.subtype,
            "data": _jsonify(message.data),
        }
        session_id = message.data.get("session_id")
        if isinstance(session_id, str):
            payload["session_id"] = session_id
        return payload

    if isinstance(message, AssistantMessage):
        payload: Dict[str, Any] = {
            "type": "assistant",
            "model": message.model,
            "content": [_serialize_content_block(block) for block in message.content],
        }
        if message.parent_tool_use_id is not None:
            payload["parent_tool_use_id"] = message.parent_tool_use_id
        return payload

    if isinstance(message, ResultMessage):
        payload: Dict[str, Any] = {
            "type": "result",
            "subtype": message.subtype,
            "duration_ms": message.duration_ms,
            "duration_api_ms": message.duration_api_ms,
            "is_error": message.is_error,
            "num_turns": message.num_turns,
            "session_id": message.session_id,
        }
        if message.total_cost_usd is not None:
            payload["total_cost_usd"] = message.total_cost_usd
        if message.usage is not None:
            payload["usage"] = _jsonify(message.usage)
        if message.result is not None:
            payload["result"] = message.result
        return payload

    if isinstance(message, dict):
        return {str(key): _jsonify(val) for key, val in message.items()}

    return None


def _dump_sdk_message(message: Any) -> Optional[Dict[str, Any]]:
    payload = _serialize_sdk_message(message)
    if payload is not None:
        return payload

    serialized = _jsonify(message)
    if isinstance(serialized, dict):
        return serialized

    if serialized is not None:
        return {
            "type": type(message).__name__,
            "value": serialized,
        }

    return None


def _log_sdk_message(label: str, payload: Dict[str, Any]) -> None:
    try:
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        serialized = str(payload)
    print(f"[ClaudeSDK:{label}]\n{serialized}\n", flush=True)


def _iter_session_records(cwd: str, session_id: str) -> Iterator[Dict[str, Any]]:
    path = _session_file_path(cwd, session_id)
    if not path.exists():
        return

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield record
    except OSError:
        return


def load_session_messages_from_jsonl(cwd: str, session_id: str) -> List[Dict[str, Any]]:
    return list(_iter_session_records(cwd, session_id))


def count_session_messages(cwd: str, session_id: str) -> int:
    count = 0
    for record in _iter_session_records(cwd, session_id) or []:
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role in ("user", "assistant"):
            count += 1
    return count


def _is_agent_session_file(path: Path) -> bool:
    return path.stem.startswith("agent-")


def _iter_session_files_from_claude(
    claude_root: Path, include_agent_runs: bool = True
) -> Iterator[Path]:
    projects_dir = claude_root / "projects"
    if not projects_dir.exists():
        return
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl_file in project_dir.glob("*.jsonl"):
            if not include_agent_runs and _is_agent_session_file(jsonl_file):
                continue
            yield jsonl_file


def _extract_session_metadata_from_file(path: Path) -> Optional[SessionFileMetadata]:
    session_id = path.stem
    is_agent_run = _is_agent_session_file(path)
    cwd: Optional[str] = None
    first_user_text: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    parent_session_id: Optional[str] = None

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                timestamp_raw = record.get("timestamp")
                if timestamp_raw:
                    ts = _parse_iso_timestamp(timestamp_raw)
                    if created_at is None or ts < created_at:
                        created_at = ts
                    if updated_at is None or ts > updated_at:
                        updated_at = ts

                if not cwd:
                    cwd = record.get("cwd")
                    if not cwd:
                        message_ctx = record.get("message") or {}
                        cwd = message_ctx.get("cwd")

                if parent_session_id is None:
                    parent_session_id = record.get("sessionId")

                if not first_user_text:
                    message = record.get("message") or {}
                    if message.get("role") == "user":
                        content = _render_message_content(message.get("content")).strip()
                        if content:
                            first_user_text = content
    except OSError:
        return None

    if not cwd:
        return None

    created_at = created_at or datetime.now(timezone.utc)
    updated_at = updated_at or created_at
    title = (first_user_text or session_id).strip() or session_id
    if len(title) > 30:
        title = title[:30] + "..."

    return SessionFileMetadata(
        session_id=session_id,
        title=title,
        cwd=cwd,
        created_at=created_at,
        updated_at=updated_at,
        parent_session_id=parent_session_id,
        is_agent_run=is_agent_run,
    )


def _discover_session_metadata_from_files(
    claude_root: Path,
) -> Iterator[SessionFileMetadata]:
    for session_file in _iter_session_files_from_claude(claude_root) or []:
        metadata = _extract_session_metadata_from_file(session_file)
        if metadata:
            yield metadata


def fetch_session(session_id: str, include_messages: bool = True) -> Optional[Session]:
    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT session_id, title, cwd, created_at, updated_at
            FROM sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

        if row is None:
            return None

        messages: List[Dict[str, Any]] = []
        if include_messages:
            messages = load_session_messages_from_jsonl(
                row["cwd"], row["session_id"]
            )

    return Session(
        session_id=row["session_id"],
        title=row["title"],
        cwd=row["cwd"],
        created_at=_str_to_dt(row["created_at"]),
        updated_at=_str_to_dt(row["updated_at"]),
        messages=messages,
    )


def list_session_summaries() -> List[SessionSummary]:
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT session_id, title, cwd, created_at, updated_at
            FROM sessions
            ORDER BY datetime(updated_at) DESC
            """
        ).fetchall()

    return [
        SessionSummary(
            session_id=row["session_id"],
            title=row["title"],
            cwd=row["cwd"],
            created_at=_str_to_dt(row["created_at"]),
            updated_at=_str_to_dt(row["updated_at"]),
            message_count=count_session_messages(row["cwd"], row["session_id"]),
        )
        for row in rows
    ]


def persist_session_metadata(
    *,
    session_id: str,
    title: str,
    cwd: str,
    created_at: datetime,
    updated_at: datetime,
) -> None:
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO sessions (session_id, title, cwd, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                title = excluded.title,
                cwd = excluded.cwd,
                created_at = CASE
                    WHEN sessions.created_at IS NULL THEN excluded.created_at
                    WHEN excluded.created_at < sessions.created_at THEN excluded.created_at
                    ELSE sessions.created_at
                END,
                updated_at = CASE
                    WHEN sessions.updated_at IS NULL THEN excluded.updated_at
                    WHEN excluded.updated_at > sessions.updated_at THEN excluded.updated_at
                    ELSE sessions.updated_at
                END
            """,
            (
                session_id,
                title,
                cwd,
                _dt_to_str(created_at),
                _dt_to_str(updated_at),
            ),
        )


def persist_agent_session_metadata(
    *,
    agent_id: str,
    parent_session_id: Optional[str],
    title: str,
    cwd: str,
    created_at: datetime,
    updated_at: datetime,
) -> None:
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO agent_sessions (agent_id, parent_session_id, title, cwd, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                parent_session_id = COALESCE(excluded.parent_session_id, agent_sessions.parent_session_id),
                title = excluded.title,
                cwd = excluded.cwd,
                created_at = CASE
                    WHEN agent_sessions.created_at IS NULL THEN excluded.created_at
                    WHEN excluded.created_at < agent_sessions.created_at THEN excluded.created_at
                    ELSE agent_sessions.created_at
                END,
                updated_at = CASE
                    WHEN agent_sessions.updated_at IS NULL THEN excluded.updated_at
                    WHEN excluded.updated_at > agent_sessions.updated_at THEN excluded.updated_at
                    ELSE agent_sessions.updated_at
                END
            """,
            (
                agent_id,
                parent_session_id,
                title,
                cwd,
                _dt_to_str(created_at),
                _dt_to_str(updated_at),
            ),
        )


def bootstrap_sessions_from_files(claude_dir: Optional[str] = None) -> Dict[str, int]:
    root = Path(claude_dir).expanduser() if claude_dir else CLAUDE_ROOT
    if not root.exists():
        raise FileNotFoundError(f"Claude directory does not exist: {root}")

    stats = {"sessions": 0, "agent_runs": 0}

    primary_sessions: List[SessionFileMetadata] = []
    agent_sessions: List[SessionFileMetadata] = []

    for metadata in _discover_session_metadata_from_files(root):
        if metadata.is_agent_run:
            agent_sessions.append(metadata)
        else:
            primary_sessions.append(metadata)

    existing_session_ids: set[str] = set()
    with db_connection() as conn:
        rows = conn.execute("SELECT session_id FROM sessions").fetchall()
        existing_session_ids.update(row["session_id"] for row in rows)

    for metadata in primary_sessions:
        persist_session_metadata(
            session_id=metadata.session_id,
            title=metadata.title,
            cwd=metadata.cwd,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
        )
        stats["sessions"] += 1
        existing_session_ids.add(metadata.session_id)

    for metadata in agent_sessions:
        parent_session_id = (
            metadata.parent_session_id
            if metadata.parent_session_id in existing_session_ids
            else None
        )
        persist_agent_session_metadata(
            agent_id=metadata.session_id,
            parent_session_id=parent_session_id,
            title=metadata.title,
            cwd=metadata.cwd,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
        )
        stats["agent_runs"] += 1

    return stats


init_db()
bootstrap_sessions_from_files()


# =========================
# 对外请求 / 响应模型
# =========================


class ChatRequest(BaseModel):
    """
    /chat 请求体：

    - 新会话：
        { "message": "...", "cwd": "/path/to/project" }
    - 继续会话：
        { "session_id": "xxx", "message": "..." }
      （可选的 cwd 如果传了，必须等于原会话的 cwd）
    """

    session_id: Optional[str] = None
    cwd: Optional[str] = None
    message: str
    permission_mode: Literal["default", "plan", "acceptEdits", "bypassPermissions"] = "default"


class SessionSummary(BaseModel):
    session_id: str
    title: str
    cwd: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class LoadSessionsRequest(BaseModel):
    claude_dir: Optional[str] = None


# =========================
# 工具函数
# =========================


def format_sse(event: str, data: dict) -> str:
    """
    把事件打成 SSE 格式：
      event: <event>
      data: <json>

      （空行分隔事件）
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# =========================
# 会话相关接口（非流式）
# =========================


@app.get("/sessions", response_model=List[SessionSummary])
async def list_sessions() -> List[SessionSummary]:
    """
    列出当前进程里所有已知会话（包含 cwd）。
    """
    return list_session_summaries()


@app.get("/sessions/{session_id}", response_model=Session)
async def get_session(session_id: str) -> Session:
    """
    返回某个会话的详细信息（包含 cwd 和历史消息）。
    """
    sess = fetch_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


@app.post("/sessions/load")
async def load_sessions(body: LoadSessionsRequest):
    try:
        stats = bootstrap_sessions_from_files(body.claude_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_root = (
        Path(body.claude_dir).expanduser() if body.claude_dir else CLAUDE_ROOT
    )

    return {
        "claude_dir": str(resolved_root),
        "sessions_loaded": stats["sessions"],
        "agent_runs_loaded": stats["agent_runs"],
    }


# =========================
# 核心：流式聊天接口 /chat
# =========================

@app.post("/chat")
async def chat(body: ChatRequest):
    """
    流式聊天接口（SSE）：

    前端调用示例（浏览器原生 EventSource）：

        const es = new EventSource("/chat", {
          // FastAPI + SSE 默认只能 GET，这里我们用 fetch + ReadableStream 示例更方便。
        })

    实际推荐用 fetch / Axios + ReadableStream，在前端手动解析 SSE。
    但为了简单，这里只关心后端输出的 SSE 格式。
    """

    # ------- 1. 校验 cwd / 会话逻辑 -------

    now = datetime.now(timezone.utc)

    is_new_session = body.session_id is None

    existing_session: Optional[Session] = None
    final_cwd: str

    if is_new_session:
        # 新会话必须带 cwd
        if not body.cwd:
            raise HTTPException(
                status_code=400,
                detail="cwd is required when starting a new session",
            )
        # 可选：校验目录是否存在
        if not Path(body.cwd).is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"cwd does not exist or is not a directory: {body.cwd}",
            )
        final_cwd = str(Path(body.cwd).resolve())
    else:
        assert body.session_id is not None
        existing_session = fetch_session(body.session_id, include_messages=False)
        if not existing_session:
            raise HTTPException(status_code=404, detail="Session not found")

        # 如果请求里额外带了 cwd，则必须与会话中的一致
        if body.cwd and Path(body.cwd).resolve() != Path(existing_session.cwd).resolve():
            raise HTTPException(
                status_code=400,
                detail="cwd mismatch for existing session",
            )
        final_cwd = existing_session.cwd

    # ------- 2. 定义异步生成器，输出 SSE -------

    async def event_stream():
        """
        真正做事的地方：调用 Agent SDK，并一边迭代、一边往前端推 SSE。
        SSE 事件设计：
          - session: 会话初始化信息（拿到 session_id 时发一次）
          - token:   生成中的文本块（前端用于逐字显示）
          - done:    一轮完成（包含简单元信息）
          - error:   发生异常
        """
        session_id: Optional[str] = body.session_id  # 继续会话时一开始就有
        assistant_chunks: List[str] = []

        # 这轮对话的 user message 先存起来，最后写入会话历史
        user_message_text = body.message

        try:
            # 所有 options 都固定加入 setting_sources=["user"]（你的要求）
            # 这样会从 ~/.claude/settings.json 读取用户级设置。:contentReference[oaicite:1]{index=1}
            options = ClaudeAgentOptions(
                resume=body.session_id,
                cwd=final_cwd,
                include_partial_messages=True,  # 尽量细粒度流式输出:contentReference[oaicite:2]{index=2}
                setting_sources=["user"],
                permission_mode=body.permission_mode,
            )

            # 调用 Agent SDK，单轮 query 返回 AsyncIterator[Message]
            async for message in query(prompt=user_message_text, options=options):
                # SystemMessage(subtype="init") 里通常带 cwd / session_id / tools 等元数据:contentReference[oaicite:3]{index=3}
                if isinstance(message, SystemMessage):
                    raw_payload = _dump_sdk_message(message)
                    if raw_payload is not None:
                        _log_sdk_message(type(message).__name__, raw_payload)
                        payload_session_id = raw_payload.get("session_id") or session_id
                        yield format_sse(
                            "message",
                            {
                                "session_id": payload_session_id,
                                "payload": raw_payload,
                            },
                        )
                    else:
                        _log_sdk_message(
                            type(message).__name__,
                            {"__repr__": repr(message)},
                        )
                    if message.subtype == "init":
                        # 新会话时，从 data 里拿 session_id
                        if session_id is None:
                            session_id = message.data.get("session_id")

                            # 通知前端：会话已建立
                            yield format_sse(
                                "session",
                                {
                                    "session_id": session_id,
                                    "cwd": final_cwd,
                                    "is_new": True,
                                },
                            )
                        else:
                            # 继续会话时也可以回一发，方便前端确认 cwd / id
                            yield format_sse(
                                "session",
                                {
                                    "session_id": session_id,
                                    "cwd": final_cwd,
                                    "is_new": False,
                                },
                            )

                # 助手文本块（可能有多条 / partial updates）
                if isinstance(message, AssistantMessage):
                    raw_payload = _dump_sdk_message(message)
                    if raw_payload is not None:
                        _log_sdk_message(type(message).__name__, raw_payload)
                        payload_session_id = raw_payload.get("session_id") or session_id
                        yield format_sse(
                            "message",
                            {
                                "session_id": payload_session_id,
                                "payload": raw_payload,
                            },
                        )
                    else:
                        _log_sdk_message(
                            type(message).__name__,
                            {"__repr__": repr(message)},
                        )
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            chunk = block.text
                            if not chunk:
                                continue
                            assistant_chunks.append(chunk)

                            # 把增量文本发给前端
                            yield format_sse(
                                "token",
                                {
                                    "session_id": session_id,
                                    "text": chunk,
                                },
                            )

                # ResultMessage 是最终结果 + 使用情况 + session_id:contentReference[oaicite:4]{index=4}
                if isinstance(message, ResultMessage):
                    raw_payload = _dump_sdk_message(message)
                    if raw_payload is not None:
                        _log_sdk_message(type(message).__name__, raw_payload)
                        payload_session_id = raw_payload.get("session_id") or session_id
                        yield format_sse(
                            "message",
                            {
                                "session_id": payload_session_id,
                                "payload": raw_payload,
                            },
                        )
                    else:
                        _log_sdk_message(
                            type(message).__name__,
                            {"__repr__": repr(message)},
                        )
                    # 确认最终 session_id
                    if session_id is None:
                        session_id = message.session_id

                        # 告知前端会话信息
                        yield format_sse(
                            "session",
                            {
                                "session_id": session_id,
                                "cwd": final_cwd,
                                "is_new": is_new_session,
                            },
                        )

                    # 可以选择使用 message.result 覆盖最终文本，
                    # 不过我们已经流式发过 chunks 了，这里主要用于保存历史。
                    if message.result is not None and not assistant_chunks:
                        assistant_chunks.append(message.result)

            # -------- 3. 一轮结束：更新持久化的会话历史 --------

            if session_id is None:
                # 理论上 query 正常返回时一定会有 session_id
                raise RuntimeError("Claude did not return session_id")

            full_assistant_text = "".join(assistant_chunks)

            if existing_session is None:
                # 新会话：用第一条 user message 当 title（截断一下）
                title = user_message_text.strip() or "新会话"
                if len(title) > 30:
                    title = title[:30] + "..."
            else:
                title = existing_session.title

            persist_session_metadata(
                session_id=session_id,
                title=title,
                cwd=final_cwd,
                created_at=now,
                updated_at=now,
            )

            # -------- 4. 通知前端完成 --------
            yield format_sse(
                "done",
                {
                    "session_id": session_id,
                    "cwd": final_cwd,
                    "length": len(full_assistant_text),
                },
            )

        except Exception as e:
            # 出错时发送 error 事件，方便前端提示
            yield format_sse(
                "error",
                {
                    "message": str(e),
                },
            )

    # 返回 StreamingResponse，Content-Type 为 text/event-stream
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 反向代理时防止缓冲
        },
    )


# =========================
# 方便本地启动
# =========================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8207, reload=False)
