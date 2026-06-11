"""
evaluation/eval_job.py — 30-day signal maturation and hit computation

Design decisions:
- Runs nightly as an APScheduler job in worker.py.
- Finds all pipeline_signals where eval_status="pending" and signal_date
  is >= 30 days ago. These are "matured" and ready to evaluate.
- For each matured signal: fetch actual 30-day return from yfinance,
  fetch SPY return over same window, determine hit, update row.
- Hit definition:
    Buy + return_30d > 0 → hit
    Sell + return_30d < 0 → hit
    Hold → excluded from hit rate (neutral signal, not directional)
- After updating individual signals, runs aggregation to produce eval_run row.
- Per-signal failure is non-fatal — one bad yfinance call never aborts the batch.
- eval_status transitions:
    pending → matured (age check)
    matured → evaluated (return computed)
    matured → failed (yfinance returned no data)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import yfinance as yf
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

MATURATION_DAYS = 30        # days before a signal is ready to evaluate
SPY_TICKER = "SPY"          # benchmark for alpha computation


def _fetch_return(ticker: str, start_date: datetime, end_date: datetime) -> Optional[float]:
    """
    Fetch the actual return for a ticker between two dates.
    Returns decimal return e.g. 0.12 = 12%, or None if data unavailable.
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
        )
        if hist.empty or len(hist) < 2:
            logger.warning(f"eval_job: insufficient price history for {ticker}")
            return None

        price_start = float(hist["Close"].iloc[0])
        price_end = float(hist["Close"].iloc[-1])

        if price_start == 0:
            return None

        return round((price_end - price_start) / price_start, 6)

    except Exception as e:
        logger.warning(f"eval_job: failed to fetch return for {ticker}: {e}")
        return None


def _determine_hit(recommendation: str, return_30d: float) -> Optional[bool]:
    """
    Determine if the recommendation was correct.
    Hold signals return None — excluded from hit rate.
    """
    if recommendation == "Buy":
        return return_30d > 0
    elif recommendation == "Sell":
        return return_30d < 0
    else:  # Hold
        return None


def run_eval_maturation(engine: Engine) -> dict:
    """
    Main eval job:
    1. Find matured pending signals (>= 30 days old)
    2. Compute actual returns
    3. Determine hits
    4. Update signal rows
    5. Aggregate into eval_run row

    Returns summary dict for logging.
    """
    now = datetime.now(timezone.utc)
    maturation_cutoff = now - timedelta(days=MATURATION_DAYS)

    summary = {
        "signals_found": 0,
        "signals_evaluated": 0,
        "signals_failed": 0,
        "hit_rate_buy": None,
        "hit_rate_sell": None,
    }

    try:
        with engine.connect() as conn:
            # Find all pending signals that have matured
            matured = conn.execute(text("""
                SELECT id, ticker, recommendation, confidence,
                       model_version, signal_date, price_at_signal
                FROM pipeline_signals
                WHERE eval_status = 'pending'
                  AND signal_date <= :cutoff
                ORDER BY signal_date ASC
            """), {"cutoff": maturation_cutoff}).fetchall()

            summary["signals_found"] = len(matured)
            logger.info(f"eval_job: found {len(matured)} matured signals to evaluate")

            if not matured:
                logger.info("eval_job: no matured signals — nothing to do")
                return summary

            # Mark them as matured first
            matured_ids = [row.id for row in matured]
            conn.execute(text("""
                UPDATE pipeline_signals
                SET eval_status = 'matured'
                WHERE id = ANY(:ids)
            """), {"ids": matured_ids})
            conn.commit()

        # Evaluate each signal
        # Fetch SPY return once per unique date window to avoid redundant calls
        spy_return_cache: dict[str, Optional[float]] = {}

        for row in matured:
            signal_date = row.signal_date
            if signal_date.tzinfo is None:
                signal_date = signal_date.replace(tzinfo=timezone.utc)

            eval_end = signal_date + timedelta(days=MATURATION_DAYS)
            cache_key = signal_date.strftime("%Y-%m-%d")

            # Fetch SPY return for this window (cached)
            if cache_key not in spy_return_cache:
                spy_return_cache[cache_key] = _fetch_return(SPY_TICKER, signal_date, eval_end)

            spy_return = spy_return_cache[cache_key]

            # Fetch ticker return
            ticker_return = _fetch_return(row.ticker, signal_date, eval_end)

            with engine.connect() as conn:
                if ticker_return is None:
                    conn.execute(text("""
                        UPDATE pipeline_signals
                        SET eval_status = 'failed',
                            eval_date = :eval_date
                        WHERE id = :id
                    """), {"eval_date": now, "id": row.id})
                    conn.commit()
                    summary["signals_failed"] += 1
                    logger.warning(f"eval_job: failed to evaluate {row.ticker} (id={row.id})")
                    continue

                hit = _determine_hit(row.recommendation, ticker_return)

                conn.execute(text("""
                    UPDATE pipeline_signals
                    SET price_30d_later = :price_30d_later,
                        return_30d      = :return_30d,
                        spy_return_30d  = :spy_return_30d,
                        hit             = :hit,
                        eval_status     = 'evaluated',
                        eval_date       = :eval_date
                    WHERE id = :id
                """), {
                    "price_30d_later": (
                        round(row.price_at_signal * (1 + ticker_return), 4)
                        if row.price_at_signal else None
                    ),
                    "return_30d": ticker_return,
                    "spy_return_30d": spy_return,
                    "hit": hit,
                    "eval_date": now,
                    "id": row.id,
                })
                conn.commit()
                summary["signals_evaluated"] += 1

                logger.info(
                    f"eval_job: evaluated {row.ticker} — "
                    f"recommendation={row.recommendation} "
                    f"return_30d={ticker_return:.2%} "
                    f"spy={spy_return:.2%} "
                    f"hit={hit}"
                )

        # Aggregate into eval_run row
        if summary["signals_evaluated"] > 0:
            _write_eval_run(engine, now)

        return summary

    except Exception as e:
        logger.error(f"eval_job: fatal error in maturation job: {e}", exc_info=True)
        return summary


