import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, BullThesis
from src.exceptions import LLMStructuredOutputError

logger = logging.getLogger(__name__)

BULL_REFERENCE = """
KEY PRINCIPLES:
1. Valuation is relative not absolute. High P/E with high growth can be fair (PEG ratio).
2. Sentiment reflects NOW, fundamentals reflect LATER. When they diverge, fundamentals win over 6-12 months.
3. Revenue growth TREND matters more than level. Acceleration from -5% to +5% is more valuable than deceleration from +20% to +5%.
4. The best calls cite specific numbers. "P/E 64x with negative EPS" beats "overvalued."
"""

BULL_PROMPT = """You are the Bull Analyst at an elite investment research firm.
Your ONLY job is to construct the most compelling, data-grounded bull case for {ticker}.
You are an advocate, not a balanced commentator. Find the strongest upside argument the data supports.
If the data is weak, say so in your confidence level — but still make the best case available.
Do NOT mention bear risks. Do NOT hedge. Make the bull case and own it.

{bull_reference}
REASONING FRAMEWORK (work through this before writing):
1. What does the macro regime mean for THIS specific asset? (Risk-On = tailwind, Risk-Off = headwind)
2. Are fundamentals improving, stable, or deteriorating? Which metric is strongest?
3. What does sentiment + headlines tell you about near-term momentum?
4. Is the Wall Street consensus in agreement? Does consensus buy + positive momentum compound?
5. What is the single strongest catalyst that could drive outperformance?

CONFIDENCE CALIBRATION:
- High: Multiple independent signals align bullish (macro + fundamentals + sentiment all positive)
- Medium: Majority of signals positive but with meaningful counter-evidence
- Low: Weak bull case — data is mixed, you're making the best argument from limited evidence

--- MACRO CONTEXT ---
Regime: {regime_label} | Summary: {regime_summary}

--- FUNDAMENTALS ---
P/E: {pe_ratio} | EPS: {eps} | Revenue Growth: {revenue_growth} | D/E: {debt_to_equity}

--- SENTIMENT ---
Score: {sentiment_score}/1.0 | Label: {sentiment_label}
Reasoning: {sentiment_reasoning}

--- WALL STREET CONSENSUS ---
Recommendation: {analyst_recommendation} | Target: {target_price} | Analysts: {num_analysts}

--- RECENT HEADLINES ---
{headlines}

Your output:
- thesis: 3-4 sentences. Lead with the strongest signal. Name specific metrics, not generalities. "Revenue growing at 23% with expanding margins" beats "strong fundamentals."
- confidence: 'High', 'Medium', or 'Low' — calibrated to how well the data supports the bull case
- key_catalysts: exactly 4 specific, concrete catalysts grounded in the data. No vague statements like "continued growth." Each catalyst should be falsifiable.
"""


class BullAnalyst:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(BullThesis)

    def analyze(self, state: FinanceState, **kwargs) -> dict:
        ticker = state["ticker"]
        logger.info(f"BullAnalyst starting for {ticker}...")

        macro = state.get("macro") or {}
        sentiment = state.get("sentiment") or {}
        fundamentals = state.get("fundamentals") or {}
        consensus = state.get("analyst_consensus") or {}
        headlines = state.get("news_headlines", [])

        def fmt(val, suffix="") -> str:
            return f"{val}{suffix}" if val is not None else "unavailable"

        f = fundamentals if isinstance(fundamentals, dict) else (fundamentals.model_dump() if hasattr(fundamentals, 'model_dump') else {})
        m = macro if isinstance(macro, dict) else {}
        s = sentiment if isinstance(sentiment, dict) else {}
        c = consensus if isinstance(consensus, dict) else (consensus.model_dump() if hasattr(consensus, 'model_dump') else {})

        prompt = BULL_PROMPT.format(
            ticker=ticker,
            bull_reference=BULL_REFERENCE,
            regime_label=fmt(m.get("regime_label")),
            regime_summary=fmt(m.get("regime_summary")),
            pe_ratio=fmt(f.get("PE_ratio")),
            eps=fmt(f.get("EPS"), " USD"),
            revenue_growth=fmt(f.get("revenue_growth")),
            debt_to_equity=fmt(f.get("debt_to_equity")),
            sentiment_score=fmt(s.get("sentiment_score")),
            sentiment_label=fmt(s.get("sentiment_label")),
            sentiment_reasoning=fmt(s.get("sentiment_reasoning")),
            analyst_recommendation=fmt(c.get("recommendation")),
            target_price=fmt(c.get("target_price"), "$"),
            num_analysts=fmt(c.get("num_analysts")),
            headlines="\n- " + "\n- ".join(headlines[:10]) if headlines else "No headlines available.",
        )

        try:
            result: BullThesis = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"BullAnalyst LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"BullAnalyst failed: {e}")

        logger.info(f"BullAnalyst complete for {ticker} — confidence={result.confidence} catalysts={len(result.key_catalysts)}")
        return {"bull_thesis": result.model_dump()}