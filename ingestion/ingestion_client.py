"""
ingestion/ingestion_client.py — FMP and Finnhub API clients

Design decisions:
- Two clean classes: FMPClient and FinnhubClient. One responsibility each.
  No side effects — they fetch and return, never write to DB.
  Scheduler owns the write logic. Clients own the fetch logic.
- All methods return validated Pydantic models (from models.py), never raw dicts.
  If the API response fails validation, the exception surfaces here with context,
  not silently inside an agent prompt 10 seconds later.
- Timeouts on every request — external APIs hang. 10s is generous but bounded.
- Per-ticker error isolation — one bad ticker never aborts the batch.
  Each method returns None on failure and logs the reason.
- Rate limiting via simple time.sleep() between batches — both free tiers are
  generous enough that this is sufficient without a full token bucket.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from ingestion.models import (
    RawFMPFundamentals,
    RawFinnhubArticle,
    ProcessedFundamentals,
    ProcessedNewsArticle,
)

logger = logging.getLogger(__name__)


# ── FMP Client ────────────────────────────────────────────────────────────────

class FMPClient:
    """
    Financial Modeling Prep API client.
    Replaces yfinance for fundamentals: P/E, EPS, revenue growth, D/E.
    Source: SEC EDGAR filings — fully auditable provenance.
    Free tier: 250 calls/day.
    """

    BASE_URL = "https://financialmodelingprep.com/api/v3"

    def __init__(self):
        self.api_key = os.getenv("FMP_API_KEY")
        if not self.api_key:
            raise RuntimeError("FMP_API_KEY environment variable is not set")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FinanceAgent/1.0"})

    def fetch_fundamentals(self, ticker: str) -> Optional[tuple[RawFMPFundamentals, str]]:
        """
        Fetch company profile + key ratios for a ticker.
        Returns (validated_raw_model, raw_json_string) or None on failure.
        raw_json_string is what gets written to raw_fundamentals.raw_json.
        """
        try:
            # /profile endpoint — company overview, P/E, market cap, sector
            url = f"{self.BASE_URL}/profile/{ticker}"
            resp = self.session.get(
                url,
                params={"apikey": self.api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data or not isinstance(data, list) or len(data) == 0:
                logger.warning(f"FMP: empty profile response for {ticker}")
                return None

            profile = data[0]

            # /ratios endpoint — revenue growth, D/E (more reliable than profile)
            ratios_url = f"{self.BASE_URL}/ratios-ttm/{ticker}"
            ratios_resp = self.session.get(
                ratios_url,
                params={"apikey": self.api_key},
                timeout=10,
            )
            ratios_resp.raise_for_status()
            ratios_data = ratios_resp.json()
            ratios = ratios_data[0] if ratios_data and isinstance(ratios_data, list) else {}

            # Merge profile + ratios into one flat dict for raw storage
            merged = {**profile, **ratios}
            raw_json = json.dumps(merged)

            # Validate shape — surfaces API contract changes immediately
            validated = RawFMPFundamentals(
                symbol=profile.get("symbol"),
                companyName=profile.get("companyName"),
                pe=profile.get("pe"),
                eps=profile.get("eps"),
                revenueGrowth=ratios.get("revenueGrowthTTM"),
                debtToEquity=ratios.get("debtEquityRatioTTM"),
                mktCap=profile.get("mktCap"),
                sector=profile.get("sector"),
            )

            logger.info(f"FMP: fundamentals fetched for {ticker} — pe={validated.pe} eps={validated.eps}")
            return validated, raw_json

        except requests.exceptions.Timeout:
            logger.warning(f"FMP: timeout fetching {ticker}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.warning(f"FMP: HTTP error for {ticker}: {e}")
            return None
        except Exception as e:
            logger.error(f"FMP: unexpected error for {ticker}: {e}", exc_info=True)
            return None

    def to_processed(
        self,
        raw: RawFMPFundamentals,
        ticker: str,
        source_raw_id: int,
    ) -> ProcessedFundamentals:
        """
        Map validated raw FMP data → ProcessedFundamentals.
        This is the transformation layer — raw field names → our schema.
        """
        return ProcessedFundamentals(
            ticker=ticker,
            pe_ratio=raw.pe,
            eps=raw.eps,
            revenue_growth=raw.revenueGrowth,
            debt_to_equity=raw.debtToEquity,
            market_cap=raw.mktCap,
            sector=raw.sector,
            processed_at=datetime.now(timezone.utc),
            source_raw_id=source_raw_id,
        )


# ── Finnhub Client ────────────────────────────────────────────────────────────

class FinnhubClient:
    """
    Finnhub API client for company news.
    Replaces yfinance news scraping with sourced articles
    (Reuters, Bloomberg wire) with publisher attribution and timestamps.
    Free tier: 60 calls/minute.
    """

    BASE_URL = "https://finnhub.io/api/v1"
    MAX_ARTICLES_PER_TICKER = 10    # cap per ticker — agents use top 10 headlines

    def __init__(self):
        self.api_key = os.getenv("FINNHUB_API_KEY")
        if not self.api_key:
            raise RuntimeError("FINNHUB_API_KEY environment variable is not set")
        self.session = requests.Session()
        self.session.headers.update({"X-Finnhub-Token": self.api_key})

    def fetch_news(self, ticker: str) -> Optional[tuple[list[RawFinnhubArticle], str]]:
        """
        Fetch last 24h of company news for a ticker.
        Returns (list_of_validated_articles, raw_json_string) or None on failure.
        """
        try:
            from datetime import timedelta
            now = datetime.now(timezone.utc)
            date_from = (now - timedelta(hours=24)).strftime("%Y-%m-%d")
            date_to = now.strftime("%Y-%m-%d")

            url = f"{self.BASE_URL}/company-news"
            resp = self.session.get(
                url,
                params={
                    "symbol": ticker,
                    "from": date_from,
                    "to": date_to,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data or not isinstance(data, list):
                logger.warning(f"Finnhub: no news for {ticker} between {date_from} and {date_to}")
                return [], json.dumps([])

            # Validate each article — skip malformed ones, don't abort batch
            validated_articles = []
            for article in data[:self.MAX_ARTICLES_PER_TICKER]:
                try:
                    validated = RawFinnhubArticle(
                        headline=article.get("headline"),
                        source=article.get("source"),
                        datetime=article.get("datetime"),
                        summary=article.get("summary"),
                        url=article.get("url"),
                    )
                    if validated.headline:  # skip articles with no headline
                        validated_articles.append(validated)
                except Exception as e:
                    logger.warning(f"Finnhub: skipping malformed article for {ticker}: {e}")
                    continue

            raw_json = json.dumps(data[:self.MAX_ARTICLES_PER_TICKER])
            logger.info(f"Finnhub: {len(validated_articles)} articles fetched for {ticker}")
            return validated_articles, raw_json

        except requests.exceptions.Timeout:
            logger.warning(f"Finnhub: timeout fetching news for {ticker}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.warning(f"Finnhub: HTTP error for {ticker}: {e}")
            return None
        except Exception as e:
            logger.error(f"Finnhub: unexpected error for {ticker}: {e}", exc_info=True)
            return None

    def to_processed(
        self,
        articles: list[RawFinnhubArticle],
        ticker: str,
        source_raw_id: int,
    ) -> list[ProcessedNewsArticle]:
        """
        Map validated raw Finnhub articles → list of ProcessedNewsArticle.
        Converts Unix timestamps → timezone-aware datetimes.
        """
        processed = []
        now = datetime.now(timezone.utc)

        for article in articles:
            published_at = None
            if article.datetime:
                try:
                    published_at = datetime.fromtimestamp(article.datetime, tz=timezone.utc)
                except Exception:
                    pass  # bad timestamp — leave as None, don't abort

            processed.append(ProcessedNewsArticle(
                ticker=ticker,
                headline=article.headline,
                publisher=article.source,
                published_at=published_at,
                processed_at=now,
                source_raw_id=source_raw_id,
            ))

        return processed


# ── Rate limiting helper ──────────────────────────────────────────────────────

def batch_sleep(seconds: float = 0.25) -> None:
    """
    Simple sleep between ticker batches.
    FMP free tier: 250 calls/day (~1 call per 6 minutes if spread evenly,
    but nightly batch runs all at once so we just need to avoid burst blocking).
    Finnhub free tier: 60 calls/minute — 0.25s between calls = 4/sec = well within limit.
    """
    time.sleep(seconds)