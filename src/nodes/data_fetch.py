from src.states.financestate import FinanceState
import yfinance as yf

from src.exceptions import FinanceAgentError,EmptyDataError,TickerNotFoundError,DataFetchRateLimitError
import logging

logger = logging.getLogger(__name__)


class DataFetchAgent:

    def fetch(self,state:FinanceState):
        #Read inputs from state
        ticker = state["ticker"]
        if not ticker or not isinstance(ticker,str):
            logger.error(f"Invalid Ticker input : {ticker}")
            raise TickerNotFoundError(f"Invalid or empty ticker: '{ticker}'")
        
        timeframe = state["timeframe"]
        if not timeframe or not isinstance(timeframe,str):
            logger.error(f"Invalid timeframe input : {timeframe}")
            raise TickerNotFoundError(f"Invalid or empty timeframe: '{timeframe}'")

        logger.info(f"Fetching Ticker Data {ticker} for given Timeframe {timeframe}...")

        #yfinance tracker object
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            # Normalize yfinance recommendation to match our pipeline output
            rec = info.get("recommendationKey", "").lower()
            normalized = (
                "Buy" if rec in ["buy", "strongbuy", "strong_buy"] else
                "Sell" if rec in ["sell", "strongsell", "strong_sell"] else
                "Hold" if rec in ["hold", "underperform", "neutral"] else
                "unavailable"
            )

            analyst_consensus = {
            "recommendation": normalized,
            "target_price": info.get("targetMeanPrice"),
            "num_analysts": info.get("numberOfAnalystOpinions")
            }

            logger.info(f"Analyst consensus for {ticker}: {analyst_consensus}")

            if not analyst_consensus.get("recommendation"):
                logger.warning(f"No analyst consensus available for {ticker}")

            raw_history = stock.history(period=timeframe).to_dict()
            # Checkpointer requires string keys — pandas Timestamps must be converted
            history = {
                col: {str(ts): val for ts, val in rows.items()}
                for col, rows in raw_history.items()
            }
            news = stock.news
        except Exception as e:
            logger.error(f"Failed to fetch data for {ticker}: {e}", exc_info=True)
            raise DataFetchRateLimitError(f"Failed to fetch data for {ticker}: {e}")

        if not info or info.get("regularMarketPrice") is None:
            logger.warning(f"Empty or invalid info returned for {ticker}")
            raise EmptyDataError(f"Ticker '{ticker}' not found or delisted")

        if not history:
            logger.warning(f"No price history returned for {ticker}")
            raise EmptyDataError(f"No price history for '{ticker}' over timeframe '{timeframe}'")
        
        
        
        #Extract headlines as a list of strings
        # with each new item as a dict with key as "Title"
        headlines = [article['content']['title'] for article in news if article.get("content") and article["content"].get("title")]

        logger.info(f"Successfully fetched data for {ticker} — {len(headlines)} headlines retrieved")

        #return state fields
        return{
            "raw_data":{
                "info":info,
                "history":history,
            },
            "news_headlines":headlines,
            'analyst_consensus':analyst_consensus
        }