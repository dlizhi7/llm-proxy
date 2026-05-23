# DeepSeek Cursor Proxy

这是一个给 Cursor Custom Model 使用的本地 OpenAI 兼容代理，目标是让 Cursor 通过本地代理调用上游 `deepseek-v4-pro`。

它解决的问题是：DeepSeek thinking/reasoning 模式在多轮对话里要求把上一轮 assistant 消息的 `reasoning_content` 带回 API，但 Cursor 的自定义模型适配不一定会保留这个字段。这个代理会在本地缓存 `reasoning_content`，下一轮请求时自动补回去，并同时兼容 Agent 模式里的流式响应和工具调用历史。

## 运行方式

```bash
cd /home/lychee/workspace/llm-proxy
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
./run.sh
```

启动后检查：

```bash
curl http://localhost:8080/health
```

## Cursor 配置

在 Cursor Settings -> Models 中：

1. 开启 **Override OpenAI Base URL**（或 OpenAI API Key 里的 Base URL 覆盖）
2. Base URL 填：`http://localhost:8080/v1`
3. **Add Custom Model** 时，优先填写：`deepseek-v4-pro`
4. API Key 填代理本地 key，默认是：`proxy`（真实 DeepSeek Key 只放在代理的 `.env` 里）

```text
Base URL: http://localhost:8080/v1
Model Name: deepseek-v4-pro
API Key: proxy
```

如果 Cursor 仍然报下面这个错误，说明请求还没有到达代理，而是被 Cursor 自己的模型名校验拦截了：

`Model name is not valid: "deepseek-v4-pro"`

代理会把请求里的 `model` 统一改成 `.env` 里的 `DEEPSEEK_MODEL`，当前默认就是 `deepseek-v4-pro`。

这种情况下，本地代理无法绕过 Cursor 的校验。可行做法是：Cursor 里选择一个它允许通过校验的外层模型名，同时保持 `.env` 里的 `DEEPSEEK_MODEL=deepseek-v4-pro`。只要请求能到达代理，上游实际调用的一定是 `deepseek-v4-pro`。

当前 `.env.example` 默认也暴露了 `deepseek-v4-flash` 作为外层别名。如果 `deepseek-v4-pro` 被 Cursor 拦截，可以在 Cursor 里添加/选择 `deepseek-v4-flash`，代理内部仍会调用 `DEEPSEEK_MODEL=deepseek-v4-pro`。

代理同时兼容两种 Base URL 写法：

```text
https://your-public-domain/v1
https://your-public-domain
```

优先使用第一种 `/v1` 结尾的写法。不要把完整接口路径填进去，例如不要填 `.../v1/chat/completions`。

如果 Cursor 运行在 Windows，而代理跑在 Linux/WSL/远程 Ubuntu 上，`localhost` 可能指向 Windows 本机。这时需要把 Base URL 改成代理所在机器的 IP，例如：

```text
http://192.168.1.100:8080/v1
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 无 | DeepSeek API Key，必填 |
| `PROXY_API_KEY` | `proxy` | Cursor 里填写的本地代理 Key，不是 DeepSeek Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | DeepSeek OpenAI 兼容接口地址 |
| `DEEPSEEK_MODEL` | `deepseek-v4-pro` | 实际调用的上游模型 |
| `PROXY_MODEL_NAME` | `deepseek-v4-pro` | `/v1/models` 主模型名 |
| `PROXY_MODEL_ALIASES` | `deepseek-v4-pro,deepseek-v4-flash` | `/v1/models` 额外返回的模型名；只影响 Cursor 外层选择 |
| `REQUEST_TIMEOUT` | `300` | 非流式请求超时时间，单位秒 |
| `MAX_REASONING_CACHE_ITEMS` | `2000` | 内存中最多保存的 reasoning 指纹数量 |
| `PORT` | `8080` | 本地代理端口 |

## 工作原理

1. Cursor 调用 `POST /v1/chat/completions`。
2. 代理把请求转发给 DeepSeek。
3. 代理把请求里的 `model` 改成 `DEEPSEEK_MODEL`，默认是 `deepseek-v4-pro`。
4. 如果 DeepSeek 返回了 `reasoning_content`，代理会按 assistant 消息内容、`tool_calls`、旧版 `function_call` 计算多种指纹并缓存。
5. Cursor 下一轮请求带回历史 assistant 消息时，代理用这些指纹找到缓存，把 `reasoning_content` 自动补回请求。
6. 流式响应结束时，代理会拼完整的 assistant 消息，再缓存对应的 `reasoning_content`。

## 注意事项

- 缓存是内存缓存，重启代理后会清空；重启后建议新开一个 Cursor 对话。
- 如果 Cursor 对历史消息做了大幅改写，指纹仍可能匹配不上，这时仍可能触发 DeepSeek 的 `reasoning_content` 报错。
- Agent 模式通常使用流式响应，本代理已支持 SSE 转发、流式 reasoning 缓存、`tool_calls` 和旧版 `function_call`。
- Cursor 3.5.x 使用 Override OpenAI Base URL 时，OpenAI API Key 需要启用并填写；这里填写 `PROXY_API_KEY`，不要填写真实 DeepSeek Key。
- 建议先只开一个 Cursor 会话测试，确认稳定后再用于日常开发。
