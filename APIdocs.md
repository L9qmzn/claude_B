# API 文档

FastAPI 服务默认监听 `http://127.0.0.1:8207`（可在 `config.yaml` 中调整端口），并提供以下接口：

## 1. `POST /chat`

- **功能**：与 Claude Agent SDK 建立流式对话（SSE）。
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
  - `permission_mode`（可选，默认 `default`）：可取 `default` / `plan` / `acceptEdits` / `bypassPermissions`，用于透传到 ClaudeAgentOptions.permission_mode，实现权限或工具调用策略控制。
- **响应**：`text/event-stream`，事件类型包含：
  - `session`：返回 `session_id`、`cwd`、`is_new`。
  - `token`：助手增量文本块（便于前端逐字渲染）。
  - `message`：Claude Agent SDK 原始消息负载（`SystemMessage` / `AssistantMessage` / `ResultMessage`），包含工具调用、token 使用等完整信息。
  - `done`：一轮完成，附带 `length` 等元信息。
  - `error`：异常信息。

## 2. `GET /sessions`

- **功能**：列出所有已知主会话元信息。
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
      {
        "type": "user",
        "timestamp": "ISO8601",
        "message": {
          "role": "user",
          "content": [
            {"type": "text", "text": "原始内容 ..."}
          ]
        }
      },
      {
        "type": "assistant",
        "timestamp": "ISO8601",
        "message": {
          "role": "assistant",
          "content": [
            {"type": "text", "text": "Claude 回复 ..."},
            {"type": "tool_use", "name": "list_files", "input": {"path": "."}}
          ],
          "session_id": "..."
        }
      }
    ]
  }
  ```
  `messages` 字段直接返回 JSONL 文件中的完整记录，保留全部字段（包括工具调用、引用等），数据位于 `~/.claude/projects/<slug>/<session>.jsonl`。

## 4. `POST /sessions/load`

- **功能**：扫描 Claude Code 存档目录，把主会话和 agent 子会话写入数据库。
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

## 5. 数据结构 / 配置

- `config.yaml`（项目根目录）：
  ```yaml
  claude_dir: C:/Users/11988/.claude
  sessions_db: ./sessions.db
  port: 8207
  ```
  - `claude_dir`：Claude Code 项目的根目录（包含 `projects/`）。
    如果此字段为空，服务会根据操作系统自动寻找 Claude 安装目录，优先使用 `~/.claude`。
  - `sessions_db`：SQLite 文件路径，支持绝对或相对路径。
  - `port`：FastAPI 服务监听端口，`start_server.ps1` 会读取此配置。

- SQLite 表：
  - `sessions`：主会话（`session_id`、`title`、`cwd`、`created_at`、`updated_at`）。
  - `agent_sessions`：子 Agent（`agent_id`、`parent_session_id`、`title`、`cwd`、时间戳）。

## 6. CLI 辅助脚本

- `cc_B/read_session.py`：从 JSONL 读取指定会话历史。
- `cc_B/test_read_session_api.py`：调用 API 验证 `/sessions` 相关读操作。
- `start_server.ps1`：一键启动 FastAPI（支持读取虚拟环境 Python、`config.yaml` 端口、自动展示 base URL）。
