"""Runtime configuration, loaded from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
SANDBOX_DIR = APP_DIR / "sandbox"
INVOICES_DIR = SANDBOX_DIR / "data" / "invoices"
STATIC_DIR = APP_DIR / "static"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (avoids an extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(PROJECT_ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", "").strip())
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini")
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    )
    force_demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", False))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    finance_api_url: str = field(default_factory=lambda: os.getenv("FINANCE_API_URL", "").rstrip("/"))
    finance_db_path: Path = field(
        default_factory=lambda: Path(os.getenv("FINANCE_DB_PATH", str(SANDBOX_DIR / "finance.db")))
    )
    invoices_dir: Path = field(default_factory=lambda: Path(os.getenv("INVOICES_DIR", str(INVOICES_DIR))))
    approval_threshold: float = field(default_factory=lambda: float(os.getenv("APPROVAL_THRESHOLD", "50000")))
    step_delay: float = field(default_factory=lambda: float(os.getenv("STEP_DELAY_SECONDS", "0.7")))
    max_steps: int = field(default_factory=lambda: int(os.getenv("MAX_AGENT_STEPS", "16")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("MAX_TOOL_RETRIES", "3")))
    approval_timeout: float = field(default_factory=lambda: float(os.getenv("APPROVAL_TIMEOUT_SECONDS", "900")))

    def __post_init__(self) -> None:
        if not self.finance_api_url:
            self.finance_api_url = f"http://127.0.0.1:{self.port}"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key) and not self.force_demo_mode
