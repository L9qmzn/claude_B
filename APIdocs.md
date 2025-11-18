# API 文档

服务默认监听 `http://127.0.0.1:8207`（可在 `config.yaml` 中调整端口）。以下接口均由 Basic Auth 保护，和 Python 版保持一致。

## 鉴权

- 所有 HTTP 接口都需要在请求头里携带 `Authorization: Basic <base64(username:password)>`
- `config.yaml` 的 `users` 列中定义合法的用户名/密码；未声明时默认存在 `admin / 642531`
- 认证失败返回 `401 Unauthorized`

## 1. `POST /chat`

- **功能**：调用 Claude Code Agent SDK，以 SSE 形式返回流式事件
- **新会话请求体**
  ```json
  {
    "message": "第一条输入",
    "cwd": "C:/path/to/project",
    "permission_mode": "default",
    "system_prompt": { "type": "preset", "preset": "claude_code" }
  }
  ```
- **继续会话请求体**
  ```json
  {
    "session_id": "session-uuid",
    "message": "继续对话"
  }
  ```
- `permission_mode` 透传给 `ClaudeAgentOptions.permission_mode`，取值 `default` / `plan` / `acceptEdits` / `bypassPermissions`
- `system_prompt` 透传给 `ClaudeAgentOptions.system_prompt`，可为字符串或 JSON 对象
- **高级参数**：现在 `/chat` 还支持直接传入 `@anthropic-ai/claude-agent-sdk` 暴露的绝大多数配置项，所有字段采用蛇形命名并在内部映射到 `ClaudeAgentOptions`：`additional_directories`、`agents`、`allowed_tools`、`continue`、`disallowed_tools`、`env`、`executable`、`executable_args`、`extra_args`、`fallback_model`、`fork_session`、`include_partial_messages`、`max_thinking_tokens`、`max_turns`、`max_budget_usd`、`mcp_servers`、`model`、`path_to_claude_code_executable`、`allow_dangerously_skip_permissions`、`permission_prompt_tool_name`、`plugins`、`resume_session_at`、`setting_sources`、`strict_mcp_config`。
  字段值与 CLI/SDK 文档保持一致，例如 `additional_directories` 期望字符串数组、`env` 期望键值对字典。
- **响应**：`text/event-stream`，事件类型：
  - `run`：连接建立后立即下发 `{ "run_id": "..." }`，便于前端主动停止任务
  - `session`：当前 `session_id`、`cwd`、`is_new`
  - `token`：助手增量文本
  - `message`：完整透传 Claude SDK 的原始消息（system/user/assistant/result/stream_event…）
  - `done`：单轮完成事件，附带输出长度
  - `error`：异常信息
  - `stopped`：调用 `/chat/stop` 或服务器中断任务时的确认事件
- 每次响应还会返回 `X-Claude-Run-Id` 响应头，与 `run` 事件的 `run_id` 相同。客户端掉线不会终止 Claude 任务，除非显式调用停止接口。
- **停止接口**：`POST /chat/stop`，请求体 `{ "run_id": "<来自 run 事件或响应头>" }`，调用后立即终止指定任务并触发 `stopped` 事件；若 `run_id` 无效或已完成则返回 `404`。

## 2. `GET /sessions`

- **功能**：按 `updated_at` 倒序列出所有 Claude 主会话概要
- **响应示例**
  ```json
  [
    {
      "session_id": "019a804b-...",
      "title": "会话标题",
      "cwd": "C:/path",
      "created_at": "ISO8601",
      "updated_at": "ISO8601",
      "message_count": 42
    }
  ]
  ```

## 3. `GET /sessions/{session_id}`

- **功能**：返回指定 Claude 会话的完整信息及 JSONL 消息列表
- **响应示例**
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
  `messages` 数组直接来自 `~/.claude/projects/<slug>/<session>.jsonl`

## 4. `POST /sessions/load`

