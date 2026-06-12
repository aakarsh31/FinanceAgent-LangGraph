"""
evaluation/signal_store.py — Records pipeline signals to Postgres

Design decisions:
- Called from app.py immediately after /approve returns the supervisor report.
  The signal is recorded at recommendation time with eval_status="pending".
- Price at signal time is fetched from yfinance at record time — not from the
  pipeline state, because the pipeline may have used cached data. We want the
  actual market price at the moment the recommendation was made.
- run_id format: "{ticker}-{date}-{model_version}" — human readable and unique.
- Writing is non-fatal — if signal_store fails, the user still gets their report.
  We log the error and move on. Never block the user experience for telemetry.
- model_version is passed in from app.py as an env var (MODEL_VERSION).
  Default "day7-baseline" — update when agents change meaningfully.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Bump this when agents change meaningfully — creates a new versioned series
MODEL_VERSION = os.getenv("MODEL_VERSION", "day7-baseline")


def _get_current_price(ticker: str) -> Optional[float]:
    """Fetch current market price for a ticker."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if price:
            return float(price)
        # Fallback: last close from history
        hist = stock.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
        return None
    except Exception as e:
        logger.warning(f"signal_store: could not fetch price for {ticker}: {e}")
        return None


def record_signal(
    engine: Engine,
    ticker: str,
    asset_class: str,
    supervisor_report: dict,
    data_provenance: Optional[dict] = None,
) -> Optional[str]:
    """
    Record a pipeline signal to Postgres immediately after supervisor completes.

    Returns the run_id if successful, None if failed.
    Failure is non-fatal — caller should log but not raise.

    Args:
        engine: SQLAlchemy engine
        ticker: e.g. "AAPL"
        asset_class: "equity" or "crypto"
        supervisor_report: dict from SupervisorReport.model_dump()
        data_provenance: optional dict from FinanceState["data_provenance"]
    """
    if not engine:
        logger.warning("signal_store: no engine available — skipping signal recording")
        return None

    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        run_id = f"{ticker}-{date_str}-{MODEL_VERSION}"

        recommendation = supervisor_report.get("recommendation", "Hold")
        confidence = supervisor_report.get("confidence", "Low")
        summary = supervisor_report.get("summary")
        bull_case = supervisor_report.get("bull_case")
        bear_case = supervisor_report.get("bear_case")
        key_metrics = supervisor_report.get("key_metrics", [])

        # Policy engine fields — for per-rule performance attribution
        policy_rule_fired            = supervisor_report.get("policy_rule_fired", "")
        policy_confidence_floor      = supervisor_report.get("policy_confidence_floor", "Low")
        analyst_override             = supervisor_report.get("policy_analyst_override", False)
        llm_recommendation_matched   = supervisor_report.get("llm_recommendation_matched", True)

        # Get current market price at signal time
        price_at_signal = _get_current_price(ticker)

        with engine.connect() as conn:
            # Check if we already recorded a signal for this ticker today
            existing = conn.execute(text("""
                SELECT id FROM pipeline_signals
                WHERE ticker = :ticker
                  AND model_version = :model_version
                  AND DATE(signal_date) = :date
                LIMIT 1
            """), {
                "ticker": ticker,
                "model_version": MODEL_VERSION,
                "date": date_str,
            }).fetchone()

            if existing:
                logger.info(f"signal_store: signal already recorded for {ticker} today — skipping duplicate")
                return run_id

            conn.execute(text("""
                INSERT INTO pipeline_signals (
                    run_id, ticker, asset_class, model_version,
                    recommendation, confidence,
                    policy_rule_fired, policy_confidence_floor,
                    analyst_override, llm_recommendation_matched,
                    supervisor_summary, bull_case, bear_case, key_metrics,
                    price_at_signal, signal_date,
                    eval_status, created_at
                ) VALUES (
                    :run_id, :ticker, :asset_class, :model_version,
                    :recommendation, :confidence,
                    :policy_rule_fired, :policy_confidence_floor,
                    :analyst_override, :llm_recommendation_matched,
                    :supervisor_summary, :bull_case, :bear_case, :key_metrics,
                    :price_at_signal, :signal_date,
                    'pending', :created_at
                )
            """), {
                "run_id": run_id,
                "ticker": ticker,
                "asset_class": asset_class,
                "model_version": MODEL_VERSION,
                "recommendation": recommendation,
                "confidence": confidence,
                "policy_rule_fired": policy_rule_fired,
                "policy_confidence_floor": policy_confidence_floor,
                "analyst_override": analyst_override,
                "llm_recommendation_matched": llm_recommendation_matched,
                "supervisor_summary": summary,
                "bull_case": bull_case,
                "bear_case": bear_case,
                "key_metrics": json.dumps(key_metrics),
                "price_at_signal": price_at_signal,
                "signal_date": now,
                "created_at": now,
            })
            conn.commit()

        logger.info(
            f"signal_store: recorded signal — "
            f"run_id={run_id} ticker={ticker} "
            f"recommendation={recommendation} confidence={confidence} "
            f"rule={policy_rule_fired} analyst_override={analyst_override} "
            f"llm_matched={llm_recommendation_matched} "
            f"price_at_signal={price_at_signal} "
            f"model_version={MODEL_VERSION}"
        )
        return run_id

    except Exception as e:
        logger.error(f"signal_store: failed to record signal for {ticker}: {e}", exc_info=True)
        return None
