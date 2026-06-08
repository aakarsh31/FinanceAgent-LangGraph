import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, BearThesis
from src.exceptions import LLMStructuredOutputError

logger = logging.getLogger(__name__)

BEAR_REFERENCE = """
KEY PRINCIPLES:
1. Valuation is relative not absolute. High P/E with high growth can be fair (PEG = P/E / growth_pct).
2. Sentiment reflects NOW, fundamentals reflect LATER. When they diverge, fundamentals win over 6-12 months.
3. Revenue growth TREND matters more than level. Acceleration from -5% to +5% is more valuable than deceleration from +20% to +5%.
4. The best calls cite specific numbers. "P/E 64x with negative EPS" beats "overvalued."
5. ROLE BOUNDARY: Do NOT compare P/E to sector median — that is the ValuationAnalyst's job.
   Reference the valuation_label already provided. Your job is to identify RISKS, not re-derive valuation.
   Valid bear risks: margin compression, debt vulnerability, competition, regulatory, execution risk.
   Invalid: "P/E is X% above sector median" — you don't have current sector data to make this claim.
"""

BEAR_PROMPT = """You are the Bear Analyst at an elite investment research firm.
Your ONLY job is to construct the most compelling, data-grounded bear case for {ticker}.
You are a skeptic and a critic. Find every crack in the story. Surface every risk the data supports.
Do NOT mention bullish factors. Do NOT hedge. Make the bear case and own it.
"Rigorous, not alarmist" means: base every risk on data, but don't soften your conclusions.

{bear_reference}
REASONING FRAMEWORK (work through this before writing):
1. What does the macro regime mean for THIS specific asset's downside? (Risk-Off = amplified risk)
2. Are fundamentals showing any deterioration signals? Declining margins? Rising debt? EPS misses?
3. What does negative sentiment and bearish headlines signal about near-term momentum?
4. Is the stock/asset overvalued relative to what the data justifies?
5. What is the single scenario that causes the most severe downside?

CONFIDENCE CALIBRATION:
- High: Multiple independent signals align bearish (macro headwind + deteriorating fundamentals + negative sentiment)
- Medium: Some bearish signals with bullish counter-evidence present
- Low: Constructing a bear case against primarily positive data — valid but acknowledge the weakness

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
- thesis: 3-4 sentences. Lead with the most severe risk. Be specific — "P/E of 45x with revenue growth decelerating to 8% leaves no margin for error" beats "valuation concerns."
- confidence: 'High', 'Medium', or 'Low' — calibrated to how well the data supports the bear case
- key_risks: exactly 4 specific, concrete risks grounded in the data. Each risk should name the metric and the scenario. No vague risks like "market downturn."
"""


class BearAnalyst:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(BearThesis)

    def analyze(self, state: FinanceState, **kwargs) -> dict:
        ticker = state["ticker"]
        logger.info(f"BearAnalyst starting for {ticker}...")

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

        prompt = BEAR_PROMPT.format(
            ticker=ticker,
            bear_reference=BEAR_REFERENCE,
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
            result: BearThesis = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"BearAnalyst LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"BearAnalyst failed: {e}")

        logger.info(f"BearAnalyst complete for {ticker} — confidence={result.confidence} risks={len(result.key_risks)}")
        return {"bear_thesis": result.model_dump()}