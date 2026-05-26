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
├── config.example.py           # 配置模板
├── docker-compose.yml          # 代理 + Cloudflare 临时隧道（默认）
├── docker-compose.fixed.yml    # 永久域名覆盖文件
├── cloudflared/
│   ├── config.example.yml      # 永久域名隧道配置模板
│   ├── setup-fixed-tunnel.sh   # 一键初始化固定隧道脚本
│   └── README.md               # 双模式部署说明
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
cp config.example.py config.py
# 编辑 config.py，填入你的 DeepSeek API keys
./run.sh
```

健康检查：

```bash
curl http://localhost:8080/health
```

## Docker 部署

项目支持两种公网暴露方式，详情见 [cloudflared/README.md](cloudflared/README.md)。

### 仅代理（内网访问）

```bash
cd /home/lychee/workspace/llm-proxy
docker build -t llm-proxy:latest .
docker compose up -d llm-proxy
docker compose logs -f llm-proxy
```

本机访问 `http://127.0.0.1:8080`。

### 代理 + 临时域名（默认，无需 Cloudflare 账号）

适合他人快速体验，每次启动生成随机 `*.trycloudflare.com` 域名。

```bash
docker compose up -d
docker compose logs -f cloudflared
```

从日志复制临时 HTTPS 地址：

```bash
docker compose logs cloudflared 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1
```

> 重建 `cloudflared` 容器后临时 URL 可能变化，需更新 Cursor 配置。

### 代理 + 永久域名（需要 Cloudflare 账号 + DNS）

适合长期使用，域名永远不变。

```bash
# 1. 一次性初始化
./cloudflared/setup-fixed-tunnel.sh

# 2. 按提示在 Cloudflare 控制台添加 DNS CNAME 记录

# 3. 先停掉旧容器，再以永久域名模式启动
docker compose down
docker compose -f docker-compose.yml -f docker-compose.fixed.yml up -d
docker compose logs -f cloudflared-fixed
```

验证：

```bash
curl https://你的域名/health
# 应返回 {"status": "ok"}
```

切换回临时域名：

```bash
docker compose down
docker compose up -d
```

### 手动 docker run（可选）

```bash
docker run -d --name llm-proxy \
  --restart unless-stopped \
  -p 8080:8080 \
  -v $(pwd)/config.py:/app/config.py:ro \
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
- 如果 Cursor 校验拦截 `deepseek-v4-pro`，可用外层别名（例如 `deepseek-v4-flash`），代理内部仍会转发到 `config.py` 中配置的模型。

## 配置项（config.py）

| 配置项 | 类型 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEYS` | `list[str]` | DeepSeek API Key 列表，代理自动轮换 |
| `PROXY_API_KEY` | `str` | Cursor 侧填写的代理 key |
| `DEEPSEEK_BASE_URL` | `str` | 上游 OpenAI 兼容地址 |
| `DEEPSEEK_MODEL` | `str` | 默认上游模型（`deepseek-v4-pro`） |
| `PROXY_MODEL_NAME` | `str` | 代理暴露的主模型名 |
| `PROXY_MODEL_ALIASES` | `list[str]` | 代理暴露的额外模型名 |
| `REQUEST_TIMEOUT` | `float` | 非流式超时（秒） |
| `KEY_RATE_LIMIT_COOLDOWN_SECONDS` | `float` | key 限流后的冷却时间 |
| `MAX_REASONING_CACHE_ITEMS` | `int` | reasoning 指纹缓存上限 |
| `PORT` | `int` | 代理端口 |

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
