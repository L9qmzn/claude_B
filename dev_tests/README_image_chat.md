# 带图片的 Chat Demo

这个 demo 展示了如何使用 ts_backend 发送包含图片的消息给 Claude。

## 功能

- 支持发送文本和图片的组合消息
- 自动生成测试图片（如果未提供）
- 或者从文件加载图片
- 流式接收 Claude 的响应

## 使用方法

### 基本用法（使用自动生成的测试图片）

```bash
python demo_chat_with_image.py --text "请描述这张图片的内容"
```

### 使用自定义图片

```bash
python demo_chat_with_image.py --text "这张图片里有什么？" --image path/to/your/image.png
```

### 继续现有会话

```bash
python demo_chat_with_image.py --text "再给我更多细节" --session-id <session-id>
```

## 命令行参数

- `--base-url`: 后端服务器地址（默认: http://127.0.0.1:8207）
- `--text`: 要发送的文本消息（默认: "请描述这张图片中的内容"）
- `--image`: 图片文件路径（可选，不提供则自动生成测试图片）
- `--cwd`: 工作目录（默认: 项目根目录）
- `--session-id`: 会话 ID，用于继续现有会话（可选）
- `--username`: HTTP Basic 认证用户名（默认从 config.yaml 读取）
- `--password`: HTTP Basic 认证密码（默认从 config.yaml 读取）
- `--permission-mode`: 权限模式（default/plan/acceptEdits/bypassPermissions）

## 依赖

- httpx: HTTP 客户端
- PyYAML: 配置文件解析
- Pillow (可选): 用于生成测试图片

安装依赖：

```bash
pip install httpx pyyaml pillow
```

注：如果没有安装 Pillow，demo 会使用一个最小的 1x1 PNG 图片作为测试。

## 技术细节

### 消息格式

发送到 `/chat` 端点的消息格式：

```json
{
  "message": [
    {
      "type": "text",
      "text": "请描述这张图片的内容"
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
  "permission_mode": "default",
  "cwd": "/path/to/working/directory"
}
```

### 后端修改

为了支持图片消息，ts_backend 进行了以下修改：

1. **models.ts**:
   - 添加了 `MessageContent` 类型，支持字符串或内容块数组
   - 更新了 `ChatRequest` 接口，`message` 字段现在使用 `MessageContent` 类型

2. **app.ts**:
   - 更新了消息验证逻辑，支持字符串和数组格式
   - 更新了会话标题提取逻辑，从内容块数组中提取文本
   - 更新了 `SDKUserMessage` 创建逻辑，直接传递消息内容

### 支持的图片格式

- JPEG (image/jpeg)
- PNG (image/png)
- GIF (image/gif)
- WebP (image/webp)

所有图片都需要使用 base64 编码发送。

## 示例输出

```
[IMAGE] Generating test image...
   Image encoded to base64: 9440 characters

[POST] http://127.0.0.1:8207/chat
    Sending message with text and image
    Text: Describe this image
    Image size: 9440 bytes (base64)

[STREAM] Streaming events:

event: run
data: {"run_id": "d82f5f8b-d4a2-4dca-a61b-15236ad1bf25"}

event: session
data: {"session_id": "656e9509-0c07-4575-aa71-efac77d1f9e7", "cwd": "...", "is_new": true}

event: token
This is a simple geometric test image with the following elements:

1. **Background**: A gradient that transitions from olive/yellow-green at the top,
   through teal/cyan in the middle, to blue at the bottom

2. **Shapes**:
   - A coral/salmon-pink circle positioned in the upper-left area
   - A bright lime-green rectangle positioned in the upper-right area

3. **Text**: "Test Image for Claude" written in black text at the bottom of the image

...

[OK] Stream completed
   session_id = 656e9509-0c07-4575-aa71-efac77d1f9e7
   Total response length: 620 characters
```

## 故障排除

### 问题：收到 400 Bad Request 错误

**解决方案**: 确保后端服务器已重启并使用最新的编译代码：

```bash
cd ts_backend
npm run build
npm start
```

### 问题：Unicode 编码错误

**解决方案**: 这是 Windows 控制台编码问题，demo 已经移除了所有特殊 Unicode 字符。如果仍有问题，可以设置环境变量：

```bash
set PYTHONIOENCODING=utf-8
python demo_chat_with_image.py ...
```

### 问题：图片太大导致请求失败

**解决方案**: 后端的 JSON body 限制是 5MB。如果图片过大，需要先压缩或调整大小。或者修改 `ts_backend/src/app.ts` 中的限制：

```typescript
app.use(express.json({ limit: "10mb" })); // 增加到 10MB
```

## 相关文件

- `demo_chat_with_image.py`: 主 demo 脚本
- `ts_backend/src/models.ts`: 消息类型定义
- `ts_backend/src/app.ts`: API 路由和处理逻辑
