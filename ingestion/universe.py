"""
ingestion/universe.py — Live universe management

Design decisions:
- FMP constituent APIs are used ONLY for index membership — 2 calls total per night.
  This is the one thing FMP free tier handles perfectly.
  /stable/sp500-constituent and /stable/nasdaq-constituent return symbol + name + sector.
- Russell 2000 slot is stubbed — add when ready. Architecture supports it with
  zero changes to the caller (scheduler just calls refresh_universe() and it expands).
- universe table stores ticker + company_name + sector + index_membership.
  company_name is used by the RSS news mapper to match articles to tickers.
  This is the name dictionary we discussed — built for free from the constituent API.
- is_active flag allows soft-delete when a ticker leaves an index.
  We never hard-delete — historical signals need the ticker record.
- UPSERT pattern — safe to run multiple times, idempotent.
"""

import logging
import os
from datetime import datetime, timezone

import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

FMP_BASE = "https://financialmodelingprep.com/stable"


# ── Universe table is defined here and added to ingestion/db.py metadata ──────
# We add it to db.py's metadata so init_db() creates it automatically.
# See db.py — universe and screening_scores tables added there.


def _fetch_fmp_constituents(endpoint: str, api_key: str) -> list[dict]:
    """
    Fetch constituent list from FMP.
    Returns list of {symbol, name, sector} dicts or empty list on failure.
    """
    try:
        url = f"{FMP_BASE}/{endpoint}"
        resp = requests.get(
            url,
            params={"apikey": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            logger.warning(f"universe: unexpected response format from {endpoint}")
            return []
        logger.info(f"universe: fetched {len(data)} constituents from {endpoint}")
        return data
    except requests.exceptions.HTTPError as e:
        logger.error(f"universe: HTTP error from {endpoint}: {e}")
        return []
    except Exception as e:
        logger.error(f"universe: failed to fetch {endpoint}: {e}", exc_info=True)
        return []


def refresh_universe(engine: Engine) -> dict:
    """
    Fetch live S&P 500 + NASDAQ 100 constituents and upsert into universe table.
    Russell 2000 slot is stubbed — uncomment when ready.

    Returns summary dict for logging.
    """
    api_key = os.getenv("FMP_API_KEY")
    if not api_key:
        logger.error("universe: FMP_API_KEY not set — cannot refresh universe")
        return {"error": "FMP_API_KEY not set", "tickers_added": 0}

    now = datetime.now(timezone.utc)
    summary = {"tickers_added": 0, "tickers_updated": 0, "sources": []}

    # Fetch S&P 500
    sp500 = _fetch_fmp_constituents("sp500-constituent", api_key)
    sp500_symbols = {item["symbol"] for item in sp500 if item.get("symbol")}

    # Fetch NASDAQ 100
    nasdaq = _fetch_fmp_constituents("nasdaq-constituent", api_key)
    nasdaq_symbols = {item["symbol"] for item in nasdaq if item.get("symbol")}

    # ── Russell 2000 stub ─────────────────────────────────────────────────────
    # Uncomment when adding Russell 2000 support:
    # russell = _fetch_russell_2000()  # implement via iShares IWM holdings API
    # russell_symbols = {item["symbol"] for item in russell}
    russell_symbols: set[str] = set()
    # ─────────────────────────────────────────────────────────────────────────

    # Build unified ticker map with index membership
    # {symbol: {name, sector, indices}}
    ticker_map: dict[str, dict] = {}

    for item in sp500:
        sym = item.get("symbol")
        if not sym:
            continue
        ticker_map[sym] = {
            "company_name": item.get("name") or item.get("companyName", ""),
            "sector": item.get("sector", ""),
            "in_sp500": True,
            "in_nasdaq100": sym in nasdaq_symbols,
            "in_russell2000": False,
        }

    for item in nasdaq:
        sym = item.get("symbol")
        if not sym:
            continue
        if sym not in ticker_map:
            ticker_map[sym] = {
                "company_name": item.get("name") or item.get("companyName", ""),
                "sector": item.get("sector", ""),
                "in_sp500": False,
                "in_nasdaq100": True,
                "in_russell2000": False,
            }
        else:
            ticker_map[sym]["in_nasdaq100"] = True

    if not ticker_map:
        logger.error("universe: no tickers fetched — aborting universe refresh")
        return {"error": "no tickers fetched", "tickers_added": 0}

    logger.info(f"universe: upserting {len(ticker_map)} unique tickers")

    # Mark all existing tickers as inactive first — reactivate ones still in index
    with engine.connect() as conn:
        conn.execute(text("UPDATE universe SET is_active = false WHERE true"))
        conn.commit()

    # Upsert each ticker
    with engine.connect() as conn:
        for symbol, data in ticker_map.items():
            conn.execute(text("""
                INSERT INTO universe (
                    ticker, company_name, sector,
                    in_sp500, in_nasdaq100, in_russell2000,
                    is_active, last_updated
                ) VALUES (
                    :ticker, :company_name, :sector,
                    :in_sp500, :in_nasdaq100, :in_russell2000,
                    true, :last_updated
                )
                ON CONFLICT (ticker) DO UPDATE SET
                    company_name   = EXCLUDED.company_name,
                    sector         = EXCLUDED.sector,
                    in_sp500       = EXCLUDED.in_sp500,
                    in_nasdaq100   = EXCLUDED.in_nasdaq100,
                    in_russell2000 = EXCLUDED.in_russell2000,
                    is_active      = true,
                    last_updated   = EXCLUDED.last_updated
            """), {
                "ticker": symbol,
                "company_name": data["company_name"],
                "sector": data["sector"],
                "in_sp500": data["in_sp500"],
                "in_nasdaq100": data["in_nasdaq100"],
                "in_russell2000": data["in_russell2000"],
                "last_updated": now,
            })
            summary["tickers_added"] += 1

        conn.commit()

    summary["sources"] = ["sp500", "nasdaq100"]
    if sp500:
        summary["sp500_count"] = len(sp500_symbols)
    if nasdaq:
        summary["nasdaq100_count"] = len(nasdaq_symbols)
    summary["total_unique"] = len(ticker_map)

    logger.info(
        f"universe: refresh complete — "
        f"sp500={len(sp500_symbols)} nasdaq={len(nasdaq_symbols)} "
        f"unique={len(ticker_map)}"
    )
    return summary


def get_active_tickers(engine: Engine) -> list[str]:
    """Return all active tickers in the universe."""
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT ticker FROM universe
            WHERE is_active = true
            ORDER BY ticker
        """))
        return [row.ticker for row in result]


def get_ticker_name_map(engine: Engine) -> dict[str, str]:
    """
    Return {ticker: company_name} for all active tickers.
    Used by RSS news mapper to match articles to tickers.
    """
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT ticker, company_name FROM universe
            WHERE is_active = true AND company_name != ''
        """))
        return {row.ticker: row.company_name for row in result}