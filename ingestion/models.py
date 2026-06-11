"""
ingestion/models.py — Pydantic models for ingestion validation layer

Design decisions:
- Two model families per data type: Raw* and Processed*.
  Raw models validate the API response shape — they fail loudly if the API
  changes its contract. Processed models are what agents actually consume.
- All fields Optional with None defaults on Raw models — external APIs return
  inconsistent shapes. We validate presence at the Processed layer, not Raw.
- Processed models mirror the fields in financestate.py exactly so DataFetchAgent
  can map Postgres rows → FinanceState without any transformation logic.
- DataFreshness is the staleness oracle model — returned by every freshness check.
- Staleness thresholds are constants defined here, not magic numbers scattered
  across the codebase. Change them in one place.
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, field_validator


# ── Staleness thresholds (hours) ──────────────────────────────────────────────
# These are the authoritative thresholds. DataFetchAgent imports these directly.

FUNDAMENTALS_TTL_HOURS = 24   # P/E, EPS update at most daily — earnings are quarterly
NEWS_TTL_HOURS = 1            # A 2-hour-old earnings surprise headline is stale
MACRO_TTL_HOURS = 6           # FRED data updates intraday but rarely changes fast


# ── Raw models (validate API response shape) ──────────────────────────────────

class RawFMPFundamentals(BaseModel):
    """
    Validates the shape of a single FMP /profile or /ratios response.
    All Optional — FMP returns null for many fields on smaller tickers.
    """
    symbol: Optional[str] = None
    companyName: Optional[str] = None
    pe: Optional[float] = None                  # trailing P/E
    eps: Optional[float] = None                 # trailing EPS
    revenueGrowth: Optional[float] = None       # YoY revenue growth as decimal
    debtToEquity: Optional[float] = None
    mktCap: Optional[float] = None
    sector: Optional[str] = None

    class Config:
        extra = "allow"   # FMP returns many extra fields — don't reject them


class RawFinnhubArticle(BaseModel):
    """
    Validates a single Finnhub news article.
    headline and source are the only fields we care about downstream.
    """
    headline: Optional[str] = None
    source: Optional[str] = None
    datetime: Optional[int] = None              # Unix timestamp
    summary: Optional[str] = None
    url: Optional[str] = None

    class Config:
        extra = "allow"


class RawFREDObservation(BaseModel):
    """
    Validates a single FRED observation from /series/observations.
    """
    date: Optional[str] = None                  # "2024-12-01"
    value: Optional[str] = None                 # FRED returns values as strings, "." for missing

    class Config:
        extra = "allow"


# ── Processed models (agent-ready, schema-enforced) ───────────────────────────

class ProcessedFundamentals(BaseModel):
    """
    What DataFetchAgent reads from processed_fundamentals table.
    Matches FundamentalsData in financestate.py exactly.
    """
    ticker: str
    pe_ratio: Optional[float] = None
    eps: Optional[float] = None
    revenue_growth: Optional[float] = None      # decimal e.g. 0.12 = 12%
    debt_to_equity: Optional[float] = None
    market_cap: Optional[float] = None
    sector: Optional[str] = None
    processed_at: datetime
    source_raw_id: int

    def to_state_dict(self) -> dict:
        """Maps to the shape FundamentalsAgent expects in FinanceState."""
        return {
            "PE_ratio": self.pe_ratio,
            "EPS": self.eps,
            "revenue_growth": self.revenue_growth,
            "debt_to_equity": self.debt_to_equity,
        }


class ProcessedNewsArticle(BaseModel):
    """
    A single processed news article from processed_news table.
    """
    ticker: str
    headline: str
    publisher: Optional[str] = None
    published_at: Optional[datetime] = None
    processed_at: datetime
    source_raw_id: int


class ProcessedMacro(BaseModel):
    """
    A single processed macro indicator from processed_macro table.
    """
    indicator: str                              # "fed_funds_rate", "cpi_yoy", etc.
    value: Optional[float] = None
    period: Optional[str] = None               # "2024-12-01"
    processed_at: datetime
    source_raw_id: int


# ── Freshness check result ────────────────────────────────────────────────────

class DataFreshness(BaseModel):
    """
    Returned by every staleness check in DataFetchAgent.
    Carries enough context to inject into agent prompts.
    """
    ticker: str
    data_type: str                              # "fundamentals", "news", "macro"
    is_fresh: bool
    last_updated: Optional[datetime] = None
    data_age_hours: Optional[float] = None
    source: Optional[str] = None               # "fmp", "finnhub", "fred"
    status: str = "unknown"                    # "fresh", "stale", "missing", "failed"

    def prompt_annotation(self) -> str:
        """
        Returns a string injected into agent prompts so the LLM knows
        the age and provenance of the data it's reasoning over.

        Example outputs:
          "Data sourced from fmp, 3.2 hours old."
          "Data sourced from live API fallback (fundamentals not in cache)."
          "Data age unknown."
        """
        if self.status == "missing":
            return f"Data sourced from live API fallback ({self.data_type} not in cache)."
        if self.data_age_hours is not None and self.source:
            return f"Data sourced from {self.source}, {self.data_age_hours:.1f} hours old."
        return "Data age unknown."


# ── Ingestion job result ──────────────────────────────────────────────────────

class IngestionResult(BaseModel):
    """
    Returned by each ingestion job. Aggregated by the scheduler for logging.
    """
    job_name: str                               # "fundamentals", "news", "macro"
    tickers_attempted: int = 0
    tickers_succeeded: int = 0
    tickers_failed: int = 0
    rows_written: int = 0
    errors: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    @field_validator("errors", mode="before")
    @classmethod
    def truncate_errors(cls, v):
        """Cap error list at 50 — don't let a bad batch flood logs."""
        if isinstance(v, list) and len(v) > 50:
            return v[:50]
        return v

    def summary(self) -> str:
        duration = ""
        if self.completed_at:
            secs = (self.completed_at - self.started_at).total_seconds()
            duration = f" in {secs:.1f}s"
        return (
            f"[{self.job_name}] {self.tickers_succeeded}/{self.tickers_attempted} tickers "
            f"succeeded, {self.rows_written} rows written{duration}"
        )
