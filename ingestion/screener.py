"""
ingestion/screener.py — Stage 1 quantitative screener

Design decisions:
- Pure math on data already in Postgres. No LLM, no API calls.
  Runs fast — 600 tickers screened in seconds from DB query + yf.download().
- Four filters applied in order. Each has a hard cutoff and a score component.
  Hard cutoffs eliminate tickers entirely. Scores rank survivors.
- yf.download() is the one true batch call — price history for all tickers
  in a single request. Used for momentum and liquidity scoring.
- Composite score = weighted sum of individual scores.
  Weights are explicit constants — easy to tune, easy to explain in interviews.
- Top 50 by composite score survive into Stage 2 (full LLM pipeline).
  50 is the right number: $0.10/night in LLM costs, ~25 min runtime.
- screening_scores table written for every ticker — passed AND failed.
  This gives you full audit trail of why a ticker was excluded.

Scoring weights:
    Liquidity:   30% — must be tradeable
    Momentum:    40% — trend following component
    Fundamental: 30% — quality filter
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# ── Scoring weights ───────────────────────────────────────────────────────────
LIQUIDITY_WEIGHT   = 0.30
MOMENTUM_WEIGHT    = 0.40
FUNDAMENTAL_WEIGHT = 0.30

# ── Hard cutoffs (eliminates ticker entirely) ─────────────────────────────────
MIN_AVG_VOLUME     = 500_000    # shares/day — filters illiquid micro-caps
MIN_PRICE          = 5.0        # USD — filters penny stocks
MAX_PE_RATIO       = 200.0      # filters bubble valuations
MIN_PE_RATIO       = 0.0        # filters negative earnings (loss-making)
MIN_REVENUE_GROWTH = -0.30      # -30% — filters severely declining businesses

# ── Top N survivors into Stage 2 ─────────────────────────────────────────────
TOP_N = 50


def run_screener(engine: Engine) -> dict:
    """
    Main screener entry point.
    1. Load universe tickers + fundamentals from Postgres
    2. Fetch 1-year price history via yf.download() (batch)
    3. Compute scores for each ticker
    4. Write screening_scores rows
    5. Return summary
    """
    now = datetime.now(timezone.utc)
    summary = {
        "tickers_screened": 0,
        "tickers_passed": 0,
        "top_50_selected": 0,
        "errors": [],
    }

    # ── Step 1: Load universe + latest fundamentals from Postgres ─────────────
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    u.ticker,
                    u.sector,
                    pf.pe_ratio,
                    pf.revenue_growth,
                    pf.market_cap
                FROM universe u
                LEFT JOIN LATERAL (
                    SELECT pe_ratio, revenue_growth, market_cap
                    FROM processed_fundamentals
                    WHERE ticker = u.ticker
                    ORDER BY processed_at DESC
                    LIMIT 1
                ) pf ON true
                WHERE u.is_active = true
                ORDER BY u.ticker
            """)).fetchall()

        tickers = [row.ticker for row in rows]
        fundamentals_map = {
            row.ticker: {
                "pe_ratio": row.pe_ratio,
                "revenue_growth": row.revenue_growth,
                "market_cap": row.market_cap,
                "sector": row.sector,
            }
            for row in rows
        }

        logger.info(f"screener: loaded {len(tickers)} active tickers from universe")
        summary["tickers_screened"] = len(tickers)

    except Exception as e:
        logger.error(f"screener: failed to load universe: {e}", exc_info=True)
        summary["errors"].append(f"universe load failed: {e}")
        return summary

    if not tickers:
        logger.warning("screener: no active tickers in universe — run universe refresh first")
        return summary

    # ── Step 2: Batch price history fetch ─────────────────────────────────────
    logger.info(f"screener: fetching 1y price history for {len(tickers)} tickers via yf.download()")
    try:
        raw = yf.download(
            tickers=tickers,
            period="1y",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as e:
        logger.error(f"screener: yf.download failed: {e}", exc_info=True)
        summary["errors"].append(f"yf.download failed: {e}")
        return summary

    # ── Step 3: Score each ticker ─────────────────────────────────────────────
    scored: list[dict] = []

    for ticker in tickers:
        try:
            fund = fundamentals_map.get(ticker, {})
            score_data = _score_ticker(ticker, raw, fund)
            if score_data:
                scored.append(score_data)
        except Exception as e:
            logger.warning(f"screener: error scoring {ticker}: {e}")
            summary["errors"].append(f"{ticker}: {e}")
            continue

    # ── Step 4: Rank and select top N ─────────────────────────────────────────
    passed = [s for s in scored if s["passed_screen"]]
    passed_sorted = sorted(passed, key=lambda x: x["composite_score"], reverse=True)

    top_tickers = set()
    for i, s in enumerate(passed_sorted):
        if i < TOP_N:
            s["top_50"] = True
            top_tickers.add(s["ticker"])
        else:
            s["top_50"] = False

    summary["tickers_passed"] = len(passed)
    summary["top_50_selected"] = len(top_tickers)

    logger.info(
        f"screener: {len(passed)}/{len(scored)} tickers passed screen — "
        f"top {len(top_tickers)} selected for pipeline"
    )

    # ── Step 5: Write screening_scores ────────────────────────────────────────
    _write_scores(engine, scored, now)

    return summary


def _score_ticker(
    ticker: str,
    price_data: pd.DataFrame,
    fundamentals: dict,
) -> Optional[dict]:
    """
    Compute scores for a single ticker from batch price data + fundamentals.
    Returns score dict or None if data insufficient.

    Fix: use isinstance(columns, pd.MultiIndex) instead of columns.levels
    which raises AttributeError on flat DataFrames (single ticker download).
    """
    try:
        # Extract this ticker's price series from batch download
        # Fix: proper MultiIndex check — columns.levels raises on flat DataFrame
        if isinstance(price_data.columns, pd.MultiIndex):
            # yfinance MultiIndex is now (ticker, field) — e.g. ("AAPL", "Close")
            if ticker not in price_data.columns.get_level_values(0):
                return None
            close = price_data[ticker]["Close"].dropna()
            volume = price_data[ticker]["Volume"].dropna()
        else:
            close = price_data["Close"].dropna()
            volume = price_data["Volume"].dropna()

        if len(close) < 20:
            return None

        current_price = float(close.iloc[-1])
        avg_volume = float(volume.mean())
        year_ago_price = float(close.iloc[0])

        # ── Hard cutoff checks ─────────────────────────────────────────────────
        fail_reason = None

        if current_price < MIN_PRICE:
            fail_reason = f"price ${current_price:.2f} below ${MIN_PRICE} minimum"
        elif avg_volume < MIN_AVG_VOLUME:
            fail_reason = f"avg volume {avg_volume:,.0f} below {MIN_AVG_VOLUME:,} minimum"
        else:
            pe = fundamentals.get("pe_ratio")
            rev_growth = fundamentals.get("revenue_growth")
            if pe is not None and (pe < MIN_PE_RATIO or pe > MAX_PE_RATIO):
                fail_reason = f"P/E {pe:.1f} outside acceptable range"
            if rev_growth is not None and rev_growth < MIN_REVENUE_GROWTH:
                fail_reason = f"revenue growth {rev_growth:.1%} below {MIN_REVENUE_GROWTH:.0%} minimum"

        passed = fail_reason is None

        # ── Score computation (even for failed tickers — for audit trail) ──────

        # Liquidity score (0-100)
        liquidity_score = min(100, max(0, (avg_volume - MIN_AVG_VOLUME) / (10_000_000 - MIN_AVG_VOLUME) * 100))

        # Momentum score (0-100): 52-week return normalized
        momentum_return = (current_price - year_ago_price) / year_ago_price if year_ago_price > 0 else 0
        momentum_score = min(100, max(0, 50 + momentum_return * 50))

        # Fundamental score (0-100)
        pe = fundamentals.get("pe_ratio")
        rev_growth = fundamentals.get("revenue_growth", 0) or 0
        if pe and 5 <= pe <= 50:
            pe_score = 100 - (abs(pe - 20) / 30 * 50)
        elif pe and pe > 50:
            pe_score = max(0, 50 - (pe - 50) / 150 * 50)
        else:
            pe_score = 40  # unknown P/E gets neutral score

        growth_score = min(100, max(0, 50 + rev_growth * 100))
        fundamental_score = (pe_score + growth_score) / 2

        composite_score = (
            liquidity_score * LIQUIDITY_WEIGHT +
            momentum_score * MOMENTUM_WEIGHT +
            fundamental_score * FUNDAMENTAL_WEIGHT
        )

        return {
            "ticker": ticker,
            "passed_screen": passed,
            "screen_reason": fail_reason or "passed all filters",
            "liquidity_score": round(liquidity_score, 2),
            "momentum_score": round(momentum_score, 2),
            "fundamental_score": round(fundamental_score, 2),
            "composite_score": round(composite_score, 2),
            "top_50": False,
        }

    except Exception as e:
        logger.warning(f"screener: _score_ticker failed for {ticker}: {e}")
        return None


def _write_scores(engine: Engine, scored: list[dict], screen_time: datetime) -> None:
    """
    Write all scoring results to screening_scores table.

    Fixes:
    - Single commit outside loop (was 600 commits, now 1)
    - Delete existing rows for same screen_date before insert (idempotent)
    """
    if not scored:
        return
    try:
        with engine.connect() as conn:
            # Remove any existing rows for this screen run (idempotent)
            conn.execute(text("""
                DELETE FROM screening_scores WHERE screen_date = :screen_date
            """), {"screen_date": screen_time})

            # Batch insert — single commit
            for s in scored:
                conn.execute(text("""
                    INSERT INTO screening_scores (
                        ticker, screen_date,
                        liquidity_score, momentum_score, fundamental_score,
                        composite_score, passed_screen, top_50, screen_reason
                    ) VALUES (
                        :ticker, :screen_date,
                        :liquidity_score, :momentum_score, :fundamental_score,
                        :composite_score, :passed_screen, :top_50, :screen_reason
                    )
                """), {
                    "ticker": s["ticker"],
                    "screen_date": screen_time,
                    "liquidity_score": s["liquidity_score"],
                    "momentum_score": s["momentum_score"],
                    "fundamental_score": s["fundamental_score"],
                    "composite_score": s["composite_score"],
                    "passed_screen": s["passed_screen"],
                    "top_50": s["top_50"],
                    "screen_reason": s["screen_reason"],
                })

            conn.commit()  # single commit for entire batch
        logger.info(f"screener: wrote {len(scored)} screening_scores rows")
    except Exception as e:
        logger.error(f"screener: failed to write screening_scores: {e}", exc_info=True)


def get_top_tickers(engine: Engine) -> list[str]:
    """
    Return today's top 50 tickers from most recent screen.
    Fix: replaced fragile DISTINCT ON with subquery that correctly
    finds the latest screen_date and returns top tickers from it.
    """
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT ticker
            FROM screening_scores
            WHERE top_50 = true
              AND screen_date = (
                  SELECT MAX(screen_date)
                  FROM screening_scores
                  WHERE top_50 = true
              )
            ORDER BY composite_score DESC
        """))
        tickers = [row.ticker for row in result]
    logger.info(f"screener: returning {len(tickers)} top tickers for pipeline")
    return tickers
