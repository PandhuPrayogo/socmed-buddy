from __future__ import annotations

import logging
import sys
from pathlib import Path

# Add project root to sys.path so 'src' module imports resolve cleanly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.bot import create_bot_application
from src.config import settings
from src.db import init_db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("agentic-ai")


def main() -> None:
    logger.info("Initializing Agentic AI System database...")
    init_db(settings.db_path)

    missing_keys = []
    if not settings.telegram_bot_token:
        missing_keys.append("TELEGRAM_BOT_TOKEN")
    if not settings.gemini_api_key and not settings.openai_api_key:
        missing_keys.append("GEMINI_API_KEY")

    scraper_keys = []
    if settings.youtube_api_key:
        scraper_keys.append("YouTube Data API v3")
    if settings.apify_token:
        scraper_keys.append("Apify (TikTok Scraper)")
    if settings.meta_access_token and settings.ig_user_id:
        scraper_keys.append("Meta Graph API (Instagram Business/Creator)")

    if scraper_keys:
        logger.info(f"Active Data Scraper Connectors: {', '.join(scraper_keys)}")
    else:
        logger.warning(
            "Notice: No scraper API keys configured in .env (YOUTUBE_API_KEY, APIFY_TOKEN, META_ACCESS_TOKEN). "
            "You can provide them when you want automated scraping for those platforms."
        )

    if missing_keys:
        logger.warning(
            f"Notice: The following keys are not configured in .env: {', '.join(missing_keys)}. "
            "Please configure .env to enable full functionality."
        )

    if not settings.telegram_bot_token:
        logger.error("Application cannot run without TELEGRAM_BOT_TOKEN. Please set it in .env.")
        sys.exit(1)

    logger.info(f"Normal Task LLM (Flash): {settings.gemini_normal_model}")
    logger.info(f"Specific Task LLM (Synthesis): {settings.gemini_specific_model}")
    logger.info("Starting Telegram Bot with JobQueue scheduler...")
    app = create_bot_application()
    app.run_polling()


if __name__ == "__main__":
    main()
