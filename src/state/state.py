from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.models import SeenState


def load_state(path: Path) -> SeenState:
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)


def save_state(state: SeenState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def is_new_or_price_dropped(
    listing_id: str,
    current_price: float,
    state: SeenState,
) -> bool:
    if listing_id not in state:
        return True
    return current_price < state[listing_id]["price"]


def upsert_listing(
    listing_id: str,
    price: float,
    alerted: bool,
    state: SeenState,
) -> None:
    state[listing_id] = {
        "price": price,
        "alerted": alerted,
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }


def prune_stale(state: SeenState, stale_days: int) -> SeenState:
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    return {
        lid: record
        for lid, record in state.items()
        if datetime.fromisoformat(record["last_seen"]) > cutoff
    }
