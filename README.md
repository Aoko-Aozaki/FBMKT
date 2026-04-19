# FB Marketplace Deal Alert

A local monitoring tool for Facebook Marketplace. It searches Marketplace using the keywords in `watchlist.yaml`, filters out listings that have already been seen without a price drop, uses semantic matching to confirm relevance, asks an LLM whether the listing is worth pursuing, and sends Telegram alerts for promising deals.

The current implementation is a long-running local service rather than a one-off scraper. After startup, it runs one check immediately and then continues running on a fixed schedule.

[中文版readme](./README_CN.md)
## What The Current Code Does

- Searches Facebook Marketplace for each keyword in the watchlist
- Uses `sentence-transformers` for title-level semantic matching to reduce Marketplace search noise
- Applies `max_price` as a hard filter so overpriced listings never reach the LLM stage
- Opens the listing detail page to fetch description and condition information
- Uses the OpenAI Python SDK against a DeepSeek-compatible endpoint to decide whether a secondhand listing is worth pursuing
- Only processes listings that are new or have dropped in price, which prevents duplicate alerts
- Persists runtime state in `seen_listings.json` and prunes stale entries based on `STALE_DAYS`
- Sends Telegram notifications and also sends an alert if the Facebook login session expires
- Adds a random delay before each keyword scrape to reduce the risk of aggressive request patterns

## Project Structure

```text
.
├── main.py                    # Program entry point, starts the scheduler
├── login.py                   # Manual Facebook login, saves auth_state.json
├── watchlist.yaml             # Watch targets and pricing rules
├── src/
│   ├── config.py              # Loads .env and watchlist settings
│   ├── models.py              # Shared data models
│   ├── scraper/scraper.py     # Playwright + BeautifulSoup scraping
│   ├── matcher/matcher.py     # sentence-transformers matching
│   ├── llm/evaluator.py       # LLM evaluation
│   ├── notifier/notifier.py   # Telegram notifications
│   ├── state/state.py         # seen_listings.json read/write
│   └── pipeline/scheduler.py  # Scheduler and main pipeline
├── tests/                     # Unit tests and a few integration tests
└── docs/                      # Design notes and task tracking
```

## Workflow

```text
Start scheduler
  ↓
Run run_pipeline() immediately
  ↓
Search Marketplace for each watchlist keyword
  ↓
Keep only new listings or listings with a lower price
  ↓
Match titles against the watchlist semantically
  ↓
Skip any listing above max_price
  ↓
Fetch detail page description and condition
  ↓
LLM evaluates worth_buying / confidence
  ↓
Send Telegram alert if thresholds are met
  ↓
Save and prune seen_listings.json
```

## Prerequisites

- Python 3.11+
- A Facebook account that can access Facebook Marketplace
- A Telegram bot token and chat ID
- A DeepSeek API key
- Playwright Chromium installed on the local machine

Notes:

- The environment variable is still named `OPENAI_API_KEY`, but the current implementation actually uses the OpenAI SDK in compatible mode with `base_url` set to `https://api.deepseek.com/v1` and model name `deepseek-chat`
- The `sentence-transformers` model may need to be downloaded the first time it is loaded, so the first run can be slower than later runs

## Installation

```bash
pip install -e ".[dev]"
playwright install chromium
```

## Configuration

### 1. Create `.env`

You can use `.env.example` as a reference, but the example below reflects the actual configuration keys and defaults used by the current code:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
OPENAI_API_KEY=your_deepseek_api_key
FB_LOCATION=San Francisco, CA

POLL_INTERVAL_MIN=15
SIMILARITY_THRESHOLD=0.60
CONFIDENCE_THRESHOLD=0.70
SCRAPE_DELAY_MIN=10
SCRAPE_DELAY_MAX=30
STALE_DAYS=30
LOG_LEVEL=INFO

AUTH_STATE_PATH=auth_state.json
SEEN_LISTINGS_PATH=seen_listings.json
WATCHLIST_PATH=watchlist.yaml

