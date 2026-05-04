import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, SentimentData
from src.exceptions import LLMStructuredOutputError

logger = logging.getLogger(__name__)

SENTIMENT_PROMPT_EQUITY = """You are the Sentiment Analyst at an investment research firm.
Analyze the market sentiment for {ticker} based on recent news headlines.

--- RECENT HEADLINES ---
{headlines}

Return:
- sentiment_score: a float from -1.0 (very bearish) to 1.0 (very bullish)
- sentiment_label: exactly one of 'bullish', 'bearish', or 'neutral'
- sentiment_reasoning: a concise 2-3 sentence explanation of what the headlines signal
"""

SENTIMENT_PROMPT_CRYPTO = """You are the Sentiment Analyst at an investment research firm.
Analyze the market sentiment for {ticker} using news headlines and CoinGecko signals.

--- RECENT HEADLINES ---
{headlines}

--- COINGECKO SIGNALS ---
Developer Activity Score (0-100): {developer_score}
Community Score (0-100): {community_score}
7-Day Price Change: {price_change_7d}%

Return:
- sentiment_score: a float from -1.0 (very bearish) to 1.0 (very bullish)
- sentiment_label: exactly one of 'bullish', 'bearish', or 'neutral'
- sentiment_reasoning: 2-3 sentences covering both headlines and CoinGecko signals
"""


class SentimentAgent:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(SentimentData)

    def analyze(self, state: FinanceState) -> dict:
        ticker = state["ticker"]
        asset_class = state.get("asset_class", "equity")
        headlines = state.get("news_headlines", [])
        logger.info(f"SentimentAgent starting for {ticker} (asset_class={asset_class})...")

        headlines_text = "\n- ".join(headlines[:10]) if headlines else "No headlines available"

        def fmt(val) -> str:
            return str(val) if val is not None else "unavailable"

        if asset_class == "crypto":
            coingecko = state["raw_data"].get("coingecko") or {}
            prompt = SENTIMENT_PROMPT_CRYPTO.format(
                ticker=ticker,
                headlines=headlines_text,
                developer_score=fmt(coingecko.get("developer_activity_score")),
                community_score=fmt(coingecko.get("community_score")),
                price_change_7d=fmt(coingecko.get("price_change_7d")),
            )
        else:
            prompt = SENTIMENT_PROMPT_EQUITY.format(
                ticker=ticker,
                headlines=headlines_text,
            )

        try:
            result: SentimentData = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"SentimentAgent LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"SentimentAgent failed to produce structured output: {e}")

        logger.info(f"SentimentAgent complete for {ticker} — label={result.sentiment_label} score={result.sentiment_score}")
        return {"sentiment": result.model_dump()}