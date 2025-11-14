# API 文档

FastAPI 服务默认监听 `http://127.0.0.1:8207`（可在 `config.yaml` 中调整端口）。以下文档说明所有公开接口及其输入输出格式。

## 1. `POST /chat`

- **功能**：与 Claude Agent SDK 建立流式（SSE）对话。
- **请求体（新会话）**：
  ```json
  {
    "message": "第一条用户输入",
    "cwd": "C:/path/to/project"
  }
  ```
- **请求体（继续会话）**：
  ```json
  {
    "session_id": "session-uuid",
    "message": "继续对话"
  }
  ```
- **参数说明**：
  - `permission_mode`（可选，默认 `default`）：`default` / `plan` / `acceptEdits` / `bypassPermissions`，直接透传至 `ClaudeAgentOptions.permission_mode`。
  - `system_prompt`（可选，默认 Claude Code 预设）：可为字符串，或 `{ "type": "preset", "preset": "claude_code" }`，透传至 `ClaudeAgentOptions.system_prompt`。
- **响应**：`text/event-stream`。事件类型：
  - `session`：返回当前 `session_id`、`cwd`、`is_new`。
  - `token`：助手增量文本，便于前端即时渲染。
  - `message`：**完整透传** Claude Agent SDK 的原始消息字典（可能是 `system` / `user` / `assistant` / `result` / `stream_event` 等类型），不再改写角色或内容，方便前端按 Claude Code 原语义处理。
  - `done`：单轮完成事件，附带 `length` 等元信息。
  - `error`：异常事件。

## 2. `GET /sessions`

- **功能**：列出所有已知主会话的概要信息。
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

- **功能**：返回指定会话的完整信息及消息列表。
- **响应**：
  ```json
  {
    "session_id": "...",
    "title": "...",
    "cwd": "...",
    "created_at": "ISO8601",
    "updated_at": "ISO8601",
    "messages": [
      { "type": "system", ... },
      { "type": "user", ... },
      { "type": "assistant", ... }
    ]
  }
  ```
  `messages` 数组对应 Claude Code JSONL 文件的逐行记录，原样返回，包含工具调用、引用、流事件等全部字段（文件位于 `~/.claude/projects/<slug>/<session>.jsonl`）。

## 4. `POST /sessions/load`

- **功能**：扫描 Claude Code 存档目录，将主会话与 Agent 子会话写入数据库。
- **请求体（可选）**：
  ```json
  {
    "claude_dir": "C:/Users/11988/.claude"
  }
  ```
- **响应**：
  ```json
  {
    "claude_dir": "最终使用的目录",
    "sessions_loaded": 10,
    "agent_runs_loaded": 4
  }
  ```

## 5. 配置与存储

- `config.yaml`（项目根目录）：
  ```yaml
  claude_dir: C:/Users/11988/.claude
  sessions_db: ./sessions.db
  port: 8207
  ```
  - `claude_dir`：Claude Code 项目根目录（含 `projects/`）；为空时会自动探测，优先 `~/.claude`。
  - `sessions_db`：SQLite 文件路径，支持绝对或相对路径。
  - `port`：FastAPI 服务监听端口，`start_server.ps1` 同步读取。
- 数据库表：
  - `sessions`：主会话（`session_id`、`title`、`cwd`、`created_at`、`updated_at`）。
  - `agent_sessions`：子 Agent 会话（`agent_id`、`parent_session_id`、`title`、`cwd`、时间戳）。

## 6. CLI 辅助脚本

- `cc_B/read_session.py`：读取 JSONL，输出指定会话的原始记录。
- `cc_B/test_read_session_api.py`：调用 `/sessions` 相关接口做冒烟测试。
- `start_server.ps1`：根据虚拟环境与 `config.yaml` 启动 FastAPI 服务。
