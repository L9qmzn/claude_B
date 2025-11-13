# API 文档

FastAPI 服务启动后（默认 `http://127.0.0.1:8207`），提供以下接口：

## 1. `POST /chat`

- **功能**：与 Claude Agent SDK 建立流式对话（SSE）。
- **请求体**：
  ```json
  {
    "message": "第一条用户输入",
    "cwd": "C:/path/to/project"
  }
  ```
  或继续会话：
  ```json
  {
    "session_id": "session-uuid",
    "message": "继续对话",
    "cwd": "可选，需与原会话一致"
  }
  ```
- **响应**：`text/event-stream`，事件类型包含：
  - `session`：返回 `session_id`、`cwd`、`is_new`。
  - `token`：助手增量文本块。
  - `done`：本轮完成，含 `length`。
  - `error`：异常信息。

## 2. `GET /sessions`

- **功能**：列出所有已知主会话的元信息。
- **响应**：
  ```json
  [
    {
      "session_id": "...",
      "title": "会话标题",
      "cwd": "C:/path",
      "created_at": "ISO8601",
      "updated_at": "ISO8601",
      "message_count": 42
    }
  ]
  ```

## 3. `GET /sessions/{session_id}`

- **功能**：返回指定会话的完整信息。
- **响应**：
  ```json
  {
    "session_id": "...",
    "title": "...",
    "cwd": "...",
    "created_at": "ISO8601",
    "updated_at": "ISO8601",
    "messages": [
      {"role": "user", "content": "...", "timestamp": "ISO8601"},
      {"role": "assistant", "content": "...", "timestamp": "ISO8601"}
    ]
  }
  ```
  消息数据来自 `~/.claude/projects/<slug>/<session>.jsonl`。

## 4. `POST /sessions/load`

- **功能**：扫描 Claude Code 的存档目录，把所有主会话与 agent 子会话写入数据库。
- **请求体**（可选）：
  ```json
  {
    "claude_dir": "C:/Users/11988/.claude"
  }
  ```
  不传则使用 `config.yaml` 中的 `claude_dir`。
- **响应**：
  ```json
  {
    "claude_dir": "最终使用的目录",
    "sessions_loaded": 10,
    "agent_runs_loaded": 4
  }
  ```

## 5. 数据结构 / 配置

- `config.yaml`（根目录）：
  ```yaml
  claude_dir: C:/Users/11988/.claude
  sessions_db: ./sessions.db
  ```
  - `claude_dir`：Claude Code 项目的根目录（包含 `projects/`）。
  - `sessions_db`：SQLite 文件路径，支持绝对或相对路径。

- SQLite 表：
  - `sessions`：主会话（`session_id`、`title`、`cwd`、`created_at`、`updated_at`）。
  - `agent_sessions`：子 Agent（`agent_id`、`parent_session_id`、`title`、`cwd`、时间戳）。

## 6. CLI 辅助脚本

- `cc_B/read_session.py`：从 JSONL 读取指定会话历史。
- `cc_B/test_read_session_api.py`：调用 API 验证 `/sessions` 相关读操作。
