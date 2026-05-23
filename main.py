import hashlib
import json
import os
from collections import OrderedDict
from copy import deepcopy
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse


app = FastAPI(title="DeepSeek Cursor Proxy")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_dotenv(path: str = ".env") -> None:
    """A tiny .env loader keeps the proxy easy to run without extra packages."""
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
PROXY_API_KEY = os.getenv("PROXY_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
PROXY_MODEL_NAME = os.getenv("PROXY_MODEL_NAME", "deepseek-v4-pro")
PROXY_MODEL_ALIASES = [
    name.strip()
    for name in os.getenv("PROXY_MODEL_ALIASES", PROXY_MODEL_NAME).split(",")
    if name.strip()
]
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "300"))
MAX_REASONING_CACHE_ITEMS = int(os.getenv("MAX_REASONING_CACHE_ITEMS", "2000"))

# Cursor may drop reasoning_content from assistant history. We remember it here
# and add it back before forwarding the next request to DeepSeek.
reasoning_cache: OrderedDict[str, str] = OrderedDict()


def deepseek_url(path: str) -> str:
    return f"{DEEPSEEK_BASE_URL}{path}"


def normalize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(value: Any) -> str:
    return hashlib.sha256(normalize_json(value).encode("utf-8")).hexdigest()


def normalize_content(content: Any) -> str | list[Any]:
    if content is None:
        return ""
    return content


def normalize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []

    normalized = []
    for tool_call in tool_calls:
        function = tool_call.get("function") or {}
        normalized.append(
            {
                "id": tool_call.get("id", ""),
                "type": tool_call.get("type", "function"),
                "function": {
                    "name": function.get("name", ""),
                    "arguments": function.get("arguments", ""),
                },
            }
        )
    return normalized


def normalize_function_call(function_call: Any) -> dict[str, Any] | None:
    if not isinstance(function_call, dict):
        return None
    return {
        "name": function_call.get("name", ""),
        "arguments": function_call.get("arguments", ""),
    }


def assistant_identity(message: dict[str, Any]) -> dict[str, Any]:
    """Build a stable identity from fields Cursor usually keeps in history."""
    return {
        "role": "assistant",
        "content": normalize_content(message.get("content")),
        "tool_calls": normalize_tool_calls(message.get("tool_calls")),
        "function_call": normalize_function_call(message.get("function_call")),
        "name": message.get("name", ""),
    }


def reasoning_keys_for_message(message: dict[str, Any]) -> list[str]:
    identity = assistant_identity(message)
    keys = [f"exact:{sha256(identity)}"]

    content = identity["content"]
    if content:
        keys.append(f"content:{sha256({'content': content})}")

    tool_calls = identity["tool_calls"]
    if tool_calls:
        keys.append(f"tools:{sha256({'tool_calls': tool_calls})}")
        tool_ids = [tool_call.get("id", "") for tool_call in tool_calls if tool_call.get("id")]
        if tool_ids:
            keys.append(f"tool_ids:{sha256({'tool_ids': tool_ids})}")

    function_call = identity["function_call"]
    if function_call:
        keys.append(f"function:{sha256({'function_call': function_call})}")

    return keys


def cache_reasoning(message: dict[str, Any]) -> None:
    reasoning = message.get("reasoning_content")
    if not reasoning:
        return

    for key in reasoning_keys_for_message(message):
        reasoning_cache[key] = reasoning
        reasoning_cache.move_to_end(key)

    while len(reasoning_cache) > MAX_REASONING_CACHE_ITEMS:
        reasoning_cache.popitem(last=False)


def find_cached_reasoning(message: dict[str, Any]) -> str | None:
    for key in reasoning_keys_for_message(message):
        reasoning = reasoning_cache.get(key)
        if reasoning:
            reasoning_cache.move_to_end(key)
            return reasoning
    return None


