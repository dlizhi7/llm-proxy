from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
from typing import Any


def _normalize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_normalize_json(value).encode("utf-8")).hexdigest()


def _normalize_content(content: Any) -> str | list[Any]:
    return "" if content is None else content


def _normalize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
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


def _normalize_function_call(function_call: Any) -> dict[str, Any] | None:
    if not isinstance(function_call, dict):
        return None
    return {
        "name": function_call.get("name", ""),
        "arguments": function_call.get("arguments", ""),
    }


class ReasoningStore:
    """Store and reinject reasoning_content for multi-turn compatibility."""

    def __init__(self, max_items: int = 2000) -> None:
        self._max_items = max_items
        self._cache: OrderedDict[str, str] = OrderedDict()

    def size(self) -> int:
        return len(self._cache)

    def _assistant_identity(self, message: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": _normalize_content(message.get("content")),
            "tool_calls": _normalize_tool_calls(message.get("tool_calls")),
            "function_call": _normalize_function_call(message.get("function_call")),
            "name": message.get("name", ""),
        }

    def _keys_for_message(self, message: dict[str, Any]) -> list[str]:
        identity = self._assistant_identity(message)
        keys = [f"exact:{_sha256(identity)}"]

        content = identity["content"]
        if content:
            keys.append(f"content:{_sha256({'content': content})}")

        tool_calls = identity["tool_calls"]
        if tool_calls:
            keys.append(f"tools:{_sha256({'tool_calls': tool_calls})}")
            tool_ids = [tool_call.get("id", "") for tool_call in tool_calls if tool_call.get("id")]
            if tool_ids:
                keys.append(f"tool_ids:{_sha256({'tool_ids': tool_ids})}")

        function_call = identity["function_call"]
        if function_call:
            keys.append(f"function:{_sha256({'function_call': function_call})}")

        return keys

    def cache_message(self, message: dict[str, Any]) -> None:
        reasoning = message.get("reasoning_content")
        if not reasoning:
            return

        for key in self._keys_for_message(message):
            self._cache[key] = reasoning
            self._cache.move_to_end(key)

        while len(self._cache) > self._max_items:
            self._cache.popitem(last=False)

    def inject_into_messages(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
        patched_messages = deepcopy(messages)
        stats = {"assistant_messages": 0, "injected": 0, "already_present": 0, "missing": 0}

        for message in patched_messages:
            if message.get("role") != "assistant":
                continue

            stats["assistant_messages"] += 1

            if message.get("reasoning_content"):
                stats["already_present"] += 1
                self.cache_message(message)
                continue

            reasoning = self.find_reasoning(message)
            if reasoning:
                message["reasoning_content"] = reasoning
                stats["injected"] += 1
            else:
                stats["missing"] += 1

        return patched_messages, stats

    def find_reasoning(self, message: dict[str, Any]) -> str | None:
        for key in self._keys_for_message(message):
            reasoning = self._cache.get(key)
            if reasoning:
                self._cache.move_to_end(key)
                return reasoning
        return None
