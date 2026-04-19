from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypedDict


@dataclass
class WatchlistEntry:
    keyword: str
    fair_price: float
    max_price: float
    notes: str = ""


@dataclass
class Listing:
    listing_id: str
    title: str
    price: float
    description: str
    condition: str
    location: str
    listing_url: str
    scraped_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SeenRecord(TypedDict):
    price: float
    alerted: bool
    last_seen: str  # ISO-8601 UTC


SeenState = dict[str, SeenRecord]


@dataclass
class DealResult:
    worth_buying: bool
    reason: str
    confidence: float  # 0.0–1.0
