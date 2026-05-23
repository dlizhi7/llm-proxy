from dataclasses import dataclass
from pathlib import Path
import os


ALLOWED_REQUEST_FIELDS = {
    "model", "messages", "stream", "temperature", "top_p", "n",
    "max_tokens", "max_completion_tokens", "stop", "frequency_penalty",
    "presence_penalty", "tools", "tool_choice", "parallel_tool_calls",
    "response_format", "seed", "user", "logprobs", "top_logprobs",
}


def load_dotenv(path: Path) -> None:
    """Tiny .env loader to keep dependencies minimal."""
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


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
    def from_env(cls, dotenv_path: str = ".env") -> "Settings":
        load_dotenv(Path(dotenv_path))

        deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")
        deepseek_api_keys = parse_csv_env("DEEPSEEK_API_KEYS")
        if not deepseek_api_keys and deepseek_api_key:
            deepseek_api_keys = [deepseek_api_key]

        proxy_model_name = os.getenv("PROXY_MODEL_NAME", "deepseek-v4-pro")
        proxy_model_aliases = parse_csv_env("PROXY_MODEL_ALIASES")
        if not proxy_model_aliases:
            proxy_model_aliases = [proxy_model_name]

        return cls(
            deepseek_api_keys=deepseek_api_keys,
            proxy_api_key=os.getenv("PROXY_API_KEY", ""),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
            proxy_model_name=proxy_model_name,
            proxy_model_aliases=proxy_model_aliases,
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", "300")),
            max_reasoning_cache_items=int(os.getenv("MAX_REASONING_CACHE_ITEMS", "2000")),
            key_rate_limit_cooldown_seconds=float(os.getenv("KEY_RATE_LIMIT_COOLDOWN_SECONDS", "60")),
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
