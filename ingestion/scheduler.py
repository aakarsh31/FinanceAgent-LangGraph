"""
ingestion/scheduler.py — Nightly ingestion jobs via APScheduler

Design decisions:
- APScheduler BlockingScheduler for the worker process — one job queue,
  runs in the main thread. No async complexity needed for batch jobs.
- Three independent jobs: fundamentals, news, macro.
  Each job fails independently — a Finnhub outage never blocks FRED ingestion.
- Per-ticker error isolation inside each job — one bad ticker never aborts the batch.
  We log failures, write IngestionResult, and move on.
- UPSERT pattern on data_freshness_meta using ON CONFLICT — idempotent.
  Running the job twice produces the same result as running it once.
- Miss-based promotion: cache_miss_log is queried before each nightly run.
  High-miss tickers are added to the universe automatically.
- Nightly universe is defined here as a constant — easy to extend.
"""

import json
import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from ingestion.db import (
    cache_miss_log,
    data_freshness_meta,
    get_engine,
    init_db,
    processed_fundamentals,
    processed_news,
    processed_macro,
    raw_fundamentals,
    raw_news,
    raw_macro,
)
from ingestion.ingestion_client import FMPClient, batch_sleep
from ingestion.models import IngestionResult

logger = logging.getLogger(__name__)

# ── Nightly universe ──────────────────────────────────────────────────────────
# Top 100 S&P constituents by market cap + major ETFs.
# Covers ~80% of real-world query traffic.
# Crypto handled separately via CoinGecko (already reliable — no replacement needed).

BASE_EQUITY_UNIVERSE = [
    # Mega cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AVGO", "ORCL",
    # Financials
    "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "BLK", "SCHW", "AXP",
    # Healthcare
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "PFE", "AMGN",
    # Consumer
    "COST", "WMT", "PG", "KO", "PEP", "MCD", "NKE", "SBUX", "TGT", "HD",
    # Industrials
    "CAT", "DE", "HON", "RTX", "LMT", "GE", "UPS", "FDX", "BA", "MMM",
    # Energy
    "XOM", "CVX", "COP", "SLB", "EOG", "PXD", "MPC", "PSX", "VLO", "OXY",
    # Semiconductors
    "AMD", "INTC", "QCOM", "MU", "AMAT", "LRCX", "KLAC", "MRVL", "TXN", "ADI",
    # Tech / SaaS
    "CRM", "ADBE", "NOW", "INTU", "PANW", "SNOW", "PLTR", "NET", "DDOG", "ZS",
    # ETFs
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "GLD", "TLT", "HYG", "EEM",
    # Communications
    "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR", "PARA", "WBD", "EA",
]

MISS_PROMOTION_THRESHOLD = 3   # promote ticker if missed 3+ times in last 24h


# ── Ticker promotion ──────────────────────────────────────────────────────────

def get_promoted_tickers(engine: Engine) -> list[str]:
    """
    Query cache_miss_log for high-miss tickers not already in the base universe.
    These get added to tonight's ingestion run automatically.
    """
    try:
        with engine.connect() as conn:
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
                if row.ticker not in BASE_EQUITY_UNIVERSE
            ]

            if promoted:
                logger.info(f"Promoting {len(promoted)} high-miss tickers: {promoted}")
            return promoted

    except Exception as e:
        logger.warning(f"Could not query miss log for promotion: {e}")
        return []


def get_tonight_universe(engine: Engine) -> list[str]:
    """Full nightly universe = base + promoted tickers."""
    promoted = get_promoted_tickers(engine)
    universe = list(dict.fromkeys(BASE_EQUITY_UNIVERSE + promoted))  # deduplicated, order preserved
    logger.info(f"Tonight's universe: {len(universe)} tickers ({len(promoted)} promoted)")
    return universe


# ── Freshness meta upsert ─────────────────────────────────────────────────────

def upsert_freshness(conn, ticker: str, data_type: str, source: str, row_count: int, status: str):
    """
    UPSERT into data_freshness_meta.
    ON CONFLICT (ticker, data_type) → UPDATE.
    Idempotent — safe to call multiple times.
    """
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


