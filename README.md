# DeepSeek Cursor Proxy

面向 Cursor 的 OpenAI 兼容代理，目标是稳定调用上游 `deepseek-v4-pro`，并兼容 reasoning/thinking 多轮对话。

## 项目结构

```text
llm-proxy/
├── app/
│   ├── core/
│   │   ├── settings.py         # 环境变量、运行配置、白名单字段
│   │   └── logging_config.py   # 统一日志配置
│   ├── services/
│   │   ├── key_pool.py         # 多 key 轮换 + 限流冷却
│   │   ├── reasoning_store.py  # reasoning_content 缓存与补回
│   │   └── payloads.py         # 请求清洗、工具格式兼容、响应处理
│   └── server.py               # FastAPI 路由与上游转发逻辑
├── main.py                     # 入口（兼容 uvicorn main:app）
├── run.sh                      # 一键启动脚本
├── test_proxy.sh               # 升级后回归检查脚本
├── .env.example                # 配置模板
├── docker-compose.yml          # 代理 + Cloudflare 隧道
├── cloudflared/
│   ├── config.example.yml      # 固定域名方案模板（当前默认不用）
│   └── README.md               # 临时隧道与升级说明
└── requirements.txt
```

## 核心能力

- **Thinking 兼容**：缓存并补回 `reasoning_content`，减少多轮报错。
- **多 Key 轮换**：支持 `DEEPSEEK_API_KEYS`，遇到 429/限流自动切换 key。
- **字段兼容**：过滤非标准字段，避免 Cursor 升级后携带新字段导致上游报错。
- **工具格式兼容**：自动把可能的平铺 tools 结构转换为嵌套 `function` 结构。
- **路径兼容**：同时支持 `/v1/...` 与无前缀路径。

## 快速启动

```bash
cd /home/lychee/workspace/llm-proxy
cp .env.example .env
# 编辑 .env
./run.sh
```

健康检查：

```bash
curl http://localhost:8080/health
```

## Docker 部署

### 仅代理

```bash
cd /home/lychee/workspace/llm-proxy
docker build -t llm-proxy:latest .
docker compose up -d llm-proxy
docker compose logs -f llm-proxy
```

会读取项目根目录的 `.env`，本机访问 `http://127.0.0.1:8080`。

### 代理 + Cloudflare 临时公网（当前默认）

无需 Cloudflare 账号或 DNS，使用 Quick Tunnel（`*.trycloudflare.com`）。域名审批通过后再改固定域名，见 [cloudflared/README.md](cloudflared/README.md)。

```bash
docker compose up -d
docker compose logs -f cloudflared
```

从日志复制临时 HTTPS 地址，Cursor Base URL 填 `https://<临时域名>/v1`。

提取地址（可选）：

```bash
docker compose logs cloudflared 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1
```

**注意**：重建 `cloudflared` 容器后临时 URL 可能变化，需更新 Cursor 配置。

仅启动代理、不启隧道：

```bash
docker compose up -d llm-proxy
```

### 手动 docker run（可选）

```bash
docker run -d --name llm-proxy \
  --restart unless-stopped \
  -p 8080:8080 \
  --env-file .env \
  llm-proxy:latest
```

## Cursor 配置

```text
Base URL: https://<your-public-url>/v1
Model Name: deepseek-v4-pro
API Key: proxy
```

注意：

- Cursor 的 API Key 填的是 `PROXY_API_KEY`，不是 DeepSeek 的真实 key。
- 如果 Cursor 校验拦截 `deepseek-v4-pro`，可用外层别名（例如 `deepseek-v4-flash`），代理内部仍会转发到 `DEEPSEEK_MODEL=deepseek-v4-pro`。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 单 key 模式 |
| `DEEPSEEK_API_KEYS` | 多 key 模式（逗号分隔），优先级高于单 key |
| `PROXY_API_KEY` | Cursor 侧填写的代理 key |
| `DEEPSEEK_BASE_URL` | 上游 OpenAI 兼容地址 |
| `DEEPSEEK_MODEL` | 实际上游模型（默认 `deepseek-v4-pro`） |
| `PROXY_MODEL_NAME` | 代理暴露的主模型名 |
| `PROXY_MODEL_ALIASES` | 代理暴露的额外模型名 |
| `REQUEST_TIMEOUT` | 非流式超时（秒） |
| `KEY_RATE_LIMIT_COOLDOWN_SECONDS` | key 限流后的冷却时间 |
| `MAX_REASONING_CACHE_ITEMS` | reasoning 指纹缓存上限 |
| `PORT` | 代理端口 |

## 多 Key 限流轮换

示例：

```env
DEEPSEEK_API_KEYS=sk-key-1,sk-key-2,sk-key-3
KEY_RATE_LIMIT_COOLDOWN_SECONDS=60
```

行为：

1. 请求按轮询选 key；
2. 命中 429/`rate limit exceeded` 时，当前 key 进入冷却；
3. 同一请求内自动切到下一个 key 重试；
4. 冷却结束后自动恢复参与轮换。

## 回归测试

每次 Cursor 升级后建议先跑：

```bash
cd /home/lychee/workspace/llm-proxy
./test_proxy.sh
```

脚本会检查：health、models、非流式 chat、thinking 返回、多轮对话、流式输出。

## 日志与排错

代理会输出关键日志：

- 请求摘要：模型、stream、消息数、工具数、reasoning 注入统计
- 上游请求：当前尝试次数、使用的 key 编号
- 响应摘要：耗时、token、缓存数量
- 限流事件：key 冷却记录

常见问题：

- `Access to private networks is forbidden`：Cursor 不允许直接访问内网地址，需走公网 HTTPS（Cloudflare Tunnel/ngrok）。
- `Model name is not valid`：Cursor 模型名校验拦截，改用可通过校验的外层模型名。
- `User API Key Rate limit exceeded`：配置 `DEEPSEEK_API_KEYS` 开启轮换。
