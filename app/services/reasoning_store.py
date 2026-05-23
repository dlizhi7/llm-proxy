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


def _normalize_content(content: Any) -> str:
    return "" if content is None else str(content).strip()


def _normalize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []

    normalized = []
    for tool_call in tool_calls:
        function = tool_call.get("function") or {}
        normalized.append(
            {
                "id": str(tool_call.get("id", "")),
                "type": tool_call.get("type", "function"),
                "function": {
                    "name": str(function.get("name", "")),
                    "arguments": str(function.get("arguments", "")).strip(),
                },
            }
        )
    return normalized


def _normalize_function_call(function_call: Any) -> dict[str, Any] | None:
    if not isinstance(function_call, dict):
        return None
    return {
        "name": str(function_call.get("name", "")),
        "arguments": str(function_call.get("arguments", "")).strip(),
    }


def _extract_tool_structure(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract only function names and argument lengths, ignoring actual argument values."""
    return [
        {
            "name": tc["function"]["name"],
            "args_len": len(tc["function"]["arguments"]),
        }
        for tc in tool_calls
    ]


class ReasoningStore:
    """Store and reinject reasoning_content for multi-turn thinking-model compatibility.

    Uses a multi-level matching strategy:
      1. content prefix  (first 200 chars) - catches minor whitespace diffs
      2. content hash    (full content for exact matches)
      3. tool_structure  (function names + argument lengths)
      4. tool_ids        (just the id strings)
      5. position key    (session:assistant_index — most reliable fallback)
    """

    def __init__(self, max_items: int = 2000) -> None:
        self._max_items = max_items
        self._cache: OrderedDict[str, str] = OrderedDict()
        # Position key → reasoning_content (session_hash:pos:index)
        self._pos_cache: OrderedDict[str, str] = OrderedDict()

    def size(self) -> int:
        return len(self._cache) + len(self._pos_cache)

    # ------------------------------------------------------------------ #
    #  key generation
    # ------------------------------------------------------------------ #

    def _assistant_identity(self, message: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": _normalize_content(message.get("content")),
            "tool_calls": _normalize_tool_calls(message.get("tool_calls")),
            "function_call": _normalize_function_call(message.get("function_call")),
            "name": str(message.get("name", "")),
        }

    def _generate_lookup_keys(self, message: dict[str, Any]) -> list[str]:
        """Produce ordered fallback keys for a message (most precise → loosest)."""
        identity = self._assistant_identity(message)
        keys: list[str] = []

        # 1. content prefix (robust against trailing whitespace changes)
        content = identity["content"]
        if content:
            keys.append(f"cpref:{_sha256({'prefix': content[:200]})}")

        # 2. full content (exact match)
        if content:
            keys.append(f"cfull:{_sha256({'content': content})}")

        # 3. tool structure (fn names + arg lengths — ignores arg value diffs)
        tool_calls = identity["tool_calls"]
        if tool_calls:
            keys.append(f"tstruct:{_sha256({'tool_struct': _extract_tool_structure(tool_calls)})}")

        # 4. tool ids only
        tool_ids = [tc["id"] for tc in tool_calls if tc.get("id")]
        if tool_ids:
            keys.append(f"tids:{_sha256({'tool_ids': tool_ids})}")

        # 5. exact match (last resort, most strict)
        keys.append(f"exact:{_sha256(identity)}")

        return keys

    def _generate_store_keys(self, message: dict[str, Any]) -> list[str]:
        """Same as _generate_lookup_keys but may include extra store-only keys."""
        return self._generate_lookup_keys(message)

    # ------------------------------------------------------------------ #
    #  caching
    # ------------------------------------------------------------------ #

    def _put(self, key: str, reasoning: str) -> None:
        self._cache[key] = reasoning
        self._cache.move_to_end(key)

    def _evict(self) -> None:
        while len(self._cache) > self._max_items:
            self._cache.popitem(last=False)

    def cache_message(self, message: dict[str, Any]) -> None:
        reasoning = message.get("reasoning_content")
        if not reasoning:
            return

        for key in self._generate_store_keys(message):
            self._put(key, reasoning)

        self._evict()

    def cache_position(self, session_hash: str, assistant_index: int, reasoning: str) -> None:
        """Cache reasoning_content by session + assistant position."""
        if not reasoning:
            return

        key = f"{session_hash}:pos:{assistant_index}"
        self._pos_cache[key] = reasoning
        self._pos_cache.move_to_end(key)

        while len(self._pos_cache) > self._max_items:
            self._pos_cache.popitem(last=False)

    # ------------------------------------------------------------------ #
    #  session hash
    # ------------------------------------------------------------------ #

    def session_hash(self, messages: list[dict[str, Any]]) -> str:
        """Stable session ID from the first 3 user message prefixes.

        Cursor Agent mode re-sends full history every request, so the
        first few user messages act as a durable conversation fingerprint.
        """
        prefixes: list[str] = []
        for msg in messages:
            if msg.get("role") == "user":
                content = str(msg.get("content", ""))
                if content:
                    prefixes.append(content[:100])
                if len(prefixes) >= 3:
                    break
        return _sha256({"user_prefixes": prefixes})[:16] if prefixes else "no_session"

    # ------------------------------------------------------------------ #
    #  injection
    # ------------------------------------------------------------------ #

    def find_reasoning(self, message: dict[str, Any]) -> str | None:
        """Multi-level fallback lookup."""
        for key in self._generate_lookup_keys(message):
            reasoning = self._cache.get(key)
            if reasoning:
                self._cache.move_to_end(key)
                return reasoning
        return None

    def inject_into_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, int], str, int]:
        """Inject reasoning_content into assistant messages.

        Returns:
            patched messages
            stats        {'assistant_messages', 'injected', 'injected_pos',
                          'already_present', 'missing'}
            session_hash  for later position caching
            next_assistant_index  index the NEXT assistant response will have
        """
        patched = deepcopy(messages)
        session = self.session_hash(messages)
        stats = {
            "assistant_messages": 0,
            "injected": 0,
            "injected_pos": 0,
            "already_present": 0,
            "missing": 0,
        }

        for message in patched:
            if message.get("role") != "assistant":
                continue

            assistant_idx = stats["assistant_messages"]
            stats["assistant_messages"] += 1

            # Skip if already has reasoning
            if message.get("reasoning_content"):
                stats["already_present"] += 1
                self.cache_message(message)
                continue

            # Strategy A: position-based lookup (most reliable for Cursor)
            pos_key = f"{session}:pos:{assistant_idx}"
            reasoning = self._pos_cache.get(pos_key)
            if reasoning:
                message["reasoning_content"] = reasoning
                self._pos_cache.move_to_end(pos_key)
                stats["injected_pos"] += 1
                stats["injected"] += 1
                continue

            # Strategy B: multi-level fingerprint fallback
            reasoning = self.find_reasoning(message)
            if reasoning:
                message["reasoning_content"] = reasoning
                stats["injected"] += 1
            else:
                stats["missing"] += 1

        return patched, stats, session, stats["assistant_messages"]
