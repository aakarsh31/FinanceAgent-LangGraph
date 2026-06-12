"""
evaluation/db_eval.py — Evaluation table definitions

Design decisions:
- Separate from ingestion/db.py — evaluation is a different concern.
  Ingestion owns raw/processed market data.
  Evaluation owns signal quality measurement.
- pipeline_signals is the core table — one row per pipeline run per ticker.
  Written immediately after SupervisorAgent completes.
  Filled in 30 days later by the eval job.
- eval_runs is the aggregated summary — one row per eval job execution.
  This is what surfaces on the dashboard as hit rate.
- model_version is a first-class field — signals get tagged by version string
  so hit rates can be compared across model versions.
- spy_return_30d stored on every signal for alpha calculation.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    MetaData,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.engine import Engine

eval_metadata = MetaData()

# ── Pipeline signals ──────────────────────────────────────────────────────────
# One row per pipeline run per ticker.
# Written at recommendation time, filled in at maturation (30 days later).

pipeline_signals = Table(
    "pipeline_signals",
    eval_metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),

    # Identity
    Column("run_id", String(100), nullable=False),          # e.g. "AAPL-2026-06-05-day7-baseline"
    Column("ticker", String(20), nullable=False),
    Column("asset_class", String(20), nullable=False),      # "equity", "crypto"
    Column("model_version", String(100), nullable=False),   # "day7-baseline", "day8-technicalagent"

    # Signal
    Column("recommendation", String(10), nullable=False),   # "Buy", "Hold", "Sell"
    Column("confidence", String(10), nullable=False),       # "High", "Medium", "Low"

    # Policy engine fields — for per-rule performance attribution
    Column("policy_rule_fired", String(100), nullable=True),    # e.g. "explicit_sell_overvalued_bearish"
    Column("policy_confidence_floor", String(10), nullable=True), # "High", "Medium", "Low"
    Column("analyst_override", Boolean, nullable=True),          # True when diverging from Wall Street
    Column("llm_recommendation_matched", Boolean, nullable=True), # False = LLM tried to override policy

    # Reasoning (stored for audit + LangSmith correlation)
    Column("supervisor_summary", Text, nullable=True),
    Column("bull_case", Text, nullable=True),
    Column("bear_case", Text, nullable=True),
    Column("key_metrics", Text, nullable=True),             # JSON array as string

    # Price at signal time
    Column("price_at_signal", Float, nullable=True),
    Column("signal_date", DateTime(timezone=True), nullable=False),

    # Filled in by eval job 30 days later
    Column("price_30d_later", Float, nullable=True),
    Column("return_30d", Float, nullable=True),             # decimal e.g. 0.12 = 12%
    Column("spy_return_30d", Float, nullable=True),         # SPY return same window
    Column("hit", Boolean, nullable=True),                  # True if call was correct
    Column("eval_date", DateTime(timezone=True), nullable=True),
    Column("eval_status", String(20), nullable=False, default="pending"),
    # "pending" → signal not yet 30 days old
    # "matured" → 30 days passed, ready to evaluate
    # "evaluated" → return computed, hit determined
    # "failed" → yfinance returned no data

    Column("created_at", DateTime(timezone=True), nullable=False),
)

# ── Eval runs ─────────────────────────────────────────────────────────────────
# One row per eval job execution — aggregated hit rate across all matured signals.

eval_runs = Table(
    "eval_runs",
    eval_metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", String(100), nullable=False),          # matches pipeline_signals.run_id prefix
    Column("model_version", String(100), nullable=False),
    Column("triggered_by", String(20), nullable=False),     # "nightly", "manual"

    # Hit rate metrics
    Column("signals_evaluated", BigInteger, nullable=False),
    Column("buy_signals", BigInteger, nullable=True),
    Column("sell_signals", BigInteger, nullable=True),
    Column("hold_signals", BigInteger, nullable=True),
    Column("hit_rate_overall", Float, nullable=True),       # all signals
    Column("hit_rate_buy", Float, nullable=True),           # Buy signals only
    Column("hit_rate_sell", Float, nullable=True),          # Sell signals only

    # Return metrics
    Column("avg_return_buy", Float, nullable=True),         # avg 30d return on Buy calls
    Column("avg_return_sell", Float, nullable=True),
    Column("avg_alpha_buy", Float, nullable=True),          # avg_return_buy - avg spy_return_30d
    Column("avg_alpha_sell", Float, nullable=True),

    # Confidence breakdown
    Column("hit_rate_high_confidence", Float, nullable=True),
    Column("hit_rate_medium_confidence", Float, nullable=True),
    Column("hit_rate_low_confidence", Float, nullable=True),

    Column("created_at", DateTime(timezone=True), nullable=False),
)


def init_eval_db(engine: Engine) -> None:
    """
    Create evaluation tables if they don't exist.
    Safe to call on every startup.
    Also creates indexes for common query patterns.
    """
    eval_metadata.create_all(engine)

    with engine.connect() as conn:
        # Index for eval job — find pending signals by date
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
            idx_signals_eval_status_date
            ON pipeline_signals (eval_status, signal_date)
        """))

        # Index for hit rate queries by model version
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
            idx_signals_model_version
            ON pipeline_signals (model_version, recommendation, hit)
        """))

        # Approval audit table — one row per approve/reject decision
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS approval_audit (
                id             BIGSERIAL PRIMARY KEY,
                thread_id      TEXT NOT NULL,
                decision       TEXT NOT NULL,
                decided_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ticker         TEXT,
                recommendation TEXT,
                confidence     TEXT,
                decided_by     TEXT NOT NULL DEFAULT 'default',
                UNIQUE (thread_id)
            )
        """))

        # Add decided_by column if upgrading from earlier schema version
        conn.execute(text("""
            ALTER TABLE approval_audit
            ADD COLUMN IF NOT EXISTS decided_by TEXT NOT NULL DEFAULT 'default'
        """))

        # Add policy engine columns to pipeline_signals if upgrading from earlier schema
        for col_sql in [
            "ALTER TABLE pipeline_signals ADD COLUMN IF NOT EXISTS policy_rule_fired VARCHAR(100)",
            "ALTER TABLE pipeline_signals ADD COLUMN IF NOT EXISTS policy_confidence_floor VARCHAR(10)",
            "ALTER TABLE pipeline_signals ADD COLUMN IF NOT EXISTS analyst_override BOOLEAN",
            "ALTER TABLE pipeline_signals ADD COLUMN IF NOT EXISTS llm_recommendation_matched BOOLEAN",
        ]:
            conn.execute(text(col_sql))

        conn.commit()