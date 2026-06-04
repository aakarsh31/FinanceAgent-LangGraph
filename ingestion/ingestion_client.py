"""
ingestion/ingestion_client.py — FMP API client (v2 — stable endpoint)

Changes from v1:
- FMP migrated from /api/v3 to /stable endpoints
- URL structure changed: /profile/{ticker} → /profile?symbol={ticker}
- FMP now has its own news endpoint — dropped Finnhub dependency
  FMP /stable/news/stock?symbols={ticker} replaces Finnhub company-news
- Consolidating to one data provider simplifies auth and rate limit management
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
    Uses the new /stable endpoint (replaces deprecated /api/v3).
    Handles both fundamentals and news — one provider, one API key.
    Source: SEC EDGAR filings — fully auditable provenance.
    Free tier: 250 calls/day.
    """

    BASE_URL = "https://financialmodelingprep.com/stable"

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
        """
        try:
            # /stable/profile?symbol=AAPL — new endpoint format
            url = f"{self.BASE_URL}/profile"
            resp = self.session.get(
                url,
                params={"symbol": ticker, "apikey": self.api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data or not isinstance(data, list) or len(data) == 0:
                logger.warning(f"FMP: empty profile response for {ticker}")
                return None

            profile = data[0]

            # ratios-ttm requires paid plan — get what we can from profile
            # profile includes pe, eps, mktCap, sector on free tier
            raw_json = json.dumps(profile)

            validated = RawFMPFundamentals(
                symbol=profile.get("symbol"),
                companyName=profile.get("companyName"),
                pe=profile.get("pe"),
                eps=profile.get("eps"),
                revenueGrowth=profile.get("revenueGrowth"),       # available in profile on free tier
                debtToEquity=profile.get("debtToEquityRatio"),    # available in profile on free tier
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

    def fetch_news(self, ticker: str) -> Optional[tuple[list[RawFinnhubArticle], str]]:
        """
        Fetch latest stock news for a ticker using FMP's news endpoint.
        Replaces Finnhub — same return type for compatibility with scheduler.
        Returns (list_of_validated_articles, raw_json_string) or None on failure.
        """
        try:
            url = f"{self.BASE_URL}/news/stock"
            resp = self.session.get(
                url,
                params={"symbols": ticker, "apikey": self.api_key, "limit": 10},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data or not isinstance(data, list):
                logger.warning(f"FMP news: no articles for {ticker}")
                return [], json.dumps([])

            validated_articles = []
            for article in data[:10]:
                try:
                    # Map FMP news fields → RawFinnhubArticle shape for compatibility
                    pub_date = article.get("publishedDate", "")
                    ts = None
                    if pub_date:
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                            ts = int(dt.timestamp())
                        except Exception:
                            pass

                    validated = RawFinnhubArticle(
                        headline=article.get("title"),
                        source=article.get("site"),
                        datetime=ts,
                        summary=article.get("text", "")[:500] if article.get("text") else None,
                        url=article.get("url"),
                    )
                    if validated.headline:
                        validated_articles.append(validated)
                except Exception as e:
                    logger.warning(f"FMP news: skipping malformed article for {ticker}: {e}")
                    continue

            raw_json = json.dumps(data[:10])
            logger.info(f"FMP news: {len(validated_articles)} articles fetched for {ticker}")
            return validated_articles, raw_json

        except requests.exceptions.Timeout:
            logger.warning(f"FMP news: timeout for {ticker}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.warning(f"FMP news: HTTP error for {ticker}: {e}")
            return None
        except Exception as e:
            logger.error(f"FMP news: unexpected error for {ticker}: {e}", exc_info=True)
            return None

    def to_processed_fundamentals(
        self,
        raw: RawFMPFundamentals,
        ticker: str,
        source_raw_id: int,
    ) -> ProcessedFundamentals:
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

    def to_processed_news(
        self,
        articles: list[RawFinnhubArticle],
        ticker: str,
        source_raw_id: int,
    ) -> list[ProcessedNewsArticle]:
        processed = []
        now = datetime.now(timezone.utc)
        for article in articles:
            published_at = None
            if article.datetime:
                try:
                    published_at = datetime.fromtimestamp(article.datetime, tz=timezone.utc)
                except Exception:
                    pass
            processed.append(ProcessedNewsArticle(
                ticker=ticker,
                headline=article.headline,
                publisher=article.source,
                published_at=published_at,
                processed_at=now,
                source_raw_id=source_raw_id,
            ))
        return processed


# ── Finnhub Client (kept for backwards compatibility but FMP is preferred) ────

class FinnhubClient:
    """
    Kept for backwards compatibility.
    FMP news endpoint is now preferred — use FMPClient.fetch_news() instead.
    Only instantiate this if FINNHUB_API_KEY is set and FMP news is unavailable.
    """

    BASE_URL = "https://finnhub.io/api/v1"
    MAX_ARTICLES_PER_TICKER = 10

    def __init__(self):
        self.api_key = os.getenv("FINNHUB_API_KEY")
        if not self.api_key:
            raise RuntimeError("FINNHUB_API_KEY environment variable is not set")
        self.session = requests.Session()
        self.session.headers.update({"X-Finnhub-Token": self.api_key})

    def fetch_news(self, ticker: str) -> Optional[tuple[list[RawFinnhubArticle], str]]:
        try:
            from datetime import timedelta
            now = datetime.now(timezone.utc)
            date_from = (now - timedelta(hours=24)).strftime("%Y-%m-%d")
            date_to = now.strftime("%Y-%m-%d")

            url = f"{self.BASE_URL}/company-news"
            resp = self.session.get(
                url,
                params={"symbol": ticker, "from": date_from, "to": date_to},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data or not isinstance(data, list):
                return [], json.dumps([])

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
                    if validated.headline:
                        validated_articles.append(validated)
                except Exception:
                    continue

            raw_json = json.dumps(data[:self.MAX_ARTICLES_PER_TICKER])
            return validated_articles, raw_json

        except Exception as e:
            logger.error(f"Finnhub: error for {ticker}: {e}", exc_info=True)
            return None

    def to_processed(
        self,
        articles: list[RawFinnhubArticle],
        ticker: str,
        source_raw_id: int,
    ) -> list[ProcessedNewsArticle]:
        processed = []
        now = datetime.now(timezone.utc)
        for article in articles:
            published_at = None
            if article.datetime:
                try:
                    published_at = datetime.fromtimestamp(article.datetime, tz=timezone.utc)
                except Exception:
                    pass
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
    time.sleep(seconds)