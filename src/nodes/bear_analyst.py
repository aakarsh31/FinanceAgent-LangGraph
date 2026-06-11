import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, BearThesis
from src.exceptions import LLMStructuredOutputError

logger = logging.getLogger(__name__)

BEAR_REFERENCE = """
KEY PRINCIPLES:
1. Valuation is relative not absolute. High P/E with high growth can be fair (PEG ratio).
2. Sentiment reflects NOW, fundamentals reflect LATER. When they diverge, fundamentals win over 6-12 months.
3. Revenue growth TREND matters more than level. Acceleration from -5% to +5% is more valuable than deceleration from +20% to +5%.
4. The best calls cite specific signals: "Revenue decelerating to low single digits with rising leverage" beats "weak fundamentals."
5. ROLE BOUNDARY: Do NOT reference P/E ratios or compare to sector medians — you have NOT been given P/E data.
   That is the ValuationAnalyst's job. Use the valuation_label already provided.
   Valid bear risks: margin compression, debt vulnerability, competition, regulatory, execution risk.
   Invalid: any P/E or sector comparison claim — you do not have this data, do not invent it.
"""

BEAR_PROMPT = """You are the Bear Analyst at an elite investment research firm.
Your ONLY job is to construct the most compelling, data-grounded bear case for {ticker}.
You are a skeptic and a critic. Find every crack in the story. Surface every risk the data supports.
Do NOT mention bullish factors. Do NOT hedge. Make the bear case and own it.
"Rigorous, not alarmist" means: base every risk on data, but don't soften your conclusions.

CRITICAL: You have NOT been given P/E ratio data. Do not reference P/E, PEG, or sector multiples.
Use only the qualitative signals provided below. The ValuationAnalyst handles all multiple-based analysis.

{bear_reference}
REASONING FRAMEWORK (work through this before writing):
1. What does the macro regime mean for THIS specific asset's downside? (Risk-Off = amplified risk)
2. Are fundamentals showing deterioration? Earnings health? Revenue trend? Leverage?
3. What does negative sentiment and bearish headlines signal about near-term momentum?
4. Is the valuation label unfavorable? Use it as-is — do not re-derive it.
5. What is the single scenario that causes the most severe downside?

CONFIDENCE CALIBRATION:
- High: Multiple independent signals align bearish (macro headwind + deteriorating fundamentals + negative sentiment)
- Medium: Some bearish signals with bullish counter-evidence present
- Low: Constructing a bear case against primarily positive data — valid but acknowledge the weakness

--- MACRO CONTEXT ---
Regime: {regime_label} | Summary: {regime_summary}

--- FUNDAMENTALS (qualitative) ---
Earnings Health: {earnings_health}
Revenue Trend: {revenue_trend}
Leverage: {leverage_level}
Valuation: {valuation_label}

--- TECHNICAL ANALYSIS ---
Signal: {technical_signal} | Trend: {technical_trend} | Momentum: {technical_momentum}
Key Levels: {technical_levels}

--- SENTIMENT ---
Score: {sentiment_score}/1.0 | Label: {sentiment_label}
Reasoning: {sentiment_reasoning}

--- WALL STREET CONSENSUS ---
Recommendation: {analyst_recommendation} | Target: {target_price} | Analysts: {num_analysts}

--- RECENT HEADLINES ---
{headlines}

Your output:
- thesis: 3-4 sentences. Lead with the most severe risk. Be specific — cite the qualitative signals above, not invented numbers.
- confidence: 'High', 'Medium', or 'Low' — calibrated to how well the data supports the bear case
- key_risks: exactly 4 specific, concrete risks grounded in the data above. Each risk should reference a signal and describe the scenario. No P/E claims, no sector median claims.
"""


# ── Qualitative bucket helpers ──────────────────────────────────────────────

def _earnings_health(eps) -> str:
    """Convert raw EPS float → qualitative earnings label."""
    if eps is None:
        return "Earnings data unavailable"
    eps = float(eps)
    if eps > 10:
        return f"Strongly profitable (${eps:.2f} EPS)"
    if eps > 3:
        return f"Profitable (${eps:.2f} EPS)"
    if eps > 0:
        return f"Marginally profitable (${eps:.2f} EPS)"
    if eps == 0:
        return "Break-even (EPS ~$0)"
    return f"Unprofitable (${eps:.2f} EPS — negative earnings)"


def _revenue_trend(revenue_growth) -> str:
    """Convert raw revenue_growth decimal → qualitative trend label."""
    if revenue_growth is None:
        return "Revenue trend unavailable"
    g = float(revenue_growth) * 100  # convert 0.17 → 17.0
    if g > 25:
        return f"High growth ({g:.0f}% YoY)"
    if g > 10:
        return f"Healthy growth ({g:.0f}% YoY)"
    if g > 3:
        return f"Modest growth ({g:.0f}% YoY)"
    if g >= 0:
        return f"Stagnant revenue ({g:.0f}% YoY)"
    if g > -10:
        return f"Declining revenue ({g:.0f}% YoY)"
    return f"Significant revenue decline ({g:.0f}% YoY)"


def _leverage_level(debt_to_equity) -> str:
    """Convert raw D/E ratio → qualitative leverage label."""
    if debt_to_equity is None:
        return "Leverage data unavailable"
    de = float(debt_to_equity)
    if de < 0:
        return "Negative equity (balance sheet distress)"
    if de < 0.3:
        return f"Low leverage ({de:.1f}x D/E)"
    if de < 0.8:
        return f"Moderate leverage ({de:.1f}x D/E)"
    if de < 2.0:
        return f"Elevated leverage ({de:.1f}x D/E)"
    return f"High leverage ({de:.1f}x D/E — significant debt burden)"


# ── Agent ────────────────────────────────────────────────────────────────────

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

        f = fundamentals if isinstance(fundamentals, dict) else (fundamentals.model_dump() if hasattr(fundamentals, "model_dump") else {})
        m = macro if isinstance(macro, dict) else {}
        s = sentiment if isinstance(sentiment, dict) else {}
        c = consensus if isinstance(consensus, dict) else (consensus.model_dump() if hasattr(consensus, "model_dump") else {})

        # Convert raw numbers → qualitative buckets before the LLM sees them
        earnings_health = _earnings_health(f.get("EPS"))
        revenue_trend = _revenue_trend(f.get("revenue_growth"))
        leverage_level = _leverage_level(f.get("debt_to_equity"))
        valuation_label = fmt(f.get("valuation_label") or m.get("valuation_label") or state.get("valuation", {}).get("valuation_label") if isinstance(state.get("valuation"), dict) else None)

        # Technical signals
        tech = state.get("technical") or {}
        technical_signal = tech.get("signal", "unavailable")
        technical_trend = tech.get("trend", "unavailable")
        technical_momentum = tech.get("momentum", "unavailable")
        technical_levels = ", ".join(tech.get("key_levels", [])) or "unavailable"

        prompt = BEAR_PROMPT.format(
            ticker=ticker,
            bear_reference=BEAR_REFERENCE,
            regime_label=fmt(m.get("regime_label")),
            regime_summary=fmt(m.get("regime_summary")),
            earnings_health=earnings_health,
            revenue_trend=revenue_trend,
            leverage_level=leverage_level,
            valuation_label=valuation_label,
            technical_signal=technical_signal,
            technical_trend=technical_trend,
            technical_momentum=technical_momentum,
            technical_levels=technical_levels,
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
