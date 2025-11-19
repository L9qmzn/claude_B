# API 文档

服务默认监听 `http://127.0.0.1:8207`（可在 `config.yaml` 中调整端口）。以下接口均由 Basic Auth 保护，和 Python 版保持一致。

## 鉴权

- 所有 HTTP 接口都需要在请求头里携带 `Authorization: Basic <base64(username:password)>`
- `config.yaml` 的 `users` 列中定义合法的用户名/密码；未声明时默认存在 `admin / 642531`
- 认证失败返回 `401 Unauthorized`

## 1. `POST /chat`

- **功能**：调用 Claude Code Agent SDK，以 SSE 形式返回流式事件
- **新会话请求体（文本消息）**
  ```json
  {
    "message": "第一条输入",
    "cwd": "C:/path/to/project",
    "permission_mode": "default",
    "system_prompt": { "type": "preset", "preset": "claude_code" }
  }
  ```
- **新会话请求体（带图片消息）**
  ```json
  {
    "message": [
      {
        "type": "text",
        "text": "请描述这张图片"
      },
      {
        "type": "image",
        "source": {
          "type": "base64",
          "media_type": "image/png",
          "data": "<base64-encoded-image-data>"
        }
      }
    ],
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
- **消息格式说明**：
  - `message` 字段支持两种格式：
    - **字符串**：纯文本消息，例如 `"message": "你好"`
    - **内容块数组**：支持文本和图片的组合，每个内容块可以是：
      - 文本块：`{ "type": "text", "text": "文本内容" }`
      - 图片块：`{ "type": "image", "source": { "type": "base64", "media_type": "image/png|image/jpeg|image/gif|image/webp", "data": "<base64编码的图片数据>" } }`
  - 图片必须使用 base64 编码
  - 支持的图片格式：PNG、JPEG、GIF、WebP
  - 一条消息可以包含多个文本块和图片块
- **持续消息传递（Continuous Messaging）**：
  - ✅ **支持中途发送新消息**：即使上一条消息还在流式返回中，也可以发送新消息到同一会话
  - 🔄 **消息队列机制**：使用 `MessageStreamController` 管理消息队列，通过 `AsyncIterable<SDKUserMessage>` 接口持续向SDK注入消息
  - ⏱️ **自动超时结束**：当收到 `result` 消息后，如果3秒内没有新消息到达，会自动结束stream并清理会话状态
  - 📡 **广播模式**：多个HTTP连接可以订阅同一个 `session_id`，所有连接都会收到相同的响应事件
  - 💡 **使用场景**：
    - 在Claude响应过程中发送补充信息或修正指令
    - 中断当前回答并提出新问题
    - 实现真正的交互式对话体验
- `permission_mode` 透传给 `ClaudeAgentOptions.permission_mode`，取值 `default` / `plan` / `acceptEdits` / `bypassPermissions`
- `system_prompt` 透传给 `ClaudeAgentOptions.system_prompt`，可为字符串或 JSON 对象
- **高级参数**：现在 `/chat` 还支持直接传入 `@anthropic-ai/claude-agent-sdk` 暴露的绝大多数配置项，所有字段采用蛇形命名并在内部映射到 `ClaudeAgentOptions`：`additional_directories`、`agents`、`allowed_tools`、`continue`、`disallowed_tools`、`env`、`executable`、`executable_args`、`extra_args`、`fallback_model`、`fork_session`、`include_partial_messages`、`max_thinking_tokens`、`max_turns`、`max_budget_usd`、`mcp_servers`、`model`、`path_to_claude_code_executable`、`allow_dangerously_skip_permissions`、`permission_prompt_tool_name`、`plugins`、`resume_session_at`、`setting_sources`、`strict_mcp_config`。
  字段值与 CLI/SDK 文档保持一致，例如 `additional_directories` 期望字符串数组、`env` 期望键值对字典。
- **响应**：`text/event-stream`，事件类型：
  - `run`：连接建立后立即下发 `{ "run_id": "..." }`，便于前端主动停止任务
  - `session`：当前 `session_id`、`cwd`、`is_new`
  - `token`：助手增量文本（**单词级流式传输**）
    - 服务器将SDK返回的完整响应文本拆分成单词级别的token
    - 每个token包含一个单词或空格，格式：`{ "session_id": "...", "text": "word" }`
    - 这种设计使客户端能够实时监控响应进度，并在合适的时机发送中断消息
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

## 9. 图片消息功能

### 9.1 功能概述

`/chat` 接口现在支持发送包含图片的消息。这使得 Claude 可以分析图片内容、回答关于图片的问题、或者基于图片进行编程任务。

### 9.2 消息格式

#### 纯文本消息（向后兼容）
```json
{
  "message": "这是一条文本消息",
  "cwd": "/path/to/project"
}
```

#### 带图片的消息
```json
{
  "message": [
    {
      "type": "text",
      "text": "请分析这张图片中的UI布局"
    },
    {
      "type": "image",
      "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB..."
      }
    }
  ],
  "cwd": "/path/to/project"
}
```

#### 多图片消息
```json
{
  "message": [
    {
      "type": "text",
      "text": "比较这两张图片的差异"
    },
    {
      "type": "image",
      "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": "<base64-image-1>"
      }
    },
    {
      "type": "image",
      "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": "<base64-image-2>"
      }
    }
  ],
  "cwd": "/path/to/project"
}
```

### 9.3 支持的图片格式

| 格式 | MIME 类型 | 说明 |
|------|-----------|------|
| PNG | `image/png` | 推荐用于截图和UI设计图 |
| JPEG | `image/jpeg` | 推荐用于照片 |
| GIF | `image/gif` | 支持静态GIF |
| WebP | `image/webp` | 现代图片格式 |

### 9.4 注意事项

1. **图片大小限制**：
   - 默认 JSON body 限制为 1MB
   - Base64 编码会使图片大小增加约 33%
   - 建议在发送前压缩或调整图片大小
   - 如需更大限制，可在 `ts_backend/src/app.ts` 中修改 `express.json({ limit: "10mb" })`

2. **Base64 编码**：
   - 所有图片必须转换为 base64 编码字符串
   - Python 示例：
     ```python
     import base64
     with open("image.png", "rb") as f:
         image_data = base64.b64encode(f.read()).decode("utf-8")
     ```
   - JavaScript 示例：
     ```javascript
     const fs = require('fs');
     const imageData = fs.readFileSync('image.png').toString('base64');
     ```

3. **性能考虑**：
   - 大图片会增加请求处理时间
   - 建议将图片缩放至合理尺寸（如 1024x1024 以内）
   - 对于UI截图，PNG 格式通常能提供更好的压缩比

4. **会话继续**：
   - 图片消息同样支持会话继续功能
   - 后续消息可以引用之前发送的图片内容

### 9.5 使用示例

项目提供了完整的 Python demo：`dev_tests/demo_chat_with_image.py`

#### 基本用法
```bash
# 使用自动生成的测试图片
python dev_tests/demo_chat_with_image.py --text "描述这张图片"

