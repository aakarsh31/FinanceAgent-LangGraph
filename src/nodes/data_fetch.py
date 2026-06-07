"""
src/nodes/data_fetch.py — DataFetchAgent (Day 6 rewrite)

What changed from Day 5:
- Fundamentals: yfinance .info → Postgres processed_fundamentals (FMP-sourced)
- News headlines: yfinance .news scraper → Postgres processed_news (Finnhub-sourced)
- Macro: live FRED on every request → Postgres processed_macro (6h TTL)
- Price/OHLCV: still yfinance — reliable for this use case, no replacement needed
- Every data source now carries a DataFreshness annotation injected into state
  so downstream agents know the age and provenance of what they're reasoning over

Fallback chain (per data type):
  1. Check data_freshness_meta — is it fresh?
  2. Yes → query processed table → inject data_age annotation
  3. No / missing → live API fallback → log cache miss → inject "live fallback" annotation

The state key "data_provenance" is new — a dict of source annotations
that SupervisorAgent can surface in the final report.
"""

import logging
import os
from datetime import datetime, timezone

import requests
import yfinance as yf
from sqlalchemy import select, text, desc

from src.exceptions import DataFetchRateLimitError, EmptyDataError, TickerNotFoundError
from src.states.financestate import AnalystConsensus, FinanceState
from ingestion.models import (
    DataFreshness,
    FUNDAMENTALS_TTL_HOURS,
    NEWS_TTL_HOURS,
    MACRO_TTL_HOURS,
)

logger = logging.getLogger(__name__)

COINGECKO_IDS: dict[str, str] = {
    "BTC-USD": "bitcoin",
    "ETH-USD": "ethereum",
    "SOL-USD": "solana",
    "BNB-USD": "binancecoin",
    "XRP-USD": "ripple",
    "ADA-USD": "cardano",
    "DOGE-USD": "dogecoin",
    "AVAX-USD": "avalanche-2",
    "MATIC-USD": "matic-network",
    "DOT-USD": "polkadot",
    "LINK-USD": "chainlink",
    "LTC-USD": "litecoin",
    "UNI-USD": "uniswap",
    "ATOM-USD": "cosmos",
    "TRX-USD": "tron",
}

FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "cpi": "CPIAUCSL",
    "yield_10y": "GS10",
    "yield_2y": "GS2",
    "unemployment": "UNRATE",
}


