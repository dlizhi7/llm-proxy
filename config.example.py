"""llm-proxy configuration template. Copy this to config.py and fill in your values."""

# DeepSeek API keys. The proxy rotates keys and temporarily cools down
# a key after rate limit errors.
DEEPSEEK_API_KEYS = [
    "sk-your-deepseek-key-1",
    "sk-your-deepseek-key-2",
    "sk-your-deepseek-key-3",
]

# Optional local proxy key. Cursor's API Key should match this, not the real DeepSeek key.
PROXY_API_KEY = "proxy"

# DeepSeek OpenAI-compatible endpoint.
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# Default upstream model when Cursor doesn't specify one.
DEEPSEEK_MODEL = "deepseek-v4-pro"

# Model name exposed by GET /v1/models.
PROXY_MODEL_NAME = "deepseek-v4-pro"

# Extra model IDs returned by GET /v1/models.
PROXY_MODEL_ALIASES = ["deepseek-v4-pro", "deepseek-v4-flash"]

# HTTP timeout in seconds for non-streaming requests.
REQUEST_TIMEOUT = 300

# Cooldown time for a key after rate limit errors.
KEY_RATE_LIMIT_COOLDOWN_SECONDS = 60

# Maximum number of cached reasoning fingerprints kept in memory.
MAX_REASONING_CACHE_ITEMS = 2000

# Local server port used by run.sh.
PORT = 8080
