from __future__ import annotations

import sys
from datetime import datetime
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from openai import OpenAI

from src.config import Settings, load_settings, load_watchlist
from src.llm.evaluator import evaluate_deal
from src.matcher.matcher import embed_watchlist, load_model, match_watchlist
from src.models import SeenState, WatchlistEntry
from src.notifier.notifier import send_alert, send_text
from src.scraper.scraper import (
    SessionExpiredError,
    build_browser_session,
    fetch_listing_detail,
    scrape_keyword,
)
from src.state.state import (
    is_new_or_price_dropped,
    load_state,
    prune_stale,
    save_state,
    upsert_listing,
)

# Module-level singletons — initialised once in main(), reused across runs
_settings: Settings | None = None
_openai_client: OpenAI | None = None
_model: Any = None


def run_pipeline_with(
    settings: Settings,
    model: Any,
    client: OpenAI,
    watchlist: list[WatchlistEntry],
    state: SeenState,
) -> SeenState:
    """
    Core pipeline logic with all dependencies injected.
    Returns the updated state (not yet persisted — caller must call save_state).
    Designed to be easily unit-tested by passing mock objects.
    """
    watchlist_embeddings = embed_watchlist(model, watchlist)
    logger.info(f"Starting pipeline run for {len(watchlist)} watchlist entries")

    session = build_browser_session(settings.auth_state_path)
    context = session.context
    try:
        for entry in watchlist:
            try:
                listings = scrape_keyword(
                    context,
                    entry,
                    settings.fb_location,
                    (settings.scrape_delay_min, settings.scrape_delay_max),
                )
                candidates = [
                    listing
                    for listing in listings
                    if is_new_or_price_dropped(listing.listing_id, listing.price, state)
                ]
                logger.info(
                    f"'{entry.keyword}': {len(listings)} scraped, "
                    f"{len(candidates)} new/price-dropped"
                )

                # Record every listing seen (price history). Preserve existing
                # `alerted` flag when price is unchanged — otherwise a previously
                # alerted listing's state would be silently corrupted every run.
                for listing in listings:
                    existing = state.get(listing.listing_id)
                    preserved_alerted = bool(
                        existing
                        and existing["price"] == listing.price
                        and existing.get("alerted")
                    )
                    upsert_listing(
                        listing.listing_id, listing.price, preserved_alerted, state
                    )

                for listing in candidates:
                    # matcher re-confirms relevance (FB search can return noise)
                    matched = match_watchlist(
                        model,
                        watchlist_embeddings,
                        watchlist,
                        listing.title,
                        settings.similarity_threshold,
                    )
                    if matched is None:
                        logger.debug(f"No watchlist match for '{listing.title}' — skipping")
                        continue
                    if listing.price > matched.max_price:
                        logger.debug(
                            f"Price ${listing.price} > max ${matched.max_price} — skipping"
                        )
                        continue

                    listing = fetch_listing_detail(context, listing)
                    result = evaluate_deal(client, listing, matched)
                    logger.info(
                        f"LLM: worth_buying={result.worth_buying} "
                        f"confidence={result.confidence:.2f} for '{listing.title}'"
                    )

                    if (
                        result.worth_buying
                        and result.confidence >= settings.confidence_threshold
                    ):
                        send_alert(
                            settings.telegram_bot_token,
                            settings.telegram_chat_id,
                            listing,
                            matched,
                            result,
                        )
                        upsert_listing(listing.listing_id, listing.price, True, state)
                        logger.info(f"Alert sent for '{listing.title}'")

            except SessionExpiredError:
                logger.error("FB session expired — sending Telegram alert and aborting run")
                try:
                    send_text(
                        settings.telegram_bot_token,
                        settings.telegram_chat_id,
                        "FB session expired. Run `python login.py` to renew auth_state.json.",
                    )
                except Exception:
                    pass
                break
            except Exception:
                logger.exception(f"Error processing keyword '{entry.keyword}' — continuing")

    finally:
        session.close()

    return state


def run_pipeline() -> None:
    """
    Public entry point called by the scheduler.
    Initialises module-level singletons on first call, then delegates to run_pipeline_with().
    """
    global _settings, _openai_client, _model

    if _settings is None:
        _settings = load_settings()
    settings = _settings

    if _model is None:
        logger.info("Loading sentence-transformers model...")
        _model = load_model()
    model = _model

    if _openai_client is None:
        _openai_client = OpenAI(
            api_key=settings.openai_api_key,
            base_url="https://api.deepseek.com/v1",
        )
    client = _openai_client

    watchlist = load_watchlist(settings.watchlist_path)
    state = load_state(settings.seen_listings_path)

    try:
        state = run_pipeline_with(settings, model, client, watchlist, state)
    finally:
        state = prune_stale(state, settings.stale_days)
        save_state(state, settings.seen_listings_path)
        logger.info("State saved")


def main() -> None:
    global _settings
    _settings = load_settings()

    logger.remove()
    logger.add(sys.stderr, level=_settings.log_level)
    logger.info("FB Marketplace deal-alert starting up")

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_pipeline,
        trigger=IntervalTrigger(minutes=_settings.poll_interval_min),
        id="pipeline",
        max_instances=1,
        misfire_grace_time=60,
        next_run_time=datetime.now(),
    )
    logger.info(
        f"Scheduler running every {_settings.poll_interval_min} minutes "
        "(first run starts immediately)"
    )
    scheduler.start()


if __name__ == "__main__":
    main()
