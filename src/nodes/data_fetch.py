import logging
import os
import requests
import yfinance as yf

from src.exceptions import DataFetchRateLimitError, EmptyDataError, TickerNotFoundError
from src.states.financestate import AnalystConsensus, FinanceState

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

    def fetch(self, state: FinanceState):
        ticker = state["ticker"]
        timeframe = state["timeframe"]

        self._validate_inputs(ticker, timeframe)
        logger.info(f"DataFetchAgent starting — ticker={ticker} timeframe={timeframe}")

        info, history, headlines = self._fetch_yfinance(ticker, timeframe)
        asset_class = self._detect_asset_class(ticker, info)
        logger.info(f"Detected asset_class='{asset_class}' for {ticker}")

        analyst_consensus = self._build_analyst_consensus(ticker, info, asset_class)
        raw_data: dict = {"info": info, "history": history}

        if asset_class == "crypto":
            raw_data["coingecko"] = self._fetch_coingecko(ticker)

        raw_data["fred"] = self._fetch_fred()

        logger.info(f"DataFetchAgent complete — asset_class={asset_class} headlines={len(headlines)} fred_ok={raw_data['fred'] is not None} coingecko_ok={raw_data.get('coingecko') is not None}")

        return {
            "asset_class": asset_class,
            "raw_data": raw_data,
            "news_headlines": headlines,
            "analyst_consensus": analyst_consensus.model_dump() if analyst_consensus else None,
        }

    def _validate_inputs(self, ticker: str, timeframe: str):
        if not ticker or not isinstance(ticker, str):
            raise TickerNotFoundError(f"Invalid or empty ticker: '{ticker}'")
        if not timeframe or not isinstance(timeframe, str):
            raise TickerNotFoundError(f"Invalid or empty timeframe: '{timeframe}'")

    def _fetch_yfinance(self, ticker: str, timeframe: str) -> tuple[dict, dict, list[str]]:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            raw_history = stock.history(period=timeframe).to_dict()
            history = {
                col: {str(ts): val for ts, val in rows.items()}
                for col, rows in raw_history.items()
            }
            news = stock.news
            headlines = [
                article["content"]["title"]
                for article in news
                if article.get("content") and article["content"].get("title")
            ]
        except Exception as e:
            logger.error(f"yfinance fetch failed for {ticker}: {e}", exc_info=True)
            raise DataFetchRateLimitError(f"Failed to fetch data for {ticker}: {e}")

        if not info or info.get("regularMarketPrice") is None:
            raise EmptyDataError(f"Ticker '{ticker}' not found or delisted")
        if not history:
            raise EmptyDataError(f"No price history for '{ticker}' over timeframe '{timeframe}'")

        return info, history, headlines

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

    def _fetch_fred(self) -> dict | None:
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
            logger.info(f"FRED fetch successful: {fred_data}")
            return fred_data
        except Exception as e:
            logger.warning(f"FRED fetch failed entirely: {e} — macro data will be unavailable")
            return None