class DataFetchAgent:

    def __init__(self, engine=None):
        """
        engine: SQLAlchemy Engine injected from app lifespan.
        If None (e.g. tests, cold start), falls back to live API for everything.
        """
        self.engine = engine

    def fetch(self, state: FinanceState, **kwargs):
        ticker = state["ticker"]
        timeframe = state["timeframe"]

        self._validate_inputs(ticker, timeframe)
        logger.info(f"DataFetchAgent starting — ticker={ticker} timeframe={timeframe}")

        # Price + OHLCV + asset class detection — still yfinance (reliable for this)
        info, history = self._fetch_yfinance_price(ticker, timeframe)
        asset_class = self._detect_asset_class(ticker, info)
        logger.info(f"Detected asset_class='{asset_class}' for {ticker}")

        analyst_consensus = self._build_analyst_consensus(ticker, info, asset_class)

        # Fundamentals — Postgres first, live fallback
        fundamentals_info, fund_freshness = self._fetch_fundamentals(ticker, info)

        # News headlines — Postgres first, live fallback
        headlines, news_freshness = self._fetch_headlines(ticker, info)

        # Macro — Postgres first, live fallback
        fred_data, macro_freshness = self._fetch_macro()

        raw_data: dict = {
            "info": fundamentals_info,
            "history": history,
        }

        if asset_class == "crypto":
            raw_data["coingecko"] = self._fetch_coingecko(ticker)

            # Fetch crypto market structure signals
            try:
                from ingestion.crypto_signals import (
                    fetch_fear_greed, fetch_btc_dominance, build_crypto_signals
                )
                fear_greed  = fetch_fear_greed()
                btc_dom     = fetch_btc_dominance()
                signals     = build_crypto_signals(ticker, fear_greed, btc_dom)
                # Store as plain dict — LangGraph checkpoint serializer
                # doesn't support custom dataclasses
                raw_data["crypto_signals"] = {
                    "prompt_context": signals.to_prompt_context(),
                    "fear_greed_value": signals.fear_greed_value,
                    "fear_greed_label": signals.fear_greed_label,
                    "btc_dominance_pct": signals.btc_dominance_pct,
                    "price_change_7d": signals.price_change_7d,
                    "price_change_30d": signals.price_change_30d,
                    "ath_change_pct": signals.ath_change_pct,
                    "commits_4w": signals.developer.commits_4w if signals.developer else None,
                    "github_momentum_pct": signals.developer.github_momentum_pct if signals.developer else None,
                }
                logger.info(
                    f"CryptoSignals: fear_greed={fear_greed[0]} "
                    f"btc_dominance={btc_dom} "
                    f"gh_momentum={signals.developer.github_momentum_pct if signals.developer else None}"
                )
            except Exception as e:
                logger.warning(f"crypto_signals fetch failed (non-fatal): {e}")
                raw_data["crypto_signals"] = None

        raw_data["fred"] = fred_data

        # data_provenance — injected into state for SupervisorAgent transparency
        data_provenance = {
            "fundamentals": fund_freshness.prompt_annotation(),
            "news": news_freshness.prompt_annotation(),
            "macro": macro_freshness.prompt_annotation(),
        }

        logger.info(
            f"DataFetchAgent complete — asset_class={asset_class} "
            f"headlines={len(headlines)} "
            f"fund_source={fund_freshness.status} "
            f"news_source={news_freshness.status} "
            f"macro_source={macro_freshness.status}"
        )

        return {
            "asset_class": asset_class,
            "raw_data": raw_data,
            "news_headlines": headlines,
            "analyst_consensus": analyst_consensus.model_dump() if analyst_consensus else None,
            "data_provenance": data_provenance,
        }

    # ── Input validation ──────────────────────────────────────────────────────

    def _validate_inputs(self, ticker: str, timeframe: str):
        if not ticker or not isinstance(ticker, str):
            raise TickerNotFoundError(f"Invalid or empty ticker: '{ticker}'")
        if not timeframe or not isinstance(timeframe, str):
            raise TickerNotFoundError(f"Invalid or empty timeframe: '{timeframe}'")

    # ── Price fetch (yfinance — retained) ─────────────────────────────────────

    def _fetch_yfinance_price(self, ticker: str, timeframe: str) -> tuple[dict, dict]:
        """
        Fetches price history and info from yfinance.
        info is used only for: asset class detection, analyst consensus,
        volatility/beta computation. NOT for fundamentals or news anymore.
        """
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            raw_history = stock.history(period=timeframe).to_dict()
            history = {
                col: {str(ts): val for ts, val in rows.items()}
                for col, rows in raw_history.items()
            }
        except Exception as e:
            logger.error(f"yfinance price fetch failed for {ticker}: {e}", exc_info=True)
            raise DataFetchRateLimitError(f"Failed to fetch price data for {ticker}: {e}")

        if not info or info.get("regularMarketPrice") is None:
            raise EmptyDataError(f"Ticker '{ticker}' not found or delisted")
        if not history:
            raise EmptyDataError(f"No price history for '{ticker}' over timeframe '{timeframe}'")

        return info, history

    # ── Freshness check ───────────────────────────────────────────────────────

    def _check_freshness(self, ticker: str, data_type: str, ttl_hours: float) -> DataFreshness:
        """
        Query data_freshness_meta for (ticker, data_type).
        Returns DataFreshness with is_fresh=True/False and age metadata.
        Returns status='missing' if no engine or no row found.
        """
        if not self.engine:
            return DataFreshness(
                ticker=ticker,
                data_type=data_type,
                is_fresh=False,
                status="missing",
            )

        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT last_updated, status, source
                    FROM data_freshness_meta
                    WHERE ticker = :ticker AND data_type = :data_type
                """), {"ticker": ticker, "data_type": data_type})

                row = result.fetchone()
                if not row:
                    return DataFreshness(
                        ticker=ticker,
                        data_type=data_type,
                        is_fresh=False,
                        status="missing",
                    )

                last_updated = row.last_updated
                if last_updated.tzinfo is None:
                    last_updated = last_updated.replace(tzinfo=timezone.utc)

                age_hours = (datetime.now(timezone.utc) - last_updated).total_seconds() / 3600
                is_fresh = age_hours <= ttl_hours and row.status == "fresh"

                return DataFreshness(
                    ticker=ticker,
                    data_type=data_type,
                    is_fresh=is_fresh,
                    last_updated=last_updated,
                    data_age_hours=round(age_hours, 2),
                    source=row.source,
                    status="fresh" if is_fresh else "stale",
                )

        except Exception as e:
            logger.warning(f"Freshness check failed for ({ticker}, {data_type}): {e}")
            return DataFreshness(
                ticker=ticker,
                data_type=data_type,
                is_fresh=False,
                status="missing",
            )

    def _log_cache_miss(self, ticker: str, data_type: str):
        """Log a cache miss for ticker promotion."""
        if not self.engine:
            return
        try:
            with self.engine.connect() as conn:
                conn.execute(text("""
                    INSERT INTO cache_miss_log (ticker, data_type, requested_at, resolved_via)
                    VALUES (:ticker, :data_type, :requested_at, 'live_api')
                """), {
                    "ticker": ticker,
                    "data_type": data_type,
                    "requested_at": datetime.now(timezone.utc),
                })
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to log cache miss for {ticker}/{data_type}: {e}")

    # ── Fundamentals fetch ────────────────────────────────────────────────────

    def _fetch_fundamentals(self, ticker: str, yf_info: dict) -> tuple[dict, DataFreshness]:
        """
        Postgres first. Falls back to yfinance .info if stale/missing.
        Returns (info_dict_for_raw_data, DataFreshness).
        """
        freshness = self._check_freshness(ticker, "fundamentals", FUNDAMENTALS_TTL_HOURS)

        if freshness.is_fresh and self.engine:
            try:
                with self.engine.connect() as conn:
                    result = conn.execute(text("""
                        SELECT pe_ratio, eps, revenue_growth, debt_to_equity,
                               market_cap, sector
                        FROM processed_fundamentals
                        WHERE ticker = :ticker
                        ORDER BY processed_at DESC
                        LIMIT 1
                    """), {"ticker": ticker})
                    row = result.fetchone()

                if row:
                    logger.info(f"Fundamentals: cache hit for {ticker} ({freshness.data_age_hours}h old)")
                    # Build info dict matching shape FundamentalsAgent expects
                    info_dict = dict(yf_info)  # keep price/quoteType fields
                    info_dict.update({
                        "trailingPE": row.pe_ratio,
                        "trailingEps": row.eps,
                        "revenueGrowth": row.revenue_growth,
                        "debtToEquity": row.debt_to_equity,
                    })
                    return info_dict, freshness

            except Exception as e:
                logger.warning(f"Fundamentals: Postgres query failed for {ticker}: {e} — falling back to yfinance")

        # Fallback to yfinance .info (already fetched for price)
        logger.info(f"Fundamentals: cache miss for {ticker} — using yfinance fallback")
        self._log_cache_miss(ticker, "fundamentals")
        fallback_freshness = DataFreshness(
            ticker=ticker,
            data_type="fundamentals",
            is_fresh=False,
            status="missing",
        )
        return yf_info, fallback_freshness

    # ── Headlines fetch ───────────────────────────────────────────────────────

    def _fetch_headlines(self, ticker: str, yf_info: dict) -> tuple[list[str], DataFreshness]:
        """
        Postgres first (Finnhub-sourced). Falls back to yfinance .news scraper.
        """
        freshness = self._check_freshness(ticker, "news", NEWS_TTL_HOURS)

        if freshness.is_fresh and self.engine:
            try:
                with self.engine.connect() as conn:
                    result = conn.execute(text("""
                        SELECT headline FROM processed_news
                        WHERE ticker = :ticker
                        ORDER BY published_at DESC NULLS LAST
                        LIMIT 10
                    """), {"ticker": ticker})
                    rows = result.fetchall()

                if rows:
                    headlines = [row.headline for row in rows]
                    logger.info(f"News: cache hit for {ticker} — {len(headlines)} headlines ({freshness.data_age_hours}h old)")
                    return headlines, freshness

            except Exception as e:
                logger.warning(f"News: Postgres query failed for {ticker}: {e} — falling back to yfinance")

        # Fallback to yfinance news scraper
        logger.info(f"News: cache miss for {ticker} — using yfinance fallback")
        self._log_cache_miss(ticker, "news")

        try:
            stock = yf.Ticker(ticker)
            news = stock.news
            headlines = [
                article["content"]["title"]
                for article in news
                if article.get("content") and article["content"].get("title")
            ]
        except Exception as e:
            logger.warning(f"News: yfinance fallback failed for {ticker}: {e}")
            headlines = []

        fallback_freshness = DataFreshness(
            ticker=ticker,
            data_type="news",
            is_fresh=False,
            status="missing",
        )
        return headlines, fallback_freshness

    # ── Macro fetch ───────────────────────────────────────────────────────────

    def _fetch_macro(self) -> tuple[dict | None, DataFreshness]:
        """
        Postgres first. Falls back to live FRED if stale/missing.
        Macro uses "macro" as both ticker and data_type in freshness meta.
        """
        freshness = self._check_freshness("macro", "macro", MACRO_TTL_HOURS)

        if freshness.is_fresh and self.engine:
            try:
                with self.engine.connect() as conn:
                    result = conn.execute(text("""
                        SELECT DISTINCT ON (indicator) indicator, value, period
                        FROM processed_macro
                        ORDER BY indicator, processed_at DESC
                    """))
                    rows = result.fetchall()

                if rows:
                    macro_map = {row.indicator: row.value for row in rows}

                    # Reconstruct fred_data dict matching existing agent expectations
                    cpi_raw = macro_map.get("cpi_yoy")
                    fred_data = {
                        "fed_funds_rate": macro_map.get("fed_funds_rate"),
                        "cpi_yoy": cpi_raw,
                        "yield_10y": macro_map.get("yield_10y"),
                        "yield_2y": macro_map.get("yield_2y"),
                        "yield_curve_spread": (
                            round(macro_map["yield_10y"] - macro_map["yield_2y"], 4)
                            if macro_map.get("yield_10y") and macro_map.get("yield_2y")
                            else None
                        ),
                        "unemployment_rate": macro_map.get("unemployment"),
                    }
                    logger.info(f"Macro: cache hit ({freshness.data_age_hours}h old)")
                    return fred_data, freshness

            except Exception as e:
                logger.warning(f"Macro: Postgres query failed: {e} — falling back to live FRED")

        # Fallback to live FRED
        logger.info("Macro: cache miss — fetching live from FRED")
        self._log_cache_miss("macro", "macro")
        fred_data = self._fetch_fred_live()
        fallback_freshness = DataFreshness(
            ticker="macro",
            data_type="macro",
            is_fresh=False,
            status="missing",
        )
        return fred_data, fallback_freshness

    def _fetch_fred_live(self) -> dict | None:
        """Live FRED fetch — identical to Day 5 implementation."""
        try:
            from fredapi import Fred
            api_key = os.getenv("FRED_API_KEY")
            if not api_key:
                logger.warning("FRED_API_KEY not set — skipping FRED fetch")
                return None
            fred = Fred(api_key=api_key)

            def latest(series_id: str) -> float | None:
                try:
                    s = fred.get_series(series_id)
                    return round(float(s.dropna().iloc[-1]), 4)
                except Exception as e:
                    logger.warning(f"FRED series '{series_id}' failed: {e}")
                    return None

            fed_funds = latest(FRED_SERIES["fed_funds_rate"])
            cpi_series = fred.get_series(FRED_SERIES["cpi"]).dropna()
            yield_10y = latest(FRED_SERIES["yield_10y"])
            yield_2y = latest(FRED_SERIES["yield_2y"])
            unemployment = latest(FRED_SERIES["unemployment"])

            cpi_yoy = None
            if len(cpi_series) >= 13:
                cpi_now = float(cpi_series.iloc[-1])
                cpi_year = float(cpi_series.iloc[-13])
                cpi_yoy = round((cpi_now - cpi_year) / cpi_year * 100, 2)

            yield_curve_spread = None
            if yield_10y is not None and yield_2y is not None:
                yield_curve_spread = round(yield_10y - yield_2y, 4)

            fred_data = {
                "fed_funds_rate": fed_funds,
                "cpi_yoy": cpi_yoy,
                "yield_10y": yield_10y,
                "yield_2y": yield_2y,
                "yield_curve_spread": yield_curve_spread,
                "unemployment_rate": unemployment,
            }
            logger.info(f"FRED live fetch successful: {fed_funds=} {cpi_yoy=}")
            return fred_data
        except Exception as e:
            logger.warning(f"FRED live fetch failed: {e} — macro data unavailable")
            return None

    # ── Asset class detection ─────────────────────────────────────────────────

    def _detect_asset_class(self, ticker: str, info: dict) -> str:
        quote_type = info.get("quoteType", "").upper()
        if quote_type == "CRYPTOCURRENCY":
            return "crypto"
        elif quote_type in ["EQUITY", "ETF"]:
            return "equity"
        else:
            raise TickerNotFoundError(
                f"Unsupported instrument type '{quote_type}' for ticker '{ticker}'. "
                f"Supported types: equities, ETFs, cryptocurrencies."
            )

    # ── Analyst consensus ─────────────────────────────────────────────────────

    def _build_analyst_consensus(self, ticker: str, info: dict, asset_class: str) -> AnalystConsensus | None:
        if asset_class == "crypto":
            return None
        rec = info.get("recommendationKey", "").lower()
        normalized = (
            "Buy" if rec in ["buy", "strongbuy", "strong_buy"] else
            "Sell" if rec in ["sell", "strongsell", "strong_sell"] else
            "Hold" if rec in ["hold", "underperform", "neutral"] else
            "unavailable"
        )
        consensus = AnalystConsensus(
            recommendation=normalized,
            target_price=info.get("targetMeanPrice"),
            num_analysts=info.get("numberOfAnalystOpinions"),
        )
        logger.info(f"Analyst consensus for {ticker}: {consensus}")
        return consensus

    # ── CoinGecko (unchanged) ─────────────────────────────────────────────────

    def _fetch_coingecko(self, ticker: str) -> dict | None:
        coin_id = COINGECKO_IDS.get(ticker.upper())
        if not coin_id:
            logger.warning(f"CoinGecko: no coin_id mapping for '{ticker}' — skipping")
            return None
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        params = {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "true",
            "developer_data": "true",
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            market = data.get("market_data", {})
            coingecko = {
                "market_cap_usd": market.get("market_cap", {}).get("usd"),
                "volume_24h_usd": market.get("total_volume", {}).get("usd"),
                "price_change_7d": market.get("price_change_percentage_7d"),
                "developer_activity_score": data.get("developer_score"),
                "community_score": data.get("community_score"),
            }
            logger.info(f"CoinGecko fetch successful for {ticker} ({coin_id})")
            return coingecko
        except requests.exceptions.RequestException as e:
            logger.warning(f"CoinGecko fetch failed for {ticker}: {e} — continuing without it")
            return None