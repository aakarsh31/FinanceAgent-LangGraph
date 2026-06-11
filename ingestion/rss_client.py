"""
ingestion/rss_client.py — RSS feed fetcher and ticker mapper

Design decisions:
- No API key, no rate limits, no quota. Replaces FMP news entirely.
- One fetch per feed — returns all recent articles for all tickers simultaneously.
  This is the fundamental architectural improvement over per-ticker API calls.
- Ticker mapping uses company name dictionary from Postgres universe table.
  Built once per nightly run from get_ticker_name_map(). Not rebuilt per article.
- Two-pass matching:
    Pass 1 — ticker symbol match: "AAPL" appears in headline
    Pass 2 — company name match: "Apple" or "Apple Inc" appears in headline
  Pass 1 is exact. Pass 2 uses lowercase contains match.
- Max 10 articles per ticker — same cap as before.
- Articles older than 48 hours are filtered out — RSS feeds can have stale entries.
- feedparser is the only new dependency.
"""

import logging
from datetime import datetime, timedelta, timezone

import time

import feedparser

logger = logging.getLogger(__name__)

# ── RSS feed sources ──────────────────────────────────────────────────────────
# All free, no authentication required.

RSS_FEEDS = {
    "reuters_business": "https://feeds.reuters.com/reuters/businessNews",
    "wsj_markets":      "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "yahoo_finance":    "https://finance.yahoo.com/news/rssindex",
    "seeking_alpha":    "https://seekingalpha.com/feed.xml",
    "marketwatch":      "https://feeds.marketwatch.com/marketwatch/topstories/",
}

MAX_ARTICLES_PER_TICKER = 10
MAX_ARTICLE_AGE_HOURS = 48


class RSSClient:

    def __init__(self):
        self.articles: list[dict] = []    # cached after fetch_all_feeds()

    def fetch_all_feeds(self) -> int:
        """
        Fetch all RSS feeds and cache articles.
        Returns total article count.
        Call this once per nightly run before mapping to tickers.
        """
        self.articles = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_ARTICLE_AGE_HOURS)

        for feed_name, url in RSS_FEEDS.items():
            try:
                feed = feedparser.parse(url)
                if feed.bozo:
                    logger.warning(f"RSS: malformed feed from {feed_name} — {feed.bozo_exception}")

                feed_articles = 0
                for entry in feed.entries:
                    # Parse published date
                    published_at = None
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        try:
                            published_at = datetime.fromtimestamp(
                                time.mktime(entry.published_parsed),
                                tz=timezone.utc
                            )
                        except Exception:
                            pass

                    # Filter stale articles
                    if published_at and published_at < cutoff:
                        continue

                    headline = getattr(entry, "title", None)
                    summary = getattr(entry, "summary", None)
                    url_link = getattr(entry, "link", None)

                    if not headline:
                        continue

                    self.articles.append({
                        "headline": headline,
                        "summary": summary or "",
                        "url": url_link,
                        "publisher": feed_name,
                        "published_at": published_at,
                    })
                    feed_articles += 1

                logger.info(f"RSS: {feed_name} → {feed_articles} articles")

            except Exception as e:
                logger.warning(f"RSS: failed to fetch {feed_name}: {e}")
                continue

        logger.info(f"RSS: total {len(self.articles)} articles fetched across {len(RSS_FEEDS)} feeds")
        return len(self.articles)

    def map_to_tickers(
        self,
        ticker_name_map: dict[str, str],
    ) -> dict[str, list[dict]]:
        """
        Map cached articles to tickers using symbol + company name matching.

        Args:
            ticker_name_map: {ticker: company_name} from universe table

        Returns:
            {ticker: [article, ...]} — up to MAX_ARTICLES_PER_TICKER per ticker
        """
        if not self.articles:
            logger.warning("RSS: no articles to map — call fetch_all_feeds() first")
            return {}

        # Build reverse lookup: lowercase name fragment → ticker
        # "apple inc" → "AAPL", "apple" → "AAPL"
        name_to_ticker: dict[str, str] = {}
        for ticker, company_name in ticker_name_map.items():
            if not company_name:
                continue
            name_lower = company_name.lower()
            name_to_ticker[name_lower] = ticker

            # Also add shortened name (first word of company name)
            # "Apple Inc." → "apple"
            # Avoids false positives from generic words
            first_word = name_lower.split()[0] if name_lower.split() else None
            if first_word and len(first_word) > 4:  # skip short words like "The", "Inc"
                if first_word not in name_to_ticker:
                    name_to_ticker[first_word] = ticker

        # Map articles to tickers
        ticker_articles: dict[str, list[dict]] = {t: [] for t in ticker_name_map}

        for article in self.articles:
            headline_lower = (article["headline"] + " " + article["summary"]).lower()
            matched_tickers: set[str] = set()

            # Pass 1 — exact ticker symbol match (e.g. "AAPL")
            for ticker in ticker_name_map:
                if f" {ticker} " in f" {headline_lower.upper()} ":
                    matched_tickers.add(ticker)

            # Pass 2 — company name match
            for name_fragment, ticker in name_to_ticker.items():
                if ticker in matched_tickers:
                    continue  # already matched
                if name_fragment in headline_lower:
                    matched_tickers.add(ticker)

            # Add article to each matched ticker (up to cap)
            for ticker in matched_tickers:
                if ticker in ticker_articles and len(ticker_articles[ticker]) < MAX_ARTICLES_PER_TICKER:
                    ticker_articles[ticker].append(article)

        # Filter out tickers with no articles
        result = {t: articles for t, articles in ticker_articles.items() if articles}
        total_mappings = sum(len(a) for a in result.values())
        logger.info(
            f"RSS: mapped {total_mappings} article-ticker pairs "
            f"across {len(result)} tickers"
        )
        return result
