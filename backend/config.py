#  Orchestration Engine - Configuration
#
#  Loads config.json and provides typed access to all settings.
#  Dot-notation path lookup: cfg("anthropic.models.haiku")
#
#  Depends on: config.json
#  Used by:    all backend modules

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "orchestration.db"

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

_config: dict = {}


def load_config(path: Path | None = None):
    """Load configuration from JSON file.

    Call during app startup. Raises FileNotFoundError if config is missing.
    """
    global _config
    config_path = path or CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            "Copy config.example.json to config.json."
        )
    with open(config_path) as f:
        _config = json.load(f)


# Auto-load if config exists (backward compat for direct imports)
if CONFIG_PATH.exists():
    load_config()


def cfg(path: str, default=None):
    """Get a config value by dot-notation path.

    Example: cfg("anthropic.models.haiku") -> "claude-haiku-4-5-20251001"
    """
    keys = path.split(".")
    val = _config
    for key in keys:
        if isinstance(val, dict) and key in val:
            val = val[key]
        else:
            return default
    return val


# ---------------------------------------------------------------------------
# Convenience constants
# ---------------------------------------------------------------------------

HOST = cfg("server.host", "0.0.0.0")
PORT = cfg("server.port", 5200)
CORS_ORIGINS = cfg("server.cors_origins", [
    "http://localhost:5173",
    f"http://localhost:{PORT}",
    "http://127.0.0.1:5173",
    f"http://127.0.0.1:{PORT}",
])

# Anthropic
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PLANNING_MODEL = cfg("anthropic.planning_model", "claude-sonnet-4-6")
MAX_CONCURRENT = cfg("anthropic.max_concurrent", 3)
API_TIMEOUT = cfg("anthropic.timeout", 120)

# Ollama
OLLAMA_HOSTS = cfg("ollama.hosts", {"local": "http://localhost:11434"})
OLLAMA_DEFAULT_MODEL = cfg("ollama.default_model", "qwen2.5-coder:14b")
OLLAMA_EMBED_MODEL = cfg("ollama.embed_model", "nomic-embed-text")
OLLAMA_EMBED_TIMEOUT = cfg("ollama.embed_timeout", 30.0)
OLLAMA_GENERATE_TIMEOUT = cfg("ollama.generate_timeout", 120.0)

# ComfyUI
COMFYUI_HOSTS = cfg("comfyui.hosts", {"local": "http://localhost:8188"})
COMFYUI_DEFAULT_CHECKPOINT = cfg("comfyui.default_checkpoint", "sd_xl_base_1.0.safetensors")

# RAG
RAG_DATABASES = cfg("rag.databases", {})
RAG_EMBED_DIMENSIONS = cfg("rag.embed_dimensions", 768)

# Budget
BUDGET_DAILY = cfg("budget.daily_limit_usd", 5.0)
BUDGET_MONTHLY = cfg("budget.monthly_limit_usd", 50.0)
BUDGET_PER_PROJECT = cfg("budget.per_project_limit_usd", 10.0)
BUDGET_WARN_PCT = cfg("budget.warn_at_pct", 80)

# Execution
MAX_CONCURRENT_TASKS = cfg("execution.max_concurrent_tasks", 3)
TICK_INTERVAL = cfg("execution.tick_interval_sec", 2.0)
MAX_TOOL_ROUNDS = cfg("execution.max_tool_rounds", 10)
DEFAULT_MAX_TOKENS = cfg("execution.default_max_tokens", 4096)

# Model pricing
MODEL_PRICING = cfg("model_pricing", {})

# Resource check
RESOURCE_CHECK_INTERVAL = cfg("resource_check_interval_sec", 30)

# Auth
AUTH_SECRET_KEY = cfg("auth.secret_key", "")
AUTH_ALGORITHM = cfg("auth.algorithm", "HS256")
AUTH_ACCESS_TOKEN_EXPIRE_MINUTES = cfg("auth.access_token_expire_minutes", 30)
AUTH_REFRESH_TOKEN_EXPIRE_DAYS = cfg("auth.refresh_token_expire_days", 7)
AUTH_ALLOW_REGISTRATION = cfg("auth.allow_registration", True)
AUTH_SSE_TOKEN_EXPIRE_SECONDS = cfg("auth.sse_token_expire_seconds", 60)


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

def validate_config():
    """Validate critical config values. Call during app startup (not at import time).

    Raises ConfigError for fatal issues, logs warnings for non-fatal ones.
    """
    import logging
    _logger = logging.getLogger("orchestration.config")

    # Fatal: JWT secret must be non-empty and at least 32 characters
    if not AUTH_SECRET_KEY or len(AUTH_SECRET_KEY) < 32:
        raise ConfigError(
            "FATAL: auth.secret_key is missing or too short in config.json "
            "(must be at least 32 characters)"
        )

    # Warning: Anthropic API key not set (Ollama-only usage is valid)
    if not ANTHROPIC_API_KEY:
        _logger.warning(
            "ANTHROPIC_API_KEY is not set. Claude API calls will fail. "
            "Set the env var or use Ollama-only mode."
        )


class ConfigError(Exception):
    """Raised when critical configuration is invalid."""
