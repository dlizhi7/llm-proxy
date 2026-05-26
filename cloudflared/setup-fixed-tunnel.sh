#!/usr/bin/env bash
# 一键初始化 Cloudflare 固定域名隧道。
#
# 使用方式:
#   chmod +x cloudflared/setup-fixed-tunnel.sh
#   ./cloudflared/setup-fixed-tunnel.sh
#
# 完成后运行: docker compose -f docker-compose.yml -f docker-compose.fixed.yml up -d

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TUNNEL_NAME="llm-proxy"

# ---------- 彩色输出 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }
ask()   { echo -e "${CYAN}[?]${NC}     $*"; }

# ---------- 检查 cloudflared ----------
if ! command -v cloudflared &>/dev/null; then
    err "未找到 cloudflared CLI，请先安装。"
    echo ""
    echo "  安装方式（选择一种）:"
    echo "    Ubuntu/Debian:  curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && sudo dpkg -i cloudflared.deb"
    echo "    macOS:          brew install cloudflare/cloudflare/cloudflared"
    echo ""
    echo "  或参考: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
fi

info "cloudflared 版本: $(cloudflared version 2>/dev/null || echo 'unknown')"

# ---------- 检测已有隧道 ----------
EXISTING_ID=""
if cloudflared tunnel list 2>/dev/null | grep -q "$TUNNEL_NAME"; then
    EXISTING_ID=$(cloudflared tunnel list --name "$TUNNEL_NAME" -o json 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin)[0]['id'])" 2>/dev/null || echo "")
    if [ -n "$EXISTING_ID" ]; then
        info "检测到已存在的隧道 '$TUNNEL_NAME' (ID: $EXISTING_ID)，将复用。"
    fi
fi

# ---------- Cloudflare 认证 ----------
info "检查 Cloudflare 认证状态..."
CERT_FILE="${HOME}/.cloudflared/cert.pem"
if [ ! -f "$CERT_FILE" ]; then
    warn "未检测到 Cloudflare 认证凭据，即将打开浏览器进行认证。"
    echo ""
    echo "  请确保本机浏览器可用，并在弹出页面中选择要使用的域名。"
    echo ""
    ask "按 Enter 继续..."
    read -r
    cloudflared tunnel login
    info "认证完成。"
else
    info "Cloudflare 认证凭据已存在: $CERT_FILE"
fi

# ---------- 创建隧道 ----------
if [ -z "$EXISTING_ID" ]; then
    info "创建命名隧道 '$TUNNEL_NAME'..."
    CLOUDFLARED_OUTPUT_DIR="${HOME}/.cloudflared"
    TUNNEL_CREATE_OUTPUT=$(cloudflared tunnel create "$TUNNEL_NAME" 2>&1)
    echo "$TUNNEL_CREATE_OUTPUT"

    TUNNEL_ID=$(cloudflared tunnel list --name "$TUNNEL_NAME" -o json 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin)[0]['id'])" 2>/dev/null || echo "")
    if [ -z "$TUNNEL_ID" ]; then
        err "无法获取隧道 ID，请确认隧道创建成功。"
        exit 1
    fi
else
    TUNNEL_ID="$EXISTING_ID"
    info "复用已存在的隧道 ID: $TUNNEL_ID"
fi

CREDENTIALS_FILE="${HOME}/.cloudflared/${TUNNEL_ID}.json"

if [ ! -f "$CREDENTIALS_FILE" ]; then
    err "找不到隧道凭证文件: $CREDENTIALS_FILE"
    err "请确认隧道 ID 正确: $TUNNEL_ID"
    exit 1
fi

# ---------- 复制凭证 ----------
mkdir -p "$SCRIPT_DIR"  # cloudflared/
cp "$CREDENTIALS_FILE" "$PROJECT_DIR/cloudflared/credentials.json"
chmod 644 "$PROJECT_DIR/cloudflared/credentials.json"
info "隧道凭证已复制到 cloudflared/credentials.json（已设为容器可读权限）"

# ---------- 询问域名 ----------
PROMPT="请输入你的永久域名（例如 llm-proxy.example.com）: "
if [ -f "$PROJECT_DIR/cloudflared/config.yml" ]; then
    DEFAULT_DOMAIN=$(grep -oP 'hostname:\s*\K\S+' "$PROJECT_DIR/cloudflared/config.yml" 2>/dev/null | grep -v 'http_status' | head -1 || echo "")
    if [ -n "$DEFAULT_DOMAIN" ]; then
        PROMPT="请输入你的永久域名 [$DEFAULT_DOMAIN]: "
    fi
fi

ask "$PROMPT"
read -r DOMAIN

if [ -z "$DOMAIN" ]; then
    if [ -n "${DEFAULT_DOMAIN:-}" ]; then
        DOMAIN="$DEFAULT_DOMAIN"
        info "使用已有域名: $DOMAIN"
    else
        err "域名不能为空。"
        exit 1
    fi
fi

# ---------- 生成 config.yml ----------
info "生成 cloudflared/config.yml..."
sed -e "s/YOUR_TUNNEL_ID/$TUNNEL_ID/" \
    -e "s/llm-proxy\.example\.com/$DOMAIN/" \
    "$PROJECT_DIR/cloudflared/config.example.yml" \
    > "$PROJECT_DIR/cloudflared/config.yml"
info "配置已保存到 cloudflared/config.yml"

# ---------- 输出 DNS 配置提示 ----------
echo ""
echo "============================================"
echo "  Cloudflare 隧道初始化完成！"
echo "============================================"
echo ""
echo "隧道 ID:   $TUNNEL_ID"
echo "隧道名称:  $TUNNEL_NAME"
echo "域名:      $DOMAIN"
echo ""
echo "下一步 —— 在 Cloudflare 控制台添加 DNS 记录:"
echo ""
echo "  类型:     CNAME"
echo "  名称:     ${DOMAIN%%.*}"
echo "  目标:     ${TUNNEL_ID}.cfargotunnel.com"
echo "  代理状态: 已代理 (橙色云朵)"
echo ""
echo "DNS 生效后，启动服务:"
echo ""
echo "  cd ${PROJECT_DIR}"
echo "  docker compose down"
echo "  docker compose -f docker-compose.yml -f docker-compose.fixed.yml up -d"
echo ""
echo "验证:"
echo ""
echo "  curl https://${DOMAIN}/health"
echo ""