# ── Job 1: Fundamentals ───────────────────────────────────────────────────────

def run_fundamentals_ingestion(engine: Engine) -> IngestionResult:
    """
    Nightly FMP fundamentals ingestion.
    For each ticker: fetch → validate → write raw → write processed → upsert freshness.
    """
    result = IngestionResult(job_name="fundamentals")
    client = FMPClient()
    universe = get_tonight_universe(engine)

    logger.info(f"[fundamentals] Starting ingestion for {len(universe)} tickers")

    for ticker in universe:
        result.tickers_attempted += 1
        try:
            fetch_result = client.fetch_fundamentals(ticker)
            if fetch_result is None:
                result.tickers_failed += 1
                result.errors.append(f"{ticker}: fetch returned None")
                with engine.connect() as conn:
                    upsert_freshness(conn, ticker, "fundamentals", "fmp", 0, "failed")
                    conn.commit()
                batch_sleep(0.25)
                continue

            raw_model, raw_json = fetch_result

            with engine.connect() as conn:
                # Write raw (immutable)
                raw_insert = conn.execute(
                    raw_fundamentals.insert().values(
                        ticker=ticker,
                        source="fmp",
                        fetched_at=datetime.now(timezone.utc),
                        raw_json=raw_json,
                    )
                )
                raw_id = raw_insert.inserted_primary_key[0]

                # Write processed
                processed = client.to_processed_fundamentals(raw_model, ticker, raw_id)
                conn.execute(
                    processed_fundamentals.insert().values(
                        ticker=processed.ticker,
                        pe_ratio=processed.pe_ratio,
                        eps=processed.eps,
                        revenue_growth=processed.revenue_growth,
                        debt_to_equity=processed.debt_to_equity,
                        market_cap=processed.market_cap,
                        sector=processed.sector,
                        processed_at=processed.processed_at,
                        source_raw_id=processed.source_raw_id,
                    )
                )

                # Upsert freshness
                upsert_freshness(conn, ticker, "fundamentals", "fmp", 1, "fresh")
                conn.commit()

            result.tickers_succeeded += 1
            result.rows_written += 1

        except Exception as e:
            logger.error(f"[fundamentals] Unexpected error for {ticker}: {e}", exc_info=True)
            result.tickers_failed += 1
            result.errors.append(f"{ticker}: {str(e)[:100]}")

        batch_sleep(0.25)

    result.completed_at = datetime.now(timezone.utc)
    logger.info(f"[fundamentals] {result.summary()}")
    return result


# ── Job 2: News ───────────────────────────────────────────────────────────────

def run_news_ingestion(engine: Engine) -> IngestionResult:
    """
    Nightly FMP news ingestion.
    Fetches latest company news for each ticker in universe via FMP stable endpoint.
    """
    result = IngestionResult(job_name="news")
    client = FMPClient()
    universe = get_tonight_universe(engine)

    logger.info(f"[news] Starting ingestion for {len(universe)} tickers")

    for ticker in universe:
        result.tickers_attempted += 1
        try:
            fetch_result = client.fetch_news(ticker)
            if fetch_result is None:
                result.tickers_failed += 1
                result.errors.append(f"{ticker}: fetch returned None")
                with engine.connect() as conn:
                    upsert_freshness(conn, ticker, "news", "finnhub", 0, "failed")
                    conn.commit()
                batch_sleep(0.25)
                continue

            articles, raw_json = fetch_result

            with engine.connect() as conn:
                # Write raw (even if no articles — log the attempt)
                raw_insert = conn.execute(
                    raw_news.insert().values(
                        ticker=ticker,
                        source="fmp_news",
                        fetched_at=datetime.now(timezone.utc),
                        raw_json=raw_json,
                    )
                )
                raw_id = raw_insert.inserted_primary_key[0]

                # Write processed articles
                processed_articles = client.to_processed_news(articles, ticker, raw_id)
                rows_written = 0
                for article in processed_articles:
                    conn.execute(
                        processed_news.insert().values(
                            ticker=article.ticker,
                            headline=article.headline,
                            publisher=article.publisher,
                            published_at=article.published_at,
                            processed_at=article.processed_at,
                            source_raw_id=article.source_raw_id,
                        )
                    )
                    rows_written += 1

                upsert_freshness(conn, ticker, "news", "fmp_news", rows_written, "fresh")
                conn.commit()

            result.tickers_succeeded += 1
            result.rows_written += rows_written

        except Exception as e:
            logger.error(f"[news] Unexpected error for {ticker}: {e}", exc_info=True)
            result.tickers_failed += 1
            result.errors.append(f"{ticker}: {str(e)[:100]}")

        batch_sleep(0.25)

    result.completed_at = datetime.now(timezone.utc)
    logger.info(f"[news] {result.summary()}")
    return result


