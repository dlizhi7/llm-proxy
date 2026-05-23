from __future__ import annotations

import json
from dataclasses import dataclass
from logging import Logger
from typing import Any

from app.core.settings import ALLOWED_REQUEST_FIELDS, Settings
from app.services.reasoning_store import ReasoningStore


def normalize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_cursor_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize potential flat cursor tool schema to OpenAI nested schema."""
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            normalized.append(tool)
            continue

        name = tool.get("name", "")
        description = tool.get("description")
        parameters = tool.get("parameters", {})
        if not name:
            normalized.append(tool)
            continue

        entry: dict[str, Any] = {
            "type": "function",
            "function": {"name": name, "parameters": parameters},
        }
        if description is not None:
            entry["function"]["description"] = description
        normalized.append(entry)

    return normalized


@dataclass
class SanitizedBody:
    body: dict[str, Any]
    stats: dict[str, int]
    session_hash: str
    assistant_position: int


def sanitize_request_body(
    body: dict[str, Any],
    settings: Settings,
    reasoning_store: ReasoningStore,
    logger: Logger,
) -> SanitizedBody:
    """Prepare a provider-safe body while preserving key chat behavior.

    Returns a SanitizedBody with session/position info for later caching.
    """
    sanitized: dict[str, Any] = {}
    stripped: list[str] = []

    for key, value in body.items():
        if key in ALLOWED_REQUEST_FIELDS:
            sanitized[key] = value
        else:
            stripped.append(key)

    sanitized["model"] = settings.deepseek_model

    if "tools" in sanitized and isinstance(sanitized["tools"], list):
        sanitized["tools"] = normalize_cursor_tools(sanitized["tools"])

    messages = sanitized.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("request body field 'messages' must be a list")

    patched_msgs, stats, session_hash, next_pos = reasoning_store.inject_into_messages(messages)
    sanitized["messages"] = patched_msgs

    if stripped:
        logger.info("stripped unknown request fields: %s", stripped)

    return SanitizedBody(
        body=sanitized,
        stats=stats,
        session_hash=session_hash,
        assistant_position=next_pos,
    )


def rewrite_response_model(data: dict[str, Any], cursor_model: str) -> dict[str, Any]:
    if cursor_model:
        data["model"] = cursor_model
    return data


def parse_json_response(response_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        return {"error": response_text}

    return payload if isinstance(payload, dict) else {"error": payload}
