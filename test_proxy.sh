#!/usr/bin/env bash
# 每次 Cursor 升级后先跑一次确认代理功能正常。
set -euo pipefail

BASE="${PROXY_BASE_URL:-http://127.0.0.1:8080}"
HEADER="Authorization: Bearer ${PROXY_API_KEY:-proxy}"
CT="Content-Type: application/json"
PASS=0
FAIL=0

green()  { printf "\033[32m%s\033[0m\n" "$*"; }
red()    { printf "\033[31m%s\033[0m\n" "$*"; }

check() {
    local name="$1" http_code
    shift
    http_code=$(curl -s -o /tmp/proxy_test_resp.txt -w "%{http_code}" "$@")
    if [ "$http_code" -eq 200 ]; then
        green "PASS $name (HTTP $http_code)"
        PASS=$((PASS + 1))
    else
        red "FAIL $name (HTTP $http_code)"
        cat /tmp/proxy_test_resp.txt
        FAIL=$((FAIL + 1))
    fi
}

echo "=== $(date) ==="
echo "Base URL: $BASE"
echo ""

# 1. Health
check "health"        "$BASE/health"

# 2. Models
check "models"        "$BASE/v1/models"

# 3. Non-stream chat
check "chat_non_stream" \
    -H "$HEADER" -H "$CT" \
    -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"reply only: ok"}],"max_tokens":5,"temperature":0}' \
    "$BASE/v1/chat/completions"

# 4. Verify reasoning_content returned
if python3 -c "
import json
with open('/tmp/proxy_test_resp.txt') as f:
    d = json.load(f)
msg = d['choices'][0]['message']
assert 'reasoning_content' in msg or msg.get('content','')
print('reasoning_content' in msg)
" 2>/dev/null; then
    green "PASS reasoning_content present"
    PASS=$((PASS + 1))
else
    red "FAIL reasoning_content missing"
    FAIL=$((FAIL + 1))
fi

# 5. Multi-turn (cache injection)
MSG_ID=$(python3 -c "import uuid; print(uuid.uuid4().hex[:12])")
curl -s -o /dev/null -H "$HEADER" -H "$CT" \
    -d "{\"model\":\"deepseek-v4-pro\",\"messages\":[{\"role\":\"user\",\"content\":\"my id is $MSG_ID. reply: ok\"}],\"max_tokens\":10}" \
    "$BASE/v1/chat/completions"

check "chat_multi_turn" \
    -H "$HEADER" -H "$CT" \
    -d "{\"model\":\"deepseek-v4-pro\",\"messages\":[{\"role\":\"user\",\"content\":\"my id is $MSG_ID. reply: ok\"},{\"role\":\"assistant\",\"content\":\"ok\"},{\"role\":\"user\",\"content\":\"what is my id?\"}],\"max_tokens\":20}" \
    "$BASE/v1/chat/completions"

# 6. Stream chat
STREAM_OK=0
curl -s -N -H "$HEADER" -H "$CT" \
    -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"say hi"}],"max_tokens":5,"stream":true}' \
    "$BASE/v1/chat/completions" > /tmp/proxy_test_stream.txt 2>&1 &
CURL_PID=$!
sleep 5
kill "$CURL_PID" 2>/dev/null || true
wait "$CURL_PID" 2>/dev/null || true

if grep -q '^data: ' /tmp/proxy_test_stream.txt 2>/dev/null; then
    green "PASS stream_chat"
    PASS=$((PASS + 1))
else
    red "FAIL stream_chat (no SSE data in stream)"
    head -5 /tmp/proxy_test_stream.txt
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