# ── Job 3: Macro (FRED) ───────────────────────────────────────────────────────

FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "cpi_yoy": "CPIAUCSL",      # we compute YoY in DataFetchAgent — store raw CPI here
    "yield_10y": "GS10",
    "yield_2y": "GS2",
    "unemployment": "UNRATE",
}


def run_macro_ingestion(engine: Engine) -> IngestionResult:
    """
    Nightly FRED macro ingestion.
    Fixed indicator set — no ticker universe needed.
    """
    result = IngestionResult(job_name="macro")

    try:
        from fredapi import Fred
        api_key = os.getenv("FRED_API_KEY")
        if not api_key:
            logger.error("[macro] FRED_API_KEY not set — skipping macro ingestion")
            result.errors.append("FRED_API_KEY not set")
            return result

        fred = Fred(api_key=api_key)

        for indicator_name, series_id in FRED_SERIES.items():
            result.tickers_attempted += 1
            try:
                series = fred.get_series(series_id).dropna()
                if series.empty:
                    logger.warning(f"[macro] FRED series '{series_id}' returned empty")
                    result.tickers_failed += 1
                    continue

                latest_value = float(series.iloc[-1])
                latest_period = str(series.index[-1].date())

                raw_payload = {
                    "series_id": series_id,
                    "latest_value": latest_value,
                    "latest_period": latest_period,
                }
                raw_json = json.dumps(raw_payload)

                with engine.connect() as conn:
                    raw_insert = conn.execute(
                        raw_macro.insert().values(
                            indicator=indicator_name,
                            source="fred",
                            fetched_at=datetime.now(timezone.utc),
                            raw_json=raw_json,
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

                    # Macro uses indicator name as "ticker" in freshness meta
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
        logger.error(f"[macro] Fatal error in macro ingestion: {e}", exc_info=True)
        result.errors.append(f"Fatal: {str(e)[:100]}")

    result.completed_at = datetime.now(timezone.utc)
    logger.info(f"[macro] {result.summary()}")
    return result


# ── Scheduler setup ───────────────────────────────────────────────────────────

def build_scheduler(engine: Engine) -> BlockingScheduler:
    """
    Build and configure the APScheduler instance.
    Three jobs, staggered start times to avoid simultaneous DB pressure.

    Schedule (UTC):
      02:00 — fundamentals (heaviest — 200 FMP calls)
      02:30 — news (moderate — 100 Finnhub calls)
      03:00 — macro (lightest — 5 FRED calls)
    """
    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        func=lambda: run_fundamentals_ingestion(engine),
        trigger="cron",
        hour=2,
        minute=0,
        id="fundamentals_ingestion",
        name="Nightly FMP Fundamentals Ingestion",
        misfire_grace_time=300,     # if job misfires, run within 5 min window
        coalesce=True,              # if multiple misfires, run only once
    )

    scheduler.add_job(
        func=lambda: run_news_ingestion(engine),
        trigger="cron",
        hour=2,
        minute=30,
        id="news_ingestion",
        name="Nightly Finnhub News Ingestion",
        misfire_grace_time=300,
        coalesce=True,
    )

    scheduler.add_job(
        func=lambda: run_macro_ingestion(engine),
        trigger="cron",
        hour=3,
        minute=0,
        id="macro_ingestion",
        name="Nightly FRED Macro Ingestion",
        misfire_grace_time=300,
        coalesce=True,
    )

    return scheduler