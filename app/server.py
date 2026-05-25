from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.logging_config import get_logger
from app.core.settings import Settings
from app.services.key_pool import ApiKeyPool, is_rate_limit_error
from app.services.payloads import (
    SanitizedBody,
    normalize_json,
    parse_json_response,
    rewrite_response_model,
    sanitize_request_body,
)
from app.services.reasoning_store import ReasoningStore


def create_app() -> FastAPI:
    settings = Settings.from_env()
    logger = get_logger()
    key_pool = ApiKeyPool(
        keys=settings.deepseek_api_keys,
        cooldown_seconds=settings.key_rate_limit_cooldown_seconds,
        logger=logger,
    )
    reasoning_store = ReasoningStore(max_items=settings.max_reasoning_cache_items)

    app = FastAPI(title="DeepSeek Cursor Proxy")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def proxy_authorized(request: Request) -> bool:
        if not settings.proxy_api_key:
            return True

        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        return token == settings.proxy_api_key

    def deepseek_headers(api_key: str) -> dict[str, str]:
        if not api_key:
            raise RuntimeError("DeepSeek API key is empty")

        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def new_stream_state() -> dict[str, Any]:
        return {
            "content_parts": [],
            "reasoning_parts": [],
            "tool_calls": {},
            "function_call": {"name": "", "arguments": ""},
        }

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

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "deepseek_base_url": settings.deepseek_base_url,
            "deepseek_model": settings.deepseek_model,
            "proxy_model_name": settings.proxy_model_name,
            "proxy_model_aliases": settings.proxy_model_aliases,
            "proxy_api_key_required": bool(settings.proxy_api_key),
            "deepseek_api_key_count": key_pool.count,
            "cooling_key_count": key_pool.cooling_count(),
            "cached_reasoning_keys": reasoning_store.size(),
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
                for model_id in settings.model_ids
            ],
        }

    async def stream_deepseek_response(
        body: dict[str, Any],
        cursor_model: str,
        session_hash: str,
        assistant_position: int,
        t0: float = 0,
    ):
        attempts = max(1, key_pool.count)

        async with httpx.AsyncClient(timeout=None) as client:
            for attempt in range(attempts):
                key_index, api_key = key_pool.next_key()
                headers = deepseek_headers(api_key)
                stream_states: dict[int, dict[str, Any]] = {}

                logger.info(
                    "upstream stream attempt=%d/%d using %s",
                    attempt + 1,
                    attempts,
                    key_pool.label(key_index),
                )

                try:
                    should_retry = False
                    retry_reason = ""

                    async with client.stream(
                        "POST",
                        settings.deepseek_url("/chat/completions"),
                        headers=headers,
                        json=body,
                    ) as response:
                        if response.status_code >= 400:
                            error_text = (await response.aread()).decode("utf-8", errors="replace")
                            if is_rate_limit_error(response.status_code, error_text) and attempt < attempts - 1:
                                should_retry = True
                                retry_reason = error_text
                            else:
                                yield f"data: {normalize_json({'error': {'message': error_text}})}\n\n"
                                yield "data: [DONE]\n\n"
                                return

                        if should_retry:
                            key_pool.mark_rate_limited(key_index, retry_reason)
                            continue

                        async for line in response.aiter_lines():
                            if not line:
                                continue

                            if not line.startswith("data: "):
                                yield f"{line}\n\n"
                                continue

                            payload = line.removeprefix("data: ").strip()
                            if payload == "[DONE]":
                                for state in stream_states.values():
                                    built = build_streamed_message(state)
                                    reasoning_store.cache_message(built)
                                    reasoning_store.cache_position(
                                        session_hash, assistant_position,
                                        built.get("reasoning_content", ""),
                                    )

                                if t0:
                                    logger.info(
                                        "stream_done model=%s elapsed=%.1fs reasoning_cache=%d pos=%s:%d",
                                        cursor_model,
                                        time.monotonic() - t0,
                                        reasoning_store.size(),
                                        session_hash,
                                        assistant_position,
                                    )

                                yield "data: [DONE]\n\n"
                                return

                            payload_dict = parse_json_response(payload)
                            rewrite_response_model(payload_dict, cursor_model)

                            for choice in payload_dict.get("choices", []):
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

                            yield f"data: {normalize_json(payload_dict)}\n\n"

                        return
                except httpx.HTTPError as exc:
                    yield f"data: {normalize_json({'error': {'message': str(exc)}})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

        yield f"data: {normalize_json({'error': {'message': 'All upstream API keys are rate limited'}})}\n\n"
        yield "data: [DONE]\n\n"

    @app.post("/chat/completions", response_model=None)
    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request):
        if not proxy_authorized(request):
            return JSONResponse(status_code=401, content={"error": {"message": "Invalid proxy API key"}})

        t0 = time.monotonic()

        try:
            body = await request.json()
            cursor_model = body.get("model") or settings.proxy_model_name
            san = sanitize_request_body(body, cursor_model, settings, reasoning_store, logger)
        except Exception as exc:
            return JSONResponse(status_code=400, content={"error": {"message": str(exc)}})

        logger.info(
            "req model=%s stream=%s msgs=%d tools=%d reasoning_inject=%s",
            cursor_model,
            san.body.get("stream", False),
            len(san.body.get("messages", [])),
            len(san.body.get("tools", [])),
            san.stats,
        )

        if san.body.get("stream"):
            return StreamingResponse(
                stream_deepseek_response(
                    san.body, cursor_model,
                    san.session_hash, san.assistant_position,
                    t0,
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        attempts = max(1, key_pool.count)
        last_status = 500
        last_data: dict[str, Any] = {"error": {"message": "No upstream response"}}

        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            for attempt in range(attempts):
                key_index, api_key = key_pool.next_key()
                headers = deepseek_headers(api_key)

                logger.info(
                    "upstream attempt=%d/%d using %s",
                    attempt + 1,
                    attempts,
                    key_pool.label(key_index),
                )

                try:
                    response = await client.post(
                        settings.deepseek_url("/chat/completions"),
                        headers=headers,
                        json=san.body,
                    )
                except httpx.HTTPError as exc:
                    return JSONResponse(status_code=502, content={"error": {"message": str(exc)}})

                last_status = response.status_code
                last_data = parse_json_response(response.text)

                if is_rate_limit_error(last_status, normalize_json(last_data)) and attempt < attempts - 1:
                    key_pool.mark_rate_limited(key_index, normalize_json(last_data))
                    continue

                break

        if last_status >= 400:
            return JSONResponse(status_code=last_status, content=last_data)

        for choice in last_data.get("choices", []):
            message = choice.get("message") or {}
            reasoning_store.cache_message(message)
            reasoning_store.cache_position(
                san.session_hash, san.assistant_position,
                message.get("reasoning_content", ""),
            )

        logger.info(
            "res model=%s status=%d elapsed=%.1fs tokens=%s reasoning_cache=%d pos=%s:%d",
            cursor_model,
            last_status,
            time.monotonic() - t0,
            last_data.get("usage", {}).get("total_tokens", "?"),
            reasoning_store.size(),
            san.session_hash,
            san.assistant_position,
        )

        return JSONResponse(content=rewrite_response_model(last_data, cursor_model))

    return app


app = create_app()