def _write_eval_run(engine: Engine, run_time: datetime) -> None:
    """
    Aggregate all evaluated signals into a single eval_run row.
    Called after each maturation batch.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE recommendation != 'Hold') as directional_count,
                    COUNT(*) FILTER (WHERE recommendation = 'Buy') as buy_count,
                    COUNT(*) FILTER (WHERE recommendation = 'Sell') as sell_count,
                    COUNT(*) FILTER (WHERE recommendation = 'Hold') as hold_count,

                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE recommendation != 'Hold') as hit_rate_overall,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE recommendation = 'Buy') as hit_rate_buy,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE recommendation = 'Sell') as hit_rate_sell,

                    AVG(return_30d) FILTER (WHERE recommendation = 'Buy') as avg_return_buy,
                    AVG(return_30d) FILTER (WHERE recommendation = 'Sell') as avg_return_sell,

                    AVG(return_30d - spy_return_30d)
                        FILTER (WHERE recommendation = 'Buy' AND spy_return_30d IS NOT NULL)
                        as avg_alpha_buy,
                    AVG(return_30d - spy_return_30d)
                        FILTER (WHERE recommendation = 'Sell' AND spy_return_30d IS NOT NULL)
                        as avg_alpha_sell,

                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE confidence = 'High' AND recommendation != 'Hold')
                        as hit_rate_high,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE confidence = 'Medium' AND recommendation != 'Hold')
                        as hit_rate_medium,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE confidence = 'Low' AND recommendation != 'Hold')
                        as hit_rate_low,

                    MAX(model_version) as model_version

                FROM pipeline_signals
                WHERE eval_status = 'evaluated'
            """)).fetchone()

            if not result or result.directional_count == 0:
                return

            conn.execute(text("""
                INSERT INTO eval_runs (
                    run_id, model_version, triggered_by,
                    signals_evaluated, buy_signals, sell_signals, hold_signals,
                    hit_rate_overall, hit_rate_buy, hit_rate_sell,
                    avg_return_buy, avg_return_sell,
                    avg_alpha_buy, avg_alpha_sell,
                    hit_rate_high_confidence, hit_rate_medium_confidence, hit_rate_low_confidence,
                    created_at
                ) VALUES (
                    :run_id, :model_version, 'nightly',
                    :signals_evaluated, :buy_signals, :sell_signals, :hold_signals,
                    :hit_rate_overall, :hit_rate_buy, :hit_rate_sell,
                    :avg_return_buy, :avg_return_sell,
                    :avg_alpha_buy, :avg_alpha_sell,
                    :hit_rate_high, :hit_rate_medium, :hit_rate_low,
                    :created_at
                )
            """), {
                "run_id": f"eval-{run_time.strftime('%Y-%m-%d')}",
                "model_version": result.model_version or "unknown",
                "signals_evaluated": result.directional_count,
                "buy_signals": result.buy_count or 0,
                "sell_signals": result.sell_count or 0,
                "hold_signals": result.hold_count or 0,
                "hit_rate_overall": result.hit_rate_overall,
                "hit_rate_buy": result.hit_rate_buy,
                "hit_rate_sell": result.hit_rate_sell,
                "avg_return_buy": result.avg_return_buy,
                "avg_return_sell": result.avg_return_sell,
                "avg_alpha_buy": result.avg_alpha_buy,
                "avg_alpha_sell": result.avg_alpha_sell,
                "hit_rate_high": result.hit_rate_high,
                "hit_rate_medium": result.hit_rate_medium,
                "hit_rate_low": result.hit_rate_low,
                "created_at": run_time,
            })
            conn.commit()

            logger.info(
                f"eval_job: eval_run written — "
                f"signals={result.directional_count} "
                f"hit_rate_buy={result.hit_rate_buy:.1%} "
                f"hit_rate_sell={result.hit_rate_sell:.1%} "
                f"avg_alpha_buy={result.avg_alpha_buy:.2%}"
                if result.avg_alpha_buy else
                f"eval_job: eval_run written — signals={result.directional_count}"
            )

    except Exception as e:
        logger.error(f"eval_job: failed to write eval_run: {e}", exc_info=True)