HEADLESS=1
```

Meaning of each setting:

- `TELEGRAM_BOT_TOKEN`: Telegram bot token, required
- `TELEGRAM_CHAT_ID`: chat ID that receives alerts, required
- `OPENAI_API_KEY`: currently used as the API key for the DeepSeek-compatible endpoint, required
- `FB_LOCATION`: Facebook Marketplace location, required, for example `San Francisco, CA`
- `POLL_INTERVAL_MIN`: scheduler interval in minutes, default `15`. The code logs a warning if this is set below 15
- `SIMILARITY_THRESHOLD`: minimum title-to-watchlist match threshold, default `0.60`
- `CONFIDENCE_THRESHOLD`: minimum confidence required when the LLM says a listing is worth pursuing, default `0.70`
- `SCRAPE_DELAY_MIN` / `SCRAPE_DELAY_MAX`: random delay range in seconds between keyword scrapes, default `10` to `30`
- `STALE_DAYS`: how many days to keep old entries in the state file, default `30`
- `LOG_LEVEL`: log level, default `INFO`
- `AUTH_STATE_PATH`: path to the saved Facebook login state file, default `auth_state.json`
- `SEEN_LISTINGS_PATH`: path to the runtime state file, default `seen_listings.json`
- `WATCHLIST_PATH`: path to the watchlist file, default `watchlist.yaml`
- `HEADLESS`: whether scraping runs in headless mode, `1` for headless and `0` to show the browser, default `1`

### 2. Edit `watchlist.yaml`

The current code supports these fields:

- `keyword`: search keyword, required
- `fair_price`: your estimated fair price, required
- `max_price`: hard upper bound; listings above this are skipped without calling the LLM. If omitted, it behaves like infinity
- `notes`: extra notes passed directly into the LLM prompt

Example:

```yaml
watchlist:
  - keyword: "iPhone 14 Pro"
    fair_price: 400
    max_price: 500
    notes: "256GB or above, good condition"

  - keyword: "Herman Miller Aeron"
    fair_price: 250
    max_price: 400
    notes: "any size"
```

## First-Time Facebook Login

Run:

```bash
python login.py
```

The script opens a visible browser window and loads Facebook Marketplace. You need to:

1. Log in to Facebook manually in the browser
2. Return to the terminal and press Enter once
3. Let the script save the login state to `auth_state.json`

This file contains login cookies and should not be committed to version control.

## Start The Program

```bash
python main.py
```

Actual behavior:

- The first pipeline run starts immediately after launch
- Future runs execute every `POLL_INTERVAL_MIN` minutes
- The embedding model, settings, and API client are reused in-process instead of being reinitialized on every run

## When An Alert Is Sent

A Telegram alert is only sent when all of the following are true:

- The listing is new, or its price is lower than the historical price
- The title matches one of the watchlist targets
- `price <= max_price`
- The LLM returns `worth_buying = true`
- `confidence >= CONFIDENCE_THRESHOLD`

## Runtime Files

- `auth_state.json`: Facebook login session, created by `python login.py`
- `seen_listings.json`: deduplication and price-history state, created automatically on first run
- `/tmp/fb_search_debug.html`: written when the search page loads HTML but no listings are parsed successfully
- `/tmp/fb_detail_debug.html`: written on the first detail-page fetch to help debug Facebook DOM changes


## FAQ

### 1. The program fails immediately with `auth_state.json not found`

You probably have not run `python login.py` yet, or `AUTH_STATE_PATH` points to the wrong location.

### 2. Telegram alerts are not arriving

Check these items first:

- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are correct
- The listing actually satisfies `worth_buying` and `CONFIDENCE_THRESHOLD`
- The listing is not already present in `seen_listings.json` with the same or a lower historical price

### 3. I want to see the browser while scraping

Set `HEADLESS=0` in `.env`, then run `python main.py` again.

### 4. What should I do when the Facebook session expires?

The program sends a Telegram text alert when it detects a redirect to the login page. After that, run:

```bash
python login.py
```

## Notes

- `POLL_INTERVAL_MIN` can be set lower, but the code explicitly warns that values below 15 minutes increase the risk of Facebook rate limiting or account restrictions
- Facebook Marketplace DOM structure changes often. If title, description, or detail extraction suddenly breaks, check the debug HTML files under `/tmp` first
- The project name and some variable names still use `openai` terminology, but the actual request path currently targets a DeepSeek-compatible endpoint
