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

import json
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


def _determine_hit(
    recommendation: str,
    return_30d: float,
    spy_return_30d: Optional[float] = None,
    relative: bool = True,
) -> Optional[bool]:
    """
    Determine if the recommendation was correct.
    Hold signals return None — excluded from hit rate.

    Args:
        relative: if True (default), Buy is a hit only if it beat SPY.
                  if False, Buy is a hit if return > 0 (absolute direction).

    The relative definition is more demanding and more meaningful — beating
    a passive SPY position is the correct bar for active recommendations.
    """
    if recommendation == "Buy":
        if relative and spy_return_30d is not None:
            return return_30d > spy_return_30d  # beat the benchmark
        return return_30d > 0
    elif recommendation == "Sell":
        if relative and spy_return_30d is not None:
            return return_30d < spy_return_30d  # underperformed benchmark
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

                hit = _determine_hit(row.recommendation, ticker_return, spy_return, relative=True)
                # Also compute absolute hit for comparison
                hit_absolute = _determine_hit(row.recommendation, ticker_return, relative=False)

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


def _binomial_ci(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """
    Wilson score confidence interval for a binomial proportion.
    More accurate than normal approximation for small n.

    Returns (lower, upper) bounds at 95% confidence (z=1.96).
    Returns (0.0, 1.0) when n == 0.
    """
    if n == 0:
        return (0.0, 1.0)
    p = hits / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * ((p * (1 - p) / n + z**2 / (4 * n**2)) ** 0.5) / denom
    return (round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4))


def _write_eval_run(engine: Engine, run_time: datetime) -> None:
    """
    Aggregate all evaluated signals into a single eval_run row.
    Includes baselines, per-rule attribution, and divergence metric.
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

                    -- Primary hit rate: SPY-relative (beat the benchmark)
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE recommendation != 'Hold') as hit_rate_overall,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE recommendation = 'Buy') as hit_rate_buy,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE recommendation = 'Sell') as hit_rate_sell,

                    -- Count hits for CI calculation
                    COUNT(*) FILTER (WHERE hit = true AND recommendation != 'Hold') as hits_total,
                    COUNT(*) FILTER (WHERE hit = true AND recommendation = 'Buy') as hits_buy,
                    COUNT(*) FILTER (WHERE hit = true AND recommendation = 'Sell') as hits_sell,

                    -- Return metrics
                    AVG(return_30d) FILTER (WHERE recommendation = 'Buy') as avg_return_buy,
                    AVG(return_30d) FILTER (WHERE recommendation = 'Sell') as avg_return_sell,

                    -- Alpha vs SPY
                    AVG(return_30d - spy_return_30d)
                        FILTER (WHERE recommendation = 'Buy' AND spy_return_30d IS NOT NULL)
                        as avg_alpha_buy,
                    AVG(return_30d - spy_return_30d)
                        FILTER (WHERE recommendation = 'Sell' AND spy_return_30d IS NOT NULL)
                        as avg_alpha_sell,

                    -- SPY return stats (for always-Buy baseline)
                    AVG(spy_return_30d) FILTER (WHERE spy_return_30d IS NOT NULL) as avg_spy_return,
                    AVG(CASE WHEN spy_return_30d > 0 THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE spy_return_30d IS NOT NULL) as spy_positive_rate,

                    -- Confidence tier hit rates
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE policy_confidence_floor = 'High' AND recommendation != 'Hold')
                        as hit_rate_high,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE policy_confidence_floor = 'Medium' AND recommendation != 'Hold')
                        as hit_rate_medium,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE policy_confidence_floor = 'Low' AND recommendation != 'Hold')
                        as hit_rate_low,

                    -- Divergence metric: when we override analyst consensus, do we win?
                    COUNT(*) FILTER (WHERE analyst_override = true AND recommendation != 'Hold')
                        as divergence_count,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE analyst_override = true AND recommendation != 'Hold')
                        as divergence_hit_rate,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE analyst_override = false AND recommendation != 'Hold')
                        as consensus_hit_rate,

                    MAX(model_version) as model_version

                FROM pipeline_signals
                WHERE eval_status = 'evaluated'
            """)).fetchone()

            if not result or result.directional_count == 0:
                return

            # Confidence intervals (Wilson score, 95%)
            ci_overall = _binomial_ci(result.hits_total or 0, result.directional_count or 0)
            ci_buy     = _binomial_ci(result.hits_buy or 0, result.buy_count or 0)
            ci_sell    = _binomial_ci(result.hits_sell or 0, result.sell_count or 0)

            # Per-rule attribution — which rules are making money?
            rule_rows = conn.execute(text("""
                SELECT
                    policy_rule_fired,
                    COUNT(*) as n,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END) as hit_rate,
                    AVG(return_30d - spy_return_30d)
                        FILTER (WHERE spy_return_30d IS NOT NULL) as avg_alpha,
                    AVG(return_30d) as avg_return
                FROM pipeline_signals
                WHERE eval_status = 'evaluated'
                  AND recommendation != 'Hold'
                  AND policy_rule_fired IS NOT NULL
                GROUP BY policy_rule_fired
                ORDER BY n DESC
            """)).fetchall()

            rule_attribution = [
                {
                    "rule": row.policy_rule_fired,
                    "n": row.n,
                    "hit_rate": round(float(row.hit_rate), 4) if row.hit_rate is not None else None,
                    "avg_alpha": round(float(row.avg_alpha), 4) if row.avg_alpha is not None else None,
                    "avg_return": round(float(row.avg_return), 4) if row.avg_return is not None else None,
                    "ci": _binomial_ci(
                        round(float(row.hit_rate) * row.n) if row.hit_rate else 0,
                        row.n
                    ),
                }
                for row in rule_rows
            ]

            import json as _json
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

            # Log comprehensive summary
            n = result.directional_count
            logger.info(
                f"eval_job: eval_run written — n={n} "
                f"hit_rate={result.hit_rate_overall:.1%} "
                f"CI=[{ci_overall[0]:.1%},{ci_overall[1]:.1%}] "
                f"(n too small for significance)" if n < 30 else
                f"eval_job: eval_run written — n={n} "
                f"hit_rate={result.hit_rate_overall:.1%} "
                f"CI=[{ci_overall[0]:.1%},{ci_overall[1]:.1%}]"
            )

            # Log baselines for comparison
            if result.avg_spy_return is not None:
                logger.info(
                    f"eval_job: baselines — "
                    f"always_buy_spy_rate={result.spy_positive_rate:.1%} "
                    f"avg_spy_return={result.avg_spy_return:.2%}"
                )

            # Log divergence metric
            if result.divergence_count and result.divergence_count > 0:
                logger.info(
                    f"eval_job: divergence — "
                    f"override_hit_rate={result.divergence_hit_rate:.1%} (n={result.divergence_count}) "
                    f"vs consensus_hit_rate={result.consensus_hit_rate:.1%}"
                )

            # Log per-rule attribution
            for r in rule_attribution:
                logger.info(
                    f"eval_job: rule [{r['rule']}] "
                    f"n={r['n']} hit_rate={r['hit_rate']:.1%} "
                    f"alpha={r['avg_alpha']:.2%} "
                    f"CI=[{r['ci'][0]:.1%},{r['ci'][1]:.1%}]"
                    if r['hit_rate'] is not None else
                    f"eval_job: rule [{r['rule']}] n={r['n']} no hits yet"
                )

    except Exception as e:
        logger.error(f"eval_job: failed to write eval_run: {e}", exc_info=True)