"""
ingestion/scheduler.py — Nightly ingestion jobs via APScheduler

Nightly schedule (UTC):
  23:00 — universe refresh (FMP constituent APIs)
  00:00 — fundamentals ingestion (yfinance, full universe)
  00:30 — news ingestion (RSS feeds, full universe)
  01:00 — screener (Stage 1 filter, outputs top 50)
  02:00 — macro (FRED, 5 indicators)
  04:00 — eval maturation (signal quality job)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

import yfinance as yf
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ingestion.db import (
    processed_fundamentals,
    processed_news,
    processed_macro,
    raw_fundamentals,
    raw_news,
    raw_macro,
)
from ingestion.models import IngestionResult
from ingestion.rss_client import RSSClient
from ingestion.screener import run_screener
from ingestion.universe import refresh_universe, get_active_tickers, get_ticker_name_map

logger = logging.getLogger(__name__)

MISS_PROMOTION_THRESHOLD = 3


# ── Freshness meta upsert ─────────────────────────────────────────────────────

def upsert_freshness(conn, ticker: str, data_type: str, source: str, row_count: int, status: str):
    conn.execute(text("""
        INSERT INTO data_freshness_meta
            (ticker, data_type, last_updated, status, source, row_count)
        VALUES
            (:ticker, :data_type, :last_updated, :status, :source, :row_count)
        ON CONFLICT (ticker, data_type)
        DO UPDATE SET
            last_updated = EXCLUDED.last_updated,
            status       = EXCLUDED.status,
            source       = EXCLUDED.source,
            row_count    = EXCLUDED.row_count
    """), {
        "ticker": ticker,
        "data_type": data_type,
        "last_updated": datetime.now(timezone.utc),
        "status": status,
        "source": source,
        "row_count": row_count,
    })


# ── Ticker promotion ──────────────────────────────────────────────────────────

def get_promoted_tickers(engine: Engine) -> list[str]:
    """Query cache_miss_log for high-miss tickers not in the universe."""
    try:
        with engine.connect() as conn:
            # Get active universe tickers
            active = {row.ticker for row in conn.execute(text(
                "SELECT ticker FROM universe WHERE is_active = true"
            ))}

            result = conn.execute(text("""
                SELECT ticker, COUNT(*) as miss_count
                FROM cache_miss_log
                WHERE data_type = 'fundamentals'
                  AND requested_at >= NOW() - INTERVAL '24 hours'
                  AND resolved_via = 'live_api'
                GROUP BY ticker
                HAVING COUNT(*) >= :threshold
            """), {"threshold": MISS_PROMOTION_THRESHOLD})

            promoted = [
                row.ticker for row in result
                if row.ticker not in active
            ]

            if promoted:
                logger.info(f"Promoting {len(promoted)} high-miss tickers: {promoted}")
            return promoted

    except Exception as e:
        logger.warning(f"Could not query miss log for promotion: {e}")
        return []


def get_tonight_universe(engine: Engine) -> list[str]:
    """Full nightly universe = active universe + promoted tickers."""
    active = get_active_tickers(engine)
    promoted = get_promoted_tickers(engine)
    universe = list(dict.fromkeys(active + promoted))
    logger.info(f"Tonight's universe: {len(universe)} tickers ({len(promoted)} promoted)")
    return universe


# ── Job 0: Universe refresh ───────────────────────────────────────────────────

def run_universe_refresh(engine: Engine) -> dict:
    """Fetch live S&P 500 + NASDAQ 100 constituents from FMP."""
    logger.info("[universe] Starting universe refresh")
    result = refresh_universe(engine)
    logger.info(f"[universe] {result}")
    return result


# ── Job 1: Fundamentals (yfinance) ───────────────────────────────────────────

def run_fundamentals_ingestion(engine: Engine) -> IngestionResult:
    """
    Nightly fundamentals ingestion using yfinance.
    No API key, no rate limits, no quota.
    """
    result = IngestionResult(job_name="fundamentals")
    universe = get_tonight_universe(engine)

    logger.info(f"[fundamentals] Starting yfinance ingestion for {len(universe)} tickers")

    for ticker in universe:
        result.tickers_attempted += 1
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            if not info or not info.get("symbol"):
                logger.warning(f"[fundamentals] yfinance: no info for {ticker}")
                result.tickers_failed += 1
                with engine.connect() as conn:
                    upsert_freshness(conn, ticker, "fundamentals", "yfinance", 0, "failed")
                    conn.commit()
                continue

            # Extract fundamentals fields
            pe_ratio = info.get("trailingPE") or info.get("forwardPE")
            eps = info.get("trailingEps")
            revenue_growth = info.get("revenueGrowth")
            debt_to_equity = info.get("debtToEquity")
            market_cap = info.get("marketCap")
            sector = info.get("sector")

            raw_json = json.dumps({
                "pe": pe_ratio,
                "eps": eps,
                "revenueGrowth": revenue_growth,
                "debtToEquity": debt_to_equity,
                "mktCap": market_cap,
                "sector": sector,
                "source": "yfinance",
            })

            with engine.connect() as conn:
                # Write raw
                raw_insert = conn.execute(
                    raw_fundamentals.insert().values(
                        ticker=ticker,
                        source="yfinance",
                        fetched_at=datetime.now(timezone.utc),
                        raw_json=raw_json,
                    )
                )
                raw_id = raw_insert.inserted_primary_key[0]

                # Write processed
                conn.execute(
                    processed_fundamentals.insert().values(
                        ticker=ticker,
                        pe_ratio=pe_ratio,
                        eps=eps,
                        revenue_growth=revenue_growth,
                        debt_to_equity=debt_to_equity,
                        market_cap=market_cap,
                        sector=sector,
                        processed_at=datetime.now(timezone.utc),
                        source_raw_id=raw_id,
                    )
                )

                upsert_freshness(conn, ticker, "fundamentals", "yfinance", 1, "fresh")
                conn.commit()

            result.tickers_succeeded += 1
            result.rows_written += 1
            logger.debug(f"[fundamentals] {ticker}: pe={pe_ratio} eps={eps}")

        except Exception as e:
            logger.error(f"[fundamentals] Error for {ticker}: {e}", exc_info=True)
            result.tickers_failed += 1
            result.errors.append(f"{ticker}: {str(e)[:100]}")

        time.sleep(0.1)  # gentle rate limiting — yfinance has no hard limit but be polite

    result.completed_at = datetime.now(timezone.utc)
    logger.info(f"[fundamentals] {result.summary()}")
    return result


# ── Job 2: News (RSS) ─────────────────────────────────────────────────────────

def run_news_ingestion(engine: Engine) -> IngestionResult:
    """
    Nightly news ingestion using RSS feeds.
    One fetch per feed — maps to all tickers simultaneously.
    No API key, no rate limits, no quota.
    """
    result = IngestionResult(job_name="news")

    # Fetch all feeds once
    client = RSSClient()
    total_articles = client.fetch_all_feeds()

    if total_articles == 0:
        logger.warning("[news] No articles fetched from RSS feeds")
        result.errors.append("No articles fetched")
        result.completed_at = datetime.now(timezone.utc)
        return result

    # Get ticker name map for matching
    ticker_name_map = get_ticker_name_map(engine)
    if not ticker_name_map:
        logger.warning("[news] No ticker name map available — run universe refresh first")
        result.errors.append("No ticker name map")
        result.completed_at = datetime.now(timezone.utc)
        return result

    # Map articles to tickers
    ticker_articles = client.map_to_tickers(ticker_name_map)
    result.tickers_attempted = len(ticker_articles)

    now = datetime.now(timezone.utc)

    for ticker, articles in ticker_articles.items():
        try:
            raw_json = json.dumps([{
                "headline": a["headline"],
                "publisher": a["publisher"],
                "published_at": a["published_at"].isoformat() if a["published_at"] else None,
                "url": a["url"],
            } for a in articles])

            with engine.connect() as conn:
                # Write raw
                raw_insert = conn.execute(
                    raw_news.insert().values(
                        ticker=ticker,
                        source="rss",
                        fetched_at=now,
                        raw_json=raw_json,
                    )
                )
                raw_id = raw_insert.inserted_primary_key[0]

                # Write processed articles
                rows_written = 0
                for article in articles:
                    conn.execute(
                        processed_news.insert().values(
                            ticker=ticker,
                            headline=article["headline"],
                            publisher=article["publisher"],
                            published_at=article["published_at"],
                            processed_at=now,
                            source_raw_id=raw_id,
                        )
                    )
                    rows_written += 1

                upsert_freshness(conn, ticker, "news", "rss", rows_written, "fresh")
                conn.commit()

            result.tickers_succeeded += 1
            result.rows_written += rows_written

        except Exception as e:
            logger.error(f"[news] Error for {ticker}: {e}", exc_info=True)
            result.tickers_failed += 1
            result.errors.append(f"{ticker}: {str(e)[:100]}")

    result.completed_at = datetime.now(timezone.utc)
    logger.info(f"[news] {result.summary()}")
    return result


# ── Job 3: Screener ───────────────────────────────────────────────────────────

def run_screener_job(engine: Engine) -> dict:
    """Stage 1 quantitative screener — filters universe to top 50."""
    logger.info("[screener] Starting Stage 1 quantitative screen")
    result = run_screener(engine)
    logger.info(f"[screener] {result}")
    return result


# ── Job 4: Macro (FRED) ───────────────────────────────────────────────────────

FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "cpi_yoy": "CPIAUCSL",
    "yield_10y": "GS10",
    "yield_2y": "GS2",
    "unemployment": "UNRATE",
}


def run_macro_ingestion(engine: Engine) -> IngestionResult:
    """Nightly FRED macro ingestion."""
    result = IngestionResult(job_name="macro")

    try:
        from fredapi import Fred
        api_key = os.getenv("FRED_API_KEY")
        if not api_key:
            logger.error("[macro] FRED_API_KEY not set")
            return result

        fred = Fred(api_key=api_key)

        for indicator_name, series_id in FRED_SERIES.items():
            result.tickers_attempted += 1
            try:
                series = fred.get_series(series_id).dropna()
                if series.empty:
                    result.tickers_failed += 1
                    continue

                latest_raw = float(series.iloc[-1])
                latest_period = str(series.index[-1].date())

                # CPI is stored as the index level (e.g. 314.2), not YoY %.
                # Compute YoY here so what lands in processed_macro is the
                # same ~3.x% figure that agents expect — not 314%.
                if indicator_name == "cpi_yoy":
                    if len(series) >= 13:
                        cpi_year_ago = float(series.iloc[-13])
                        latest_value = round((latest_raw - cpi_year_ago) / cpi_year_ago * 100, 2)
                    else:
                        logger.warning("[macro] CPI series too short for YoY — skipping")
                        result.tickers_failed += 1
                        continue
                else:
                    latest_value = latest_raw

                raw_payload = {
                    "series_id": series_id,
                    "latest_raw": latest_raw,       # raw FRED level — kept for auditability
                    "latest_value": latest_value,   # processed value stored in processed_macro
                    "latest_period": latest_period,
                }

                with engine.connect() as conn:
                    raw_insert = conn.execute(
                        raw_macro.insert().values(
                            indicator=indicator_name,
                            source="fred",
                            fetched_at=datetime.now(timezone.utc),
                            raw_json=json.dumps(raw_payload),
                        )
                    )
                    raw_id = raw_insert.inserted_primary_key[0]

                    conn.execute(
                        processed_macro.insert().values(
                            indicator=indicator_name,
                            value=latest_value,
                            period=latest_period,
                            processed_at=datetime.now(timezone.utc),
                            source_raw_id=raw_id,
                        )
                    )

                    upsert_freshness(conn, indicator_name, "macro", "fred", 1, "fresh")
                    conn.commit()

                result.tickers_succeeded += 1
                result.rows_written += 1
                logger.info(f"[macro] {indicator_name}={latest_value} ({latest_period})")

            except Exception as e:
                logger.error(f"[macro] Error fetching {indicator_name}: {e}", exc_info=True)
                result.tickers_failed += 1
                result.errors.append(f"{indicator_name}: {str(e)[:100]}")

    except Exception as e:
        logger.error(f"[macro] Fatal error: {e}", exc_info=True)
        result.errors.append(f"Fatal: {str(e)[:100]}")

    result.completed_at = datetime.now(timezone.utc)
    logger.info(f"[macro] {result.summary()}")
    return result


# ── Scheduler setup ───────────────────────────────────────────────────────────

def build_scheduler(engine: Engine) -> BlockingScheduler:
    """
    Nightly schedule (UTC):
      23:00 — universe refresh
      00:00 — fundamentals (yfinance)
      00:30 — news (RSS)
      01:00 — screener (Stage 1)
      02:00 — macro (FRED)
      04:00 — eval maturation
    """
    from evaluation.eval_job import run_eval_maturation

    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        func=lambda: run_universe_refresh(engine),
        trigger="cron", hour=23, minute=0,
        id="universe_refresh",
        name="Nightly Universe Refresh",
        misfire_grace_time=300, coalesce=True,
    )

    scheduler.add_job(
        func=lambda: run_fundamentals_ingestion(engine),
        trigger="cron", hour=0, minute=0,
        id="fundamentals_ingestion",
        name="Nightly yfinance Fundamentals Ingestion",
        misfire_grace_time=300, coalesce=True,
    )

    scheduler.add_job(
        func=lambda: run_news_ingestion(engine),
        trigger="cron", hour=0, minute=30,
        id="news_ingestion",
        name="Nightly RSS News Ingestion",
        misfire_grace_time=300, coalesce=True,
    )

    scheduler.add_job(
        func=lambda: run_screener_job(engine),
        trigger="cron", hour=1, minute=0,
        id="screener",
        name="Nightly Stage 1 Screener",
        misfire_grace_time=300, coalesce=True,
    )

    scheduler.add_job(
        func=lambda: run_macro_ingestion(engine),
        trigger="cron", hour=2, minute=0,
        id="macro_ingestion",
        name="Nightly FRED Macro Ingestion",
        misfire_grace_time=300, coalesce=True,
    )

    scheduler.add_job(
        func=lambda: run_eval_maturation(engine),
        trigger="cron", hour=4, minute=0,
        id="eval_maturation",
        name="Nightly Signal Eval Maturation",
        misfire_grace_time=300, coalesce=True,
    )

    return scheduler
