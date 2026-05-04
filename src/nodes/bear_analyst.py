import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, BearThesis
from src.exceptions import LLMStructuredOutputError


logger = logging.getLogger(__name__)

class _D(dict):
    def __getattr__(self, k): return self.get(k)
    def __bool__(self): return len(self) > 0

def _w(v): return _D(v) if isinstance(v, dict) else v

BEAR_PROMPT = """You are the Bear Analyst at an investment research firm.
Your role is to construct the strongest possible bear case for {ticker}.
You are NOT a balanced analyst — your job is to find and articulate the most
compelling reasons this asset will underperform or decline. Be rigorous, not alarmist.
Base your thesis only on the data provided — do not invent metrics.

--- MACRO CONTEXT ---
Regime: {regime_label}
Summary: {regime_summary}

--- FUNDAMENTALS ---
P/E Ratio: {pe_ratio}
EPS: {eps}
Revenue Growth (YoY): {revenue_growth}
Debt-to-Equity: {debt_to_equity}

--- SENTIMENT ---
Sentiment Score: {sentiment_score} (scale: -1.0 bearish to 1.0 bullish)
Sentiment Label: {sentiment_label}
Reasoning: {sentiment_reasoning}

--- ANALYST CONSENSUS ---
Wall Street Recommendation: {analyst_recommendation}
Mean Price Target: {target_price}
Number of Analysts: {num_analysts}

--- RECENT HEADLINES ---
{headlines}

Your output:
- thesis: A compelling 3-4 sentence bear case narrative grounded in the data above
- confidence: Your conviction level — 'High', 'Medium', or 'Low'
- key_risks: 3-5 specific, concrete risks or headwinds that support the downside case
"""


class BearAnalyst:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(BearThesis)

    def analyze(self, state: FinanceState, **kwargs) -> dict:
        ticker = state["ticker"]
        logger.info(f"BearAnalyst starting for {ticker}...")

        macro = _w(state.get("macro"))
        sentiment = _w(state.get("sentiment"))
        fundamentals = _w(state.get("fundamentals"))
        consensus = _w(state.get("analyst_consensus"))
        headlines = state.get("news_headlines", [])

        def fmt(val, suffix="") -> str:
            return f"{val}{suffix}" if val is not None else "unavailable"

        prompt = BEAR_PROMPT.format(
            ticker=ticker,
            regime_label=fmt(macro.regime_label if macro else None),
            regime_summary=fmt(macro.regime_summary if macro else None),
            pe_ratio=fmt(fundamentals.PE_ratio if fundamentals else None),
            eps=fmt(fundamentals.EPS if fundamentals else None, " USD"),
            revenue_growth=fmt(fundamentals.revenue_growth if fundamentals else None),
            debt_to_equity=fmt(fundamentals.debt_to_equity if fundamentals else None),
            sentiment_score=fmt(sentiment.sentiment_score if sentiment else None),
            sentiment_label=fmt(sentiment.sentiment_label if sentiment else None),
            sentiment_reasoning=fmt(sentiment.sentiment_reasoning if sentiment else None),
            analyst_recommendation=fmt(consensus.recommendation if consensus else None),
            target_price=fmt(consensus.target_price if consensus else None, " USD"),
            num_analysts=fmt(consensus.num_analysts if consensus else None),
            headlines="\n".join(f"- {h}" for h in headlines[:10]) or "No headlines available",
        )

        try:
            result: BearThesis = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"BearAnalyst LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"BearAnalyst failed to produce structured output: {e}")

        logger.info(f"BearAnalyst complete for {ticker} — confidence={result.confidence} risks={len(result.key_risks)}")
        return {"bear_thesis": result.model_dump()}