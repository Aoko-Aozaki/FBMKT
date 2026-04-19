from __future__ import annotations

import html

import requests
from loguru import logger

from src.models import DealResult, Listing, WatchlistEntry

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

MESSAGE_TEMPLATE = (
    "<b>Deal Alert</b>\n"
    "{title}\n"
    "💰 <b>${price:.0f}</b>  |  Fair price: ~${fair_price:.0f}  (<b>{discount:+.0f}%</b>)\n"
    "📍 {location}\n"
    "🏷 {condition}\n"
    "\n"
    "🤖 {reason}\n"
    "\n"
    "🔗 {listing_url}"
)


def send_alert(
    bot_token: str,
    chat_id: str,
    listing: Listing,
    entry: WatchlistEntry,
    result: DealResult,
) -> None:
    text = _format_message(listing, entry, result)
    send_text(bot_token, chat_id, text)
    logger.info(f"Telegram alert sent for '{listing.title}'")


def send_text(
    bot_token: str,
    chat_id: str,
    text: str,
) -> None:
    url = _TELEGRAM_API.format(token=bot_token)
    response = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    response.raise_for_status()


def _format_message(
    listing: Listing,
    entry: WatchlistEntry,
    result: DealResult,
) -> str:
    if entry.fair_price > 0:
        discount = (listing.price - entry.fair_price) / entry.fair_price * 100
    else:
        discount = 0.0

    # Escape every user-/LLM-supplied field — unescaped '<' or '&' breaks
    # Telegram's HTML parser and raises HTTP 400.
    return MESSAGE_TEMPLATE.format(
        title=html.escape(listing.title),
        price=listing.price,
        fair_price=entry.fair_price,
        discount=discount,
        location=html.escape(listing.location or "Unknown"),
        condition=html.escape(listing.condition or "Not specified"),
        reason=html.escape(result.reason),
        listing_url=listing.listing_url,
    )
