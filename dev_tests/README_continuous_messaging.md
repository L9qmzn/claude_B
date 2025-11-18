# Continuous Messaging Test

这个测试用于验证新功能：**在 Claude 流式返回消息的过程中，用户能够持续发送新消息**。

## 功能说明

在之前的实现中，用户只能等待 Claude 完全回复完一条消息后，才能发送下一条。新功能允许：

1. 用户发送第一条消息，Claude 开始流式返回
2. **在 Claude 还在返回的过程中**，用户发送第二条消息
3. 第二条消息会被添加到消息队列
4. Claude 收到新消息后会立即处理
5. 两个流式连接同时工作，都能收到响应

## 技术实现

- 使用 `AsyncIterable<SDKUserMessage>` 替代单个字符串消息
- 维护活跃会话映射 `activeSessions: Map<sessionId, ActiveSession>`
- 使用 `MessageQueue` 类实现异步消息队列
- 支持多个 HTTP 连接同时订阅同一个会话的输出

## 运行测试

### 前提条件

1. 确保后端服务正在运行（默认端口 8207）
2. 安装依赖：
   ```bash
   pip install httpx pyyaml
   ```

### 运行测试

```bash
# 使用默认参数
python dev_tests/test_continuous_messaging.py

# 自定义参数
python dev_tests/test_continuous_messaging.py \
  --base-url http://localhost:3000 \
  --cwd /path/to/project \
  --tokens-before-interrupt 50
```

### 参数说明

- `--base-url`: 后端服务地址（默认：`http://127.0.0.1:8207`）
- `--cwd`: 工作目录（默认：从已有会话获取，或使用项目根目录）
- `--username`: 用户名（默认：从 config.yaml 读取）
- `--password`: 密码（默认：从 config.yaml 读取）
- `--first-message`: 第一条消息内容
- `--second-message`: 第二条消息内容（将打断第一条）
- `--tokens-before-interrupt`: 接收多少个 token 后发送第二条消息（默认：30）

## 测试流程

1. **STEP 1**: 发送第一条消息（例如："请慢慢从 1 数到 10，并简要解释每个数字"）
2. **STEP 2**: 在接收到指定数量的 token 后，发送第二条消息（例如："停止数数，告诉我 2+2=?"）
3. **STEP 3**: 同时监听两个流式响应
4. 验证两条消息都被正确处理

## 预期结果

```
======================================================================
TEST: Continuous Messaging During Streaming
======================================================================

[STEP 1] Starting first message stream...
[conn-1] Run ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
[conn-1:session] ID=abc123, new=True
[conn-1] Streaming response...

1. One - This is the first natural number...
2. Two - The smallest and first prime number...
3. Three - ...

**********************************************************************
[STEP 2] !!! SENDING SECOND MESSAGE WHILE FIRST IS STREAMING !!!
**********************************************************************

[conn-2] Sending: Actually, stop counting. Just tell me what is 2 + 2?
[conn-2] Session ID: abc123
[conn-2] Run ID: yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy
[conn-2] Streaming...

[conn-2:session] ID=abc123, new=False
2 + 2 equals 4.

[conn-2:done] Received 8 tokens

4. Four - ...
[conn-1:done] First stream complete. Tokens: 45

======================================================================
[SUCCESS] Test completed!
Session ID: abc123
First stream tokens: 45
Second message was successfully sent during first stream!
======================================================================
```

## 注意事项

1. 确保使用相同的 `session_id` 才能触发连续消息功能
2. 第二条消息会创建新的 HTTP 连接，但共享同一个 MessageQueue
3. 所有连接都会收到广播的响应事件
4. 测试需要真实的 Claude API key 才能正常工作

## 相关文件

- `ts_backend/src/messageQueue.ts` - 消息队列实现
- `ts_backend/src/app.ts` - `/chat` 接口实现（550-891 行）
- `dev_tests/test_continuous_messaging.py` - 测试脚本