# 使用自定义图片
python dev_tests/demo_chat_with_image.py --text "这是什么？" --image path/to/image.png

# 继续会话
python dev_tests/demo_chat_with_image.py --text "更详细地分析" --session-id <session-id>
```

#### Python 代码示例
```python
import httpx
import base64

# 读取并编码图片
with open("screenshot.png", "rb") as f:
    image_base64 = base64.b64encode(f.read()).decode("utf-8")

# 构建消息
message = [
    {"type": "text", "text": "请帮我实现这个UI界面"},
    {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": image_base64
        }
    }
]

# 发送请求
async with httpx.AsyncClient(auth=("admin", "642531")) as client:
    async with client.stream(
        "POST",
        "http://127.0.0.1:8207/chat",
        json={
            "message": message,
            "cwd": "/path/to/project",
            "permission_mode": "default"
        }
    ) as response:
        async for line in response.aiter_lines():
            print(line)
```

### 9.6 典型应用场景

1. **UI/UX 实现**：发送设计稿截图，让 Claude 生成对应的 HTML/CSS/React 代码
2. **错误诊断**：发送错误截图，让 Claude 分析问题并提供解决方案
3. **文档分析**：发送文档截图，让 Claude 提取信息或回答问题
4. **代码审查**：发送代码截图，让 Claude 提供改进建议
5. **架构设计**：发送架构图，让 Claude 帮助实现或优化

### 9.7 更多信息

详细使用说明请参考：`dev_tests/README_image_chat.md`
