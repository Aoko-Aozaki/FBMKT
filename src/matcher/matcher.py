from __future__ import annotations

import numpy as np
from loguru import logger
from sentence_transformers import SentenceTransformer

from src.models import WatchlistEntry


def load_model(model_name: str = "all-MiniLM-L6-v2") -> SentenceTransformer:
    logger.info(f"Loading embedding model '{model_name}'")
    return SentenceTransformer(model_name)


def embed_watchlist(
    model: SentenceTransformer,
    entries: list[WatchlistEntry],
) -> np.ndarray:
    keywords = [e.keyword for e in entries]
    # normalize_embeddings=True so cosine similarity == dot product
    return model.encode(keywords, convert_to_numpy=True, normalize_embeddings=True)


def match_watchlist(
    model: SentenceTransformer,
    watchlist_embeddings: np.ndarray,
    entries: list[WatchlistEntry],
    listing_title: str,
    threshold: float = 0.60,
) -> WatchlistEntry | None:
    # Short-circuit: if all keyword tokens appear as substrings in the title,
    # treat as a hard match (handles "iPhone 14 Pro 256GB" vs "iPhone 14 Pro",
    # where embedding cosine drops below threshold on short queries).
    title_lower = listing_title.lower()
    for entry in entries:
        tokens = [tok for tok in entry.keyword.lower().split() if tok]
        if tokens and all(tok in title_lower for tok in tokens):
            logger.debug(
                f"Keyword subset match: '{listing_title}' -> '{entry.keyword}'"
            )
            return entry

    title_vec = model.encode(
        [listing_title], convert_to_numpy=True, normalize_embeddings=True
    )[0]
    scores = _cosine_similarity(title_vec, watchlist_embeddings)
    best_idx = int(np.argmax(scores))
    best_score = float(scores[best_idx])
    logger.debug(
        f"Best match for '{listing_title}': "
        f"'{entries[best_idx].keyword}' score={best_score:.3f}"
    )
    if best_score < threshold:
        return None
    return entries[best_idx]


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    # Both vectors already L2-normalized; cosine == dot product
    return b @ a