def inject_reasoning_content(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    patched_messages = deepcopy(messages)
    stats = {"assistant_messages": 0, "injected": 0, "already_present": 0, "missing": 0}

    for message in patched_messages:
        if message.get("role") != "assistant":
            continue

        stats["assistant_messages"] += 1
        if message.get("reasoning_content"):
            stats["already_present"] += 1
            cache_reasoning(message)
            continue

        cached_reasoning = find_cached_reasoning(message)
        if cached_reasoning:
            message["reasoning_content"] = cached_reasoning
            stats["injected"] += 1
        else:
            stats["missing"] += 1

    return patched_messages, stats


def prepare_deepseek_body(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    deepseek_body = deepcopy(body)
    deepseek_body["model"] = DEEPSEEK_MODEL

    messages = deepseek_body.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("request body field 'messages' must be a list")

    deepseek_body["messages"], stats = inject_reasoning_content(messages)
    return deepseek_body, stats


def deepseek_headers() -> dict[str, str]:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    return {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }


def proxy_authorized(request: Request) -> bool:
    if not PROXY_API_KEY:
        return True

    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    return token == PROXY_API_KEY


def proxy_model_ids() -> list[str]:
    ids: list[str] = []
    for name in [PROXY_MODEL_NAME, *PROXY_MODEL_ALIASES]:
        if name and name not in ids:
            ids.append(name)
    return ids


def rewrite_response_model(data: dict[str, Any], cursor_model: str) -> dict[str, Any]:
    if cursor_model:
        data["model"] = cursor_model
    return data


def append_tool_call_delta(tool_calls: dict[int, dict[str, Any]], delta: dict[str, Any]) -> None:
    index = int(delta.get("index", 0))
    current = tool_calls.setdefault(index, {"function": {"name": "", "arguments": ""}})

    if delta.get("id"):
        current["id"] = delta["id"]
    if delta.get("type"):
        current["type"] = delta["type"]

    function_delta = delta.get("function") or {}
    if function_delta.get("name"):
        current["function"]["name"] += function_delta["name"]
    if function_delta.get("arguments"):
        current["function"]["arguments"] += function_delta["arguments"]


def append_function_call_delta(function_call: dict[str, str], delta: dict[str, Any]) -> None:
    if delta.get("name"):
        function_call["name"] += delta["name"]
    if delta.get("arguments"):
        function_call["arguments"] += delta["arguments"]


def build_streamed_message(state: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(state["content_parts"]) or None,
    }

    if state["tool_calls"]:
        message["tool_calls"] = [
            state["tool_calls"][index] for index in sorted(state["tool_calls"])
        ]

    if state["function_call"]["name"] or state["function_call"]["arguments"]:
        message["function_call"] = state["function_call"]

    reasoning = "".join(state["reasoning_parts"])
    if reasoning:
        message["reasoning_content"] = reasoning

    return message


def new_stream_state() -> dict[str, Any]:
    return {
        "content_parts": [],
        "reasoning_parts": [],
        "tool_calls": {},
        "function_call": {"name": "", "arguments": ""},
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "deepseek_base_url": DEEPSEEK_BASE_URL,
        "deepseek_model": DEEPSEEK_MODEL,
        "proxy_model_name": PROXY_MODEL_NAME,
        "proxy_model_aliases": PROXY_MODEL_ALIASES,
        "proxy_api_key_required": bool(PROXY_API_KEY),
        "cached_reasoning_keys": len(reasoning_cache),
    }


@app.get("/models")
@app.get("/v1/models")
def models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "owned_by": "local-proxy",
            }
            for model_id in proxy_model_ids()
        ],
    }


@app.post("/chat/completions", response_model=None)
@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request):
    if not proxy_authorized(request):
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid proxy API key"}})

    try:
        body = await request.json()
        cursor_model = body.get("model") or PROXY_MODEL_NAME
        deepseek_body, _stats = prepare_deepseek_body(body)
        headers = deepseek_headers()
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": {"message": str(exc)}})

    if deepseek_body.get("stream"):
        return StreamingResponse(
            stream_deepseek_response(deepseek_body, headers, cursor_model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                deepseek_url("/chat/completions"),
                headers=headers,
                json=deepseek_body,
            )
    except httpx.HTTPError as exc:
        return JSONResponse(status_code=502, content={"error": {"message": str(exc)}})

    try:
        data = response.json()
    except ValueError:
        return JSONResponse(status_code=response.status_code, content={"error": response.text})

    if response.status_code >= 400:
        return JSONResponse(status_code=response.status_code, content=data)

    for choice in data.get("choices", []):
        message = choice.get("message") or {}
        cache_reasoning(message)

    return JSONResponse(content=rewrite_response_model(data, cursor_model))


async def stream_deepseek_response(
    body: dict[str, Any],
    headers: dict[str, str],
    cursor_model: str,
):
    stream_states: dict[int, dict[str, Any]] = {}

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                deepseek_url("/chat/completions"),
                headers=headers,
                json=body,
            ) as response:
                if response.status_code >= 400:
                    error_text = (await response.aread()).decode("utf-8", errors="replace")
                    yield f"data: {normalize_json({'error': {'message': error_text}})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                async for line in response.aiter_lines():
                    if not line:
                        continue

                    if not line.startswith("data: "):
                        yield f"{line}\n\n"
                        continue

                    payload = line.removeprefix("data: ").strip()
                    if payload == "[DONE]":
                        for state in stream_states.values():
                            cache_reasoning(build_streamed_message(state))
                        yield "data: [DONE]\n\n"
                        continue

                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        yield f"{line}\n\n"
                        continue

                    if isinstance(chunk, dict):
                        rewrite_response_model(chunk, cursor_model)

                    for choice in chunk.get("choices", []):
                        index = int(choice.get("index", 0))
                        state = stream_states.setdefault(index, new_stream_state())
                        delta = choice.get("delta") or {}

                        if delta.get("content"):
                            state["content_parts"].append(delta["content"])
                        if delta.get("reasoning_content"):
                            state["reasoning_parts"].append(delta["reasoning_content"])
                        if delta.get("tool_calls"):
                            for tool_delta in delta["tool_calls"]:
                                append_tool_call_delta(state["tool_calls"], tool_delta)
                        if delta.get("function_call"):
                            append_function_call_delta(state["function_call"], delta["function_call"])

                    yield f"data: {normalize_json(chunk)}\n\n"
    except httpx.HTTPError as exc:
        yield f"data: {normalize_json({'error': {'message': str(exc)}})}\n\n"
        yield "data: [DONE]\n\n"
