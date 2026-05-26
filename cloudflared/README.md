# Cloudflare 隧道

项目提供两种公网暴露方式，可根据场景自由切换。

## 模式对比

| | 临时域名（Quick Tunnel） | 永久域名（Named Tunnel） |
|---|---|---|
| 域名格式 | `*.trycloudflare.com`（随机） | 你的固定域名 |
| 需要 Cloudflare 账号 | 不需要 | 需要 |
| 需要 DNS 配置 | 不需要 | 需要（CNAME 指向隧道） |
| 容器重建后域名变化 | **会变** | 不变 |
| 适用场景 | 他人快速体验、测试 | 长期使用 |
| 启动命令 | `docker compose up -d` | `docker compose -f docker-compose.yml -f docker-compose.fixed.yml up -d` |

---

## 方式一：临时域名（默认）

无需任何前置准备，直接启动即可。

```bash
cd /path/to/llm-proxy
docker compose up -d
docker compose logs -f cloudflared
```

日志中会出现：

```text
https://random-words.trycloudflare.com
```

提取临时地址：

```bash
docker compose logs cloudflared 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1
```

Cursor 配置：

```text
Base URL: https://<临时域名>/v1
API Key: proxy
Model Name: deepseek-v4-pro
```

> 重建 `cloudflared` 容器后临时 URL **可能变化**，需在 Cursor 里更新 Base URL。

仅启动代理、不启隧道：

```bash
docker compose up -d llm-proxy
```

---

## 方式二：永久域名

需要先完成一次性初始化，之后域名永远不变。

### 1. 一键初始化

```bash
cd /path/to/llm-proxy
chmod +x cloudflared/setup-fixed-tunnel.sh
./cloudflared/setup-fixed-tunnel.sh
```

脚本会引导你完成：
- Cloudflare 账号认证
- 创建命名隧道
- 生成 `credentials.json` 和 `config.yml`
- 提示 DNS 配置步骤

> 手动初始化请参考下方「手动初始化」章节。

### 2. DNS 配置

在 Cloudflare 控制台为你的域名添加一条 **CNAME 记录**：

| 字段 | 值 |
|---|---|
| 名称 | `llm-proxy`（或你选择的子域名） |
| 目标 | `<TUNNEL_ID>.cfargotunnel.com` |

脚本执行后会在终端打印具体的 `<TUNNEL_ID>`。

### 3. 启动

```bash
# 先停掉旧的临时容器（如果有）
docker compose down

# 以永久域名模式启动
docker compose -f docker-compose.yml -f docker-compose.fixed.yml up -d
docker compose logs -f cloudflared-fixed
```

### 4. 验证

```bash
curl https://你的域名/health
# 应返回 {"status": "ok"}
```

Cursor 配置与临时域名相同，Base URL 换成你的永久域名：

```text
Base URL: https://你的域名/v1
API Key: proxy
Model Name: deepseek-v4-pro
```

---

## 切换模式

```bash
# 从临时域名切换到永久域名
docker compose down
docker compose -f docker-compose.yml -f docker-compose.fixed.yml up -d

# 从永久域名切回临时域名
docker compose down
docker compose up -d
```

切换前务必先 `docker compose down`，避免容器名冲突。

---

## 手动初始化（可选）

如果不使用脚本，手动步骤：

```bash
# 1. 安装 cloudflared CLI
#    参见 https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

# 2. 认证（浏览器弹窗）
cloudflared tunnel login

# 3. 创建命名隧道
cloudflared tunnel create llm-proxy

# 4. 复制凭证
cp ~/.cloudflared/$(cloudflared tunnel list --name llm-proxy -o json | jq -r '.[0].id').json cloudflared/credentials.json

# 5. 生成配置
cp cloudflared/config.example.yml cloudflared/config.yml
# 编辑 config.yml，替换 YOUR_TUNNEL_ID 和 llm-proxy.example.com
```

---

## 排错

- **看不到 URL**：等几秒后 `docker compose logs cloudflared`，或重启 `cloudflared`
- **502**：确认 `llm-proxy` 健康：`curl http://127.0.0.1:8080/health`
- **Cursor 报私网问题**：Base URL 必须是 `https://....trycloudflare.com/v1`，不要填内网 IP
- **固定域名 502**：检查 DNS CNAME 是否指向正确的 `<TUNNEL_ID>.cfargotunnel.com`
- **固定域名证书错误**：Cloudflare 自动签发证书，DNS 生效后等待 1-2 分钟
