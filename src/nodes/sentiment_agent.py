import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, SentimentData
from src.exceptions import LLMStructuredOutputError

logger = logging.getLogger(__name__)

SENTIMENT_REFERENCE = """
SCORE CALIBRATION:
+0.7 to 1.0: Euphoria — massive beat, guidance raise, major catalyst
+0.3 to 0.7: Bullish — positive earnings, upgrades, favorable news
-0.3 to 0.3: Neutral — mixed signals, generic commentary
-0.3 to -0.7: Bearish — misses, downgrades, regulatory concern
-0.7 to -1.0: Fear — scandal, massive miss, sector crisis

HEADLINE WEIGHT (highest to lowest signal):
1. Earnings beat/miss vs estimates
2. Management guidance changes (forward-looking)
3. Analyst upgrades/downgrades with price target changes
4. Regulatory or legal developments
5. Insider buying/selling (discretionary purchases most meaningful)
6. General sector commentary (lowest — often noise)
"""

SENTIMENT_PROMPT_EQUITY = """You are the Sentiment Analyst at an elite investment research firm.
Your job is to extract the market's TRUE emotional state from headlines — not what should be true, but what the market BELIEVES right now.
Sentiment drives short-term price action even when it conflicts with fundamentals.

{sentiment_reference}
SCORING FRAMEWORK:
- Score range: -1.0 (maximum bearish) to +1.0 (maximum bullish)
- +0.7 to +1.0: Euphoria / strong momentum — analysts upgrading, earnings beats, major catalysts
- +0.3 to +0.7: Bullish — positive news flow, upgrades, optimism
- -0.3 to +0.3: Neutral — mixed signals, no clear directional bias
- -0.3 to -0.7: Bearish — negative news flow, downgrades, concern
- -0.7 to -1.0: Fear / capitulation — major miss, scandal, sector crisis, analyst downgrades

HEADLINE INTERPRETATION RULES:
1. Weight recent headlines MORE than older ones
2. Earnings beats/misses > analyst upgrades/downgrades > general sector news
3. Management guidance changes are HIGH signal — forward guidance matters more than backward results
4. Regulatory or legal headlines are HIGH signal negative
5. If headlines are generic market commentary with no ticker-specific content, score neutral (0.0)
6. Multiple bearish headlines compound — don't average them to neutral

FEW-SHOT EXAMPLES:
Headlines: ["AAPL beats Q4 EPS by 15%, raises guidance", "iPhone demand surges in China"]
→ score: 0.75, label: bullish, reasoning: Strong earnings beat with raised guidance signals management confidence. China demand recovery removes key bear thesis.

Headlines: ["META faces $1.2B EU fine", "Zuckerberg sells $500M in shares", "Analysts cut price targets"]
→ score: -0.65, label: bearish, reasoning: Regulatory fine creates earnings headwind. Insider selling at this scale signals lack of confidence. Analyst downgrades confirm deteriorating outlook.

Headlines: ["Markets mixed ahead of Fed decision", "Sector rotation into defensives continues"]
→ score: -0.1, label: neutral, reasoning: Generic macro commentary without ticker-specific signal. Defensive rotation is mildly negative for growth assets.

--- HEADLINES FOR {ticker} ---
{headlines}

Your output:
- sentiment_score: float from -1.0 to 1.0 (do not round to 0.0 unless truly neutral)
- sentiment_label: exactly 'bullish', 'bearish', or 'neutral'
- sentiment_reasoning: 2-3 sentences. Name the 1-2 most influential headlines and explain specifically why they moved the score in that direction.
"""