- **功能**：扫描 Claude Code 存档目录，将主会话与 Agent 子会话写入数据库
- **请求体（可选）**
  ```json
  { "claude_dir": "C:/Users/11988/.claude" }
  ```
- **响应**
  ```json
  {
    "claude_dir": "最终使用的目录",
    "sessions_loaded": 10,
    "agent_runs_loaded": 4
  }
  ```

## 5. 配置与存储

`config.yaml` 示例：
```yaml
claude_dir: C:/Users/11988/.claude
codex_dir: C:/Users/11988/.codex/sessions
codex_api_key: ""       # 可选，缺省读取环境变量 CODEX_API_KEY
codex_cli_path: ""      # 可选，自定义 codex 可执行文件
sessions_db: ./sessions.db
port: 8207
users:
  admin: 642531
```

- `claude_dir`：Claude Code 项目根目录（为空时自动探测 `~/.claude`）
- `codex_dir`：Codex CLI 会话目录（默认为 `~/.codex/sessions`）
- `sessions_db`：SQLite 文件路径（可相对或绝对）
- `users`：Basic Auth 用户表
- SQLite 中维护的表：
  - `sessions`：Claude 主会话
  - `agent_sessions`：Claude Agent 子会话
  - `user_settings`：Claude per-user 设置
  - `codex_sessions`：Codex 主会话
  - `codex_user_settings`：Codex per-user 设置（JSON 形式）

## 6. `GET/PUT /users/{user_id}/settings`

- **功能**：读写某个用户的全局偏好（`permission_mode`、`system_prompt`）
- **权限**：路径中的 `user_id` 必须等于 Basic Auth 用户名，否则返回 403
- **GET 默认响应**
  ```json
  {
    "user_id": "someone",
    "permission_mode": "default",
    "system_prompt": { "type": "preset", "preset": "claude_code" }
  }
  ```
- **PUT 示例**
  ```json
  {
    "permission_mode": "plan",
    "system_prompt": "You are a helpful assistant"
  }
  ```

## 7. CLI 辅助脚本

- `cc_B/read_session.py`：读取 Claude JSONL 并输出完整消息
- `cc_B/test_read_session_api.py`：调用 `/sessions` 相关接口做冒烟测试
- `start_server.ps1`：根据虚拟环境与 `config.yaml` 启动后端服务

## 8. Codex CLI 相关接口（`/codex/*`）

Codex HTTP 路径与 Claude 路径的鉴权与返回格式保持一致，只是在 URL 前添加 `/codex` 前缀，并使用 `@openai/codex-sdk` 调用本地 Codex CLI。

- `POST /codex/chat`：请求体与 `/chat` 类似，但支持 Codex 专用字段：
  - `approval_policy`: `"never" | "on-request" | "on-failure" | "untrusted"`
  - `sandbox_mode`: `"read-only" | "workspace-write" | "danger-full-access"`
  - `skip_git_repo_check`: `true/false`
  - `model`, `model_reasoning_effort`, `network_access_enabled`, `web_search_enabled`
  这些字段若未提供，会回退到 `/codex/users/{user_id}/settings` 保存的 JSON 默认值。SSE 事件仍为 `session`/`token`/`message`/`done`/`error`。
- `GET /codex/sessions`、`GET /codex/sessions/{session_id}`、`POST /codex/sessions/load`：与 Claude 版本一一对应，只是读取 `codex_dir` 并写入 `codex_sessions` 表。
- `GET/PUT /codex/users/{user_id}/settings`：存储 Codex per-user 默认参数，例如：
  ```json
  {
    "approval_policy": "on-request",
    "sandbox_mode": "read-only",
    "model_reasoning_effort": "medium",
    "network_access_enabled": false,
    "web_search_enabled": false,
    "skip_git_repo_check": false
  }
  ```
  `/codex/chat` 会在请求体未提供时自动应用这些默认值。
