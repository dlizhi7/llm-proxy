# Cloudflare 隧道（Docker Compose）

默认使用 **Quick Tunnel**（临时 `*.trycloudflare.com` 域名），无需账号、凭证或 DNS，适合域名审批完成前的过渡期。

与 `llm-proxy` 同网启动，公网 HTTPS 指向容器内 `http://llm-proxy:8080`。

## 启动

```bash
cd /path/to/llm-proxy
docker compose up -d
docker compose logs -f cloudflared
```

日志里会出现类似：

```text
https://random-words.trycloudflare.com
```

提取临时地址（可选）：

```bash
docker compose logs cloudflared 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1
```

## Cursor

```text
Base URL: https://<临时域名>/v1
API Key: <PROXY_API_KEY from .env>
Model Name: deepseek-v4-pro
```

## 注意（临时域名）

- **每次重建 `cloudflared` 容器**（`docker compose up -d --force-recreate cloudflared` 或整机重启后）URL **可能变化**，需在 Cursor 里改 Base URL。
- 临时隧道不适合生产 SLA，仅作过渡。
- 仅启动代理、不启隧道：`docker compose up -d llm-proxy`

## 升级到固定域名（域名审批通过后）

1. 宿主机创建命名隧道，见 [Cloudflare 文档](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/)。
2. 复制 `config.example.yml` → `config.yml`，复制 `~/.cloudflared/<TUNNEL_ID>.json` → `credentials.json`。
3. 将 `docker-compose.yml` 中 `cloudflared` 服务改回命名隧道配置（见 `config.example.yml` 顶部注释或 git 历史）。

`config.example.yml` 为固定域名方案的模板，当前 Quick Tunnel **不需要**这些文件。

## 排错

- 看不到 URL：等几秒后 `docker compose logs cloudflared`，或重启 `cloudflared`。
- `502`：确认 `llm-proxy` 健康：`curl http://127.0.0.1:8080/health`。
- Cursor 报私网/HTTPS 问题：Base URL 必须是日志里的 `https://....trycloudflare.com/v1`，不要填内网 IP。