SENTIMENT_PROMPT_CRYPTO = """You are the Sentiment Analyst at an elite investment research firm specializing in digital assets.
Crypto sentiment is more volatile and self-reinforcing than equity sentiment. Fear and greed move prices dramatically.
Your job is to assess the TRUE market emotional state — not what should happen, but what the market believes NOW.

SCORING FRAMEWORK:
- Score range: -1.0 (maximum bearish) to +1.0 (maximum bullish)
- +0.7 to +1.0: Euphoria — mainstream adoption news, ETF inflows, institutional buying, protocol milestones
- +0.3 to +0.7: Bullish — positive regulatory news, network upgrades, developer activity acceleration
- -0.3 to +0.3: Neutral — mixed signals, consolidation, no directional catalyst
- -0.3 to -0.7: Bearish — regulatory crackdowns, exchange issues, hack/exploit news, whale selling
- -0.7 to -1.0: Fear/capitulation — exchange collapses, major regulatory bans, security breaches, market-wide deleveraging

CRYPTO-SPECIFIC SIGNAL WEIGHTS:
- Score range: -1.0 (maximum bearish) to +1.0 (maximum bullish)
- +0.7 to +1.0: Euphoria — mainstream adoption news, ETF inflows, institutional buying, protocol milestones
- +0.3 to +0.7: Bullish — positive regulatory news, network upgrades, developer activity acceleration
- -0.3 to +0.3: Neutral — mixed signals, consolidation, no directional catalyst
- -0.3 to -0.7: Bearish — regulatory crackdowns, exchange issues, hack/exploit news, whale selling
- -0.7 to -1.0: Fear/capitulation — exchange collapses, major regulatory bans, security breaches, market-wide deleveraging

CRYPTO-SPECIFIC SIGNAL WEIGHTS:
- Fear & Greed Index:
  * 0-25 (Extreme Fear): High probability of panic selling, potential capitulation bottom
  * 25-45 (Fear): Bearish sentiment, buyers cautious
  * 45-55 (Neutral): No strong directional signal
  * 55-75 (Greed): Momentum positive, FOMO building
  * 75-100 (Extreme Greed): Overheated, correction risk elevated
- Developer Activity: High activity (commits_4w >> avg) signals protocol health, often precedes price strength
- 7-Day Price Change: >+20% often signals overbought, <-20% often signals panic
- Community Score: Declining trend is early warning of ecosystem health issues

--- HEADLINES FOR {ticker} ---
{headlines}

--- MARKET STRUCTURE SIGNALS ---
Developer Activity Score (0-100): {developer_score}
Community Score (0-100): {community_score}
7-Day Price Change: {price_change_7d}%
Fear & Greed Index: {fear_greed}

Your output:
- sentiment_score: float from -1.0 to 1.0. Fear & Greed below 25 should pull score below -0.4 unless headlines strongly contradict.
- sentiment_label: exactly 'bullish', 'bearish', or 'neutral'
- sentiment_reasoning: 2-3 sentences. Integrate headlines AND market structure signals — both matter for crypto. Explicitly reference Fear & Greed if below 30 or above 70.
"""


class SentimentAgent:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(SentimentData)

    def analyze(self, state: FinanceState, **kwargs) -> dict:
        ticker = state["ticker"]
        asset_class = state.get("asset_class", "equity")
        headlines = state.get("news_headlines", [])
        logger.info(f"SentimentAgent starting for {ticker} (asset_class={asset_class})...")

        headlines_text = "\n- " + "\n- ".join(headlines[:10]) if headlines else "No headlines available for this ticker."

        def fmt(val) -> str:
            return str(val) if val is not None else "unavailable"

        if asset_class == "crypto":
            coingecko = state["raw_data"].get("coingecko") or {}
            crypto_signals = state["raw_data"].get("crypto_signals") or {}
            fear_greed = crypto_signals.get("fear_greed_value")
            fear_greed_label = crypto_signals.get("fear_greed_label")
            fg_str = f"{fear_greed}/100 ({fear_greed_label})" if fear_greed is not None else "unavailable"

            prompt = SENTIMENT_PROMPT_CRYPTO.format(
                ticker=ticker,
                headlines=headlines_text,
                developer_score=fmt(coingecko.get("developer_activity_score")),
                community_score=fmt(coingecko.get("community_score")),
                price_change_7d=fmt(coingecko.get("price_change_7d")),
                fear_greed=fg_str,
            )
        else:
            prompt = SENTIMENT_PROMPT_EQUITY.format(
                ticker=ticker,
                headlines=headlines_text,
                sentiment_reference=SENTIMENT_REFERENCE,
            )

        try:
            result: SentimentData = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"SentimentAgent LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"SentimentAgent failed: {e}")

        logger.info(f"SentimentAgent complete for {ticker} — label={result.sentiment_label} score={result.sentiment_score}")
        return {"sentiment": result.model_dump()}