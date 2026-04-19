from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger

from src.models import WatchlistEntry


@dataclass
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    openai_api_key: str
    fb_location: str
    poll_interval_min: int = 15
    auth_state_path: Path = field(default_factory=lambda: Path("auth_state.json"))
    seen_listings_path: Path = field(default_factory=lambda: Path("seen_listings.json"))
    watchlist_path: Path = field(default_factory=lambda: Path("watchlist.yaml"))
    similarity_threshold: float = 0.60
    confidence_threshold: float = 0.70
    scrape_delay_min: int = 10
    scrape_delay_max: int = 30
    stale_days: int = 30
    log_level: str = "INFO"


def load_settings(env_path: Path | None = None) -> Settings:
    load_dotenv(dotenv_path=env_path)

    def _require(key: str) -> str:
        val = os.getenv(key)
        if not val:
            raise ValueError(f"Missing required environment variable: {key}")
        return val

    poll_interval_min = int(os.getenv("POLL_INTERVAL_MIN", "15"))
    if poll_interval_min < 15:
        logger.warning(
            f"POLL_INTERVAL_MIN={poll_interval_min} is below the recommended 15 "
            "minutes — risk of FB rate-limiting or ban increases significantly."
        )

    return Settings(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
        openai_api_key=_require("OPENAI_API_KEY"),
        fb_location=_require("FB_LOCATION"),
        poll_interval_min=poll_interval_min,
        auth_state_path=Path(os.getenv("AUTH_STATE_PATH", "auth_state.json")),
        seen_listings_path=Path(os.getenv("SEEN_LISTINGS_PATH", "seen_listings.json")),
        watchlist_path=Path(os.getenv("WATCHLIST_PATH", "watchlist.yaml")),
        similarity_threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.60")),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.70")),
        scrape_delay_min=int(os.getenv("SCRAPE_DELAY_MIN", "10")),
        scrape_delay_max=int(os.getenv("SCRAPE_DELAY_MAX", "30")),
        stale_days=int(os.getenv("STALE_DAYS", "30")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


def load_watchlist(path: Path | None = None) -> list[WatchlistEntry]:
    wl_path = path or Path("watchlist.yaml")
    if not wl_path.exists():
        raise FileNotFoundError(f"watchlist.yaml not found at {wl_path}")

    with wl_path.open() as f:
        data = yaml.safe_load(f)

    entries = []
    for item in data.get("watchlist", []):
        if "keyword" not in item:
            raise ValueError(f"watchlist entry missing 'keyword': {item}")
        if "fair_price" not in item:
            raise ValueError(f"watchlist entry missing 'fair_price': {item}")
        entries.append(
            WatchlistEntry(
                keyword=item["keyword"],
                fair_price=float(item["fair_price"]),
                max_price=float(item.get("max_price", float("inf"))),
                notes=item.get("notes", ""),
            )
        )
    return entries
