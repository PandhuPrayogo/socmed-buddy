from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Set LocalAppData directory inside workspace for sandbox and Windows permission safety
fallback_appdata = BASE_DIR / ".appdata"
fallback_appdata.mkdir(parents=True, exist_ok=True)
os.environ["LOCALAPPDATA"] = str(fallback_appdata)

load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_allowed_chat_ids: list[int] = None  # type: ignore

    # Gemini Models & API Configuration
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
    gemini_normal_model: str = os.getenv("GEMINI_NORMAL_MODEL", "gemini/gemini-2.5-flash")
    gemini_specific_model: str = os.getenv("GEMINI_SPECIFIC_MODEL", "gemini/gemini-2.5-pro")

    # Optional Fallback/Legacy LLM
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "gemini/gemini-2.5-flash")

    # Social Media Data Scrapers & Connectors
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")
    apify_token: str = os.getenv("APIFY_TOKEN", "")
    meta_access_token: str = os.getenv("META_ACCESS_TOKEN", "")
    ig_user_id: str = os.getenv("IG_USER_ID", "")

    # Budget & Governance
    monthly_budget_usd: float = float(os.getenv("MONTHLY_BUDGET_USD", "50.0"))
    db_path: Path = BASE_DIR / os.getenv("DATABASE_PATH", "agentic.db")

    def __post_init__(self):
        raw_ids = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
        parsed_ids = [int(i.strip()) for i in raw_ids.split(",") if i.strip().isdigit()]
        object.__setattr__(self, "telegram_allowed_chat_ids", parsed_ids)

        # Sync GEMINI_API_KEY to os.environ so CrewAI/Google GenAI picks it up seamlessly
        if self.gemini_api_key:
            os.environ["GEMINI_API_KEY"] = self.gemini_api_key
            os.environ["GOOGLE_API_KEY"] = self.gemini_api_key


settings = Settings()
