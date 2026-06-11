"""
ingestion/db.py — Postgres connection + table definitions

Design decisions:
- SQLAlchemy Core (not ORM) — we're writing SQL-flavored code, not object graphs.
  Agents query dicts, not model instances. Core is the right tool.
- Two-table pattern per data type: raw (immutable) + processed (agent-ready).
  raw is never overwritten — if validation logic changes, reprocess from raw.
  processed always has a source_raw_id FK back to the exact raw row that produced it.
- data_freshness_meta is the single source of truth for staleness checks.
  Agents query this first — one row per (ticker, data_type), updated on every ingestion.
- Connection via DATABASE_URL env var only — no hardcoded credentials anywhere.
"""

import logging
import os

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    text,
)
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

metadata = MetaData()

# ── Raw tables (immutable — never overwritten) ────────────────────────────────

raw_fundamentals = Table(
    "raw_fundamentals",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ticker", String(20), nullable=False),
    Column("source", String(50), nullable=False),       # "fmp"
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("raw_json", Text, nullable=False),           # full API response as JSON string
)

raw_news = Table(
    "raw_news",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ticker", String(20), nullable=False),
    Column("source", String(50), nullable=False),       # "finnhub"
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("raw_json", Text, nullable=False),
)

raw_macro = Table(
    "raw_macro",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("indicator", String(50), nullable=False),    # "fed_funds_rate", "cpi_yoy", etc.
    Column("source", String(50), nullable=False),       # "fred"
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("raw_json", Text, nullable=False),
)

# ── Processed tables (agent-ready, validated, schema-enforced) ────────────────

processed_fundamentals = Table(
    "processed_fundamentals",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ticker", String(20), nullable=False),
    Column("pe_ratio", Float, nullable=True),
    Column("eps", Float, nullable=True),
    Column("revenue_growth", Float, nullable=True),     # YoY as decimal e.g. 0.12 = 12%
    Column("debt_to_equity", Float, nullable=True),
    Column("market_cap", Float, nullable=True),
    Column("sector", String(100), nullable=True),
    Column("processed_at", DateTime(timezone=True), nullable=False),
    Column("source_raw_id", BigInteger, nullable=False),  # FK → raw_fundamentals.id
)

processed_news = Table(
    "processed_news",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ticker", String(20), nullable=False),
    Column("headline", Text, nullable=False),
    Column("publisher", String(200), nullable=True),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("processed_at", DateTime(timezone=True), nullable=False),
    Column("source_raw_id", BigInteger, nullable=False),  # FK → raw_news.id
)

processed_macro = Table(
    "processed_macro",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("indicator", String(50), nullable=False),
    Column("value", Float, nullable=True),
    Column("period", String(20), nullable=True),        # e.g. "2024-12-01"
    Column("processed_at", DateTime(timezone=True), nullable=False),
    Column("source_raw_id", BigInteger, nullable=False),  # FK → raw_macro.id
)

# ── Freshness metadata (staleness oracle) ─────────────────────────────────────

data_freshness_meta = Table(
    "data_freshness_meta",
    metadata,
    # ticker + data_type is the composite unique key
    # e.g. ("AAPL", "fundamentals"), ("AAPL", "news"), ("FEDFUNDS", "macro")
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ticker", String(20), nullable=False),
    Column("data_type", String(50), nullable=False),    # "fundamentals", "news", "macro"
    Column("last_updated", DateTime(timezone=True), nullable=False),
    Column("status", String(20), nullable=False),       # "fresh", "stale", "failed"
    Column("source", String(50), nullable=False),       # "fmp", "finnhub", "fred"
    Column("row_count", Integer, nullable=True),        # how many rows were written
)

# ── Miss log (ticker promotion) ───────────────────────────────────────────────
# When an agent requests a ticker not in the nightly universe,
# we log the miss. High-miss tickers get promoted into the nightly list.

cache_miss_log = Table(
    "cache_miss_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ticker", String(20), nullable=False),
    Column("data_type", String(50), nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    Column("resolved_via", String(20), nullable=False),  # "live_api" or "cache"
)


# ── Universe table ───────────────────────────────────────────────────────────
# Live index constituent list. Refreshed nightly from FMP.
# company_name used by RSS news mapper for article → ticker matching.

universe = Table(
    "universe",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ticker", String(20), nullable=False, unique=True),
    Column("company_name", String(200), nullable=True),
    Column("sector", String(100), nullable=True),
    Column("in_sp500", Boolean, nullable=False, default=False),
    Column("in_nasdaq100", Boolean, nullable=False, default=False),
    Column("in_russell2000", Boolean, nullable=False, default=False),  # stubbed
    Column("is_active", Boolean, nullable=False, default=True),
    Column("last_updated", DateTime(timezone=True), nullable=False),
)

# ── Screening scores ──────────────────────────────────────────────────────────
# Stage 1 screener output. One row per ticker per screening run.
# top_50 flag marks tickers that survive into Stage 2 (full LLM pipeline).

screening_scores = Table(
    "screening_scores",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ticker", String(20), nullable=False),
    Column("screen_date", DateTime(timezone=True), nullable=False),
    Column("liquidity_score", Float, nullable=True),    # avg daily volume score
    Column("momentum_score", Float, nullable=True),     # 52-week return vs universe
    Column("fundamental_score", Float, nullable=True),  # P/E, revenue growth score
    Column("composite_score", Float, nullable=True),    # weighted combination
    Column("passed_screen", Boolean, nullable=False, default=False),
    Column("top_50", Boolean, nullable=False, default=False),
    Column("screen_reason", String(200), nullable=True),  # why it passed/failed
)

# ── Engine factory ────────────────────────────────────────────────────────────

def get_engine() -> Engine:
    """
    Create a SQLAlchemy engine from DATABASE_URL env var.
    Called once at app/worker startup — engine is reused for the process lifetime.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    # Railway's DATABASE_URL uses 'postgres://' scheme (older style).
    # SQLAlchemy 2.x requires 'postgresql://'.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    engine = create_engine(
        url,
        pool_size=5,           # max persistent connections
        max_overflow=10,       # burst connections above pool_size
        pool_pre_ping=True,    # test connection health before using from pool
        echo=False,            # set True locally to see SQL in logs
    )
    logger.info("Postgres engine created")
    return engine


def init_db(engine: Engine) -> None:
    """
    Create all tables if they don't exist.
    Safe to call on every startup — CREATE TABLE IF NOT EXISTS semantics.
    Also creates the unique index on data_freshness_meta(ticker, data_type).
    """
    metadata.create_all(engine)

    # Unique index on freshness meta — one row per (ticker, data_type)
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            uix_freshness_ticker_type
            ON data_freshness_meta (ticker, data_type)
        """))
        conn.commit()

    logger.info("Database tables initialized")
