"""Application settings loaded from config.py."""

from dataclasses import dataclass
from typing import Any


ALLOWED_REQUEST_FIELDS = {
    "model", "messages", "stream", "stream_options", "temperature",
    "top_p", "n", "max_tokens", "max_completion_tokens", "stop",
    "frequency_penalty", "presence_penalty", "tools", "tool_choice",
    "parallel_tool_calls", "response_format", "seed", "user",
    "logprobs", "top_logprobs",
}


@dataclass(frozen=True)
class Settings:
    deepseek_api_keys: list[str]
    proxy_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    proxy_model_name: str
    proxy_model_aliases: list[str]
    request_timeout: float
    max_reasoning_cache_items: int
    key_rate_limit_cooldown_seconds: float

    @classmethod
    def from_config(cls, config: Any) -> "Settings":
        # proxy_model_aliases may be a list or comma-separated string
        aliases = getattr(config, "PROXY_MODEL_ALIASES", [])
        if isinstance(aliases, str):
            aliases = [a.strip() for a in aliases.replace("\n", ",").split(",") if a.strip()]

        return cls(
            deepseek_api_keys=getattr(config, "DEEPSEEK_API_KEYS", []),
            proxy_api_key=getattr(config, "PROXY_API_KEY", ""),
            deepseek_base_url=getattr(config, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
            deepseek_model=getattr(config, "DEEPSEEK_MODEL", "deepseek-v4-pro"),
            proxy_model_name=getattr(config, "PROXY_MODEL_NAME", "deepseek-v4-pro"),
            proxy_model_aliases=aliases,
            request_timeout=float(getattr(config, "REQUEST_TIMEOUT", "300")),
            max_reasoning_cache_items=int(getattr(config, "MAX_REASONING_CACHE_ITEMS", "2000")),
            key_rate_limit_cooldown_seconds=float(getattr(config, "KEY_RATE_LIMIT_COOLDOWN_SECONDS", "60")),
        )

    @property
    def model_ids(self) -> list[str]:
        ids: list[str] = []
        for model_name in [self.proxy_model_name, *self.proxy_model_aliases]:
            if model_name and model_name not in ids:
                ids.append(model_name)
        return ids

    def deepseek_url(self, path: str) -> str:
        return f"{self.deepseek_base_url}{path}"
