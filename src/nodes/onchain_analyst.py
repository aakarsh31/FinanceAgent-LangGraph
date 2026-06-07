import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, OnChainData
from src.exceptions import LLMStructuredOutputError


logger = logging.getLogger(__name__)

class _D(dict):
    def __getattr__(self, k): return self.get(k)
    def __bool__(self): return len(self) > 0

def _w(v): return _D(v) if isinstance(v, dict) else v

ONCHAIN_PROMPT = """You are the On-Chain Analyst at an investment research firm.
Assess the market health and community strength of {ticker} using CoinGecko data.
Base your assessment only on the data provided — do not invent metrics.

--- MACRO CONTEXT ---
Regime: {regime_label}
Summary: {regime_summary}

--- MARKET DATA (CoinGecko) ---
Market Cap (USD): {market_cap}
24h Trading Volume (USD): {volume_24h}
7-Day Price Change: {price_change_7d}%

--- COMMUNITY & DEVELOPMENT ---
Developer Activity Score (0-100): {developer_score}
Community Score (0-100): {community_score}

--- MARKET STRUCTURE SIGNALS ---
{crypto_signals_context}

--- RECENT HEADLINES ---
{headlines}

Scoring guidance:
- Developer Activity > 70: active development
- Developer Activity 40-70: moderate, project maintained
- Developer Activity < 40: low activity, stagnation risk
- Community Score > 70: strong engagement, healthy network effects
- Volume/MCap > 10%: high liquidity, < 2%: thin market

Your output:
- network_health: Exactly one of 'Strong', 'Moderate', or 'Weak'
- onchain_summary: 2-3 sentences on development momentum, community strength, and price action in context of macro regime
"""


class OnChainAnalyst:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(OnChainData)


    def analyze(self, state: FinanceState, **kwargs) -> dict:
        ticker = state["ticker"]
        logger.info(f"OnChainAnalyst starting for {ticker}...")

        coingecko = state["raw_data"].get("coingecko") or {}
        macro = _w(state.get("macro"))
        headlines = state.get("news_headlines", [])

        if not coingecko:
            logger.warning(f"No CoinGecko data for {ticker} — OnChainData fields will be None")

        def fmt(val, prefix="", suffix="") -> str:
            return f"{prefix}{val}{suffix}" if val is not None else "unavailable"

        market_cap = coingecko.get("market_cap_usd")
        volume_24h = coingecko.get("volume_24h_usd")
        price_change = coingecko.get("price_change_7d")
        dev_score = coingecko.get("developer_activity_score")
        comm_score = coingecko.get("community_score")

        # Get crypto signals context for prompt injection
        # Stored as plain dict to avoid LangGraph checkpoint serialization issues
        crypto_signals = state["raw_data"].get("crypto_signals") or {}
        crypto_signals_context = (
            crypto_signals.get("prompt_context")
            or "No market structure signals available."
        )

        prompt = ONCHAIN_PROMPT.format(
            ticker=ticker,
            regime_label=fmt(macro.regime_label if macro else None),
            regime_summary=fmt(macro.regime_summary if macro else None),
            market_cap=fmt(market_cap, prefix="$"),
            volume_24h=fmt(volume_24h, prefix="$"),
            price_change_7d=fmt(price_change),
            developer_score=fmt(dev_score),
            community_score=fmt(comm_score),
            crypto_signals_context=crypto_signals_context,
            headlines="\n".join(f"- {h}" for h in headlines[:10]) or "No headlines available",
        )

        try:
            result: OnChainData = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"OnChainAnalyst LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"OnChainAnalyst failed to produce structured output: {e}")

        result.market_cap_usd = market_cap
        result.volume_24h_usd = volume_24h
        result.price_change_7d = price_change
        result.developer_activity_score = dev_score
        result.community_score = comm_score

        # Inject crypto signals into onchain state so frontend can display them
        crypto_signals = state["raw_data"].get("crypto_signals") or {}
        result.fear_greed_score = crypto_signals.get("fear_greed_value")
        result.fear_greed_label = crypto_signals.get("fear_greed_label")
        result.btc_dominance_pct = crypto_signals.get("btc_dominance_pct")
        result.github_momentum_pct = crypto_signals.get("github_momentum_pct")

        logger.info(f"OnChainAnalyst complete for {ticker} — health={result.network_health} dev_score={dev_score} comm_score={comm_score} fear_greed={result.fear_greed_score}")
        return {"onchain": result.model_dump()}