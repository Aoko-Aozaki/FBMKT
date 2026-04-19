from __future__ import annotations

import json
import re

from loguru import logger
from openai import OpenAI

from src.models import DealResult, Listing, WatchlistEntry

SYSTEM_PROMPT = """
You are a secondhand marketplace deal analyst.

Your job is NOT to determine whether the listing is perfect.
Your job is to determine whether the listing is worth pursuing as a potentially good deal.

Use these rules in order:

1) Hard reject (worth_buying = false) ONLY if there is clear evidence that:
- the item is not the target item,
- it clearly fails a required constraint in the watchlist notes,
- the listing has serious red flags (broken, locked, blacklisted, fake, empty box, parts only, major damage, missing essential parts),
- or the price is bad relative to the fair price and the listing quality is weak.

2) Missing information is NOT the same as failing a requirement.
- If storage, battery health, accessories, or other details are not mentioned, do NOT assume the worst.
- Only reject for a requirement mismatch when the listing explicitly says it does not meet the requirement.

3) Short or vague descriptions are common in secondhand listings.
- A vague description alone is not enough to reject.
- Instead, reduce confidence.

4) If the listing probably matches the desired item and the price is meaningfully below fair price,
lean toward worth_buying = true unless there is explicit negative evidence.

5) Seller-entered condition fields can be noisy.
- Minor inconsistency such as condition='New' but description saying 'great condition' should be treated as low-severity noise, not an automatic red flag.

6) If the description is empty or missing, default to worth_buying = true,
but keep confidence <= 0.5 unless other evidence is unusually strong.

Confidence guide:
- 0.85-1.0: strong evidence
- 0.60-0.84: likely worth buying / likely not worth buying, but some uncertainty
- 0.35-0.59: unclear / limited information
- 0.00-0.34: very weak evidence

Respond ONLY with valid JSON in this exact schema:
{"worth_buying": bool, "reason": str, "confidence": float}
"""


def evaluate_deal(
    client: OpenAI,
    listing: Listing,
    entry: WatchlistEntry,
) -> DealResult:
    prompt = _build_prompt(listing, entry)
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
    except Exception as exc:
        logger.error(f"LLM call failed: {exc}")
        return DealResult(worth_buying=False, reason=f"llm error: {exc}", confidence=0.0)
    raw = response.choices[0].message.content or ""
    logger.debug(f"LLM raw response: {raw[:300]}")
    return _parse_response(raw)


def _build_prompt(listing: Listing, entry: WatchlistEntry) -> str:
    if entry.fair_price > 0:
        discount_pct = round((1 - listing.price / entry.fair_price) * 100, 1)
    else:
        discount_pct = 0.0

    return (
        f"Item: {listing.title}\n"
        f"Asking price: ${listing.price:.0f}\n"
        f"Description: {listing.description or '(none provided)'}\n"
        f"Condition: {listing.condition or '(not specified)'}\n"
        f"The item I'm looking for: {entry.keyword or '(not specified)'}\n"
        f"My fair price: ${entry.fair_price:.0f} (notes: {entry.notes or 'none'})\n"
        f"Discount vs fair price: {discount_pct:+.1f}%\n\n"
        "Is this listing worth buying? Look for red flags in the description and condition."
    )


def _parse_response(raw: str) -> DealResult:
    _fallback = DealResult(worth_buying=False, reason="parse error", confidence=0.0)

    try:
        data = json.loads(raw)
        return _dict_to_result(data)
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if fence_match:
        try:
            data = json.loads(fence_match.group(1))
            return _dict_to_result(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    logger.warning(f"Could not parse LLM response: {raw[:200]}")
    return _fallback


def _dict_to_result(data: dict) -> DealResult:
    return DealResult(
        worth_buying=bool(data["worth_buying"]),
        reason=str(data.get("reason", "")),
        confidence=float(data.get("confidence", 0.0)),
    )
