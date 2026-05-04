import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, ValuationData
from src.exceptions import LLMStructuredOutputError

logger = logging.getLogger(__name__)

class _D(dict):
    def __getattr__(self, k): return self.get(k)
    def __bool__(self): return len(self) > 0

def _w(v): return _D(v) if isinstance(v, dict) else v

VALUATION_PROMPT = """You are the Valuation Analyst at an investment research firm.
Your role is to assess whether {ticker} is fairly priced relative to its fundamentals
and sector peers. Your judgments must be grounded in the numbers provided.
Do not invent data or assume values not given.

--- FUNDAMENTALS ---
P/E Ratio: {pe_ratio}
EPS: {eps}
Revenue Growth (YoY): {revenue_growth}
Debt-to-Equity: {debt_to_equity}

--- SECTOR CONTEXT ---
Sector: {sector}
Industry: {industry}
Sector P/E (trailing): {sector_pe}

--- PRICE CONTEXT ---
Current Price: {current_price} USD
52-Week High: {week_52_high} USD
52-Week Low: {week_52_low} USD
Mean Analyst Price Target: {target_price} USD

--- MACRO CONTEXT ---
Regime: {regime_label}
Yield Curve Spread: {yield_curve_spread} (negative = inverted = higher discount rate pressure)

Your output:
- pe_vs_sector: One sentence comparing the stock's P/E to its sector median
- intrinsic_value_estimate: One sentence on fair value using price target and 52-week range
- valuation_label: Exactly one of 'Overvalued', 'Fairly Valued', or 'Undervalued'
- valuation_summary: 2-3 sentences explaining the valuation and what it means for risk/reward
"""


class ValuationAnalyst:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(ValuationData)

    def analyze(self, state: FinanceState) -> dict:
        ticker = state["ticker"]
        logger.info(f"ValuationAnalyst starting for {ticker}...")

        info = state["raw_data"].get("info", {})
        fundamentals = _w(state.get("fundamentals"))
        consensus = _w(state.get("analyst_consensus"))
        macro = _w(state.get("macro"))

        def fmt(val, prefix="", suffix="") -> str:
            return f"{prefix}{val}{suffix}" if val is not None else "unavailable"

        prompt = VALUATION_PROMPT.format(
            ticker=ticker,
            pe_ratio=fmt(fundamentals.PE_ratio if fundamentals else None),
            eps=fmt(fundamentals.EPS if fundamentals else None, suffix=" USD"),
            revenue_growth=fmt(fundamentals.revenue_growth if fundamentals else None),
            debt_to_equity=fmt(fundamentals.debt_to_equity if fundamentals else None),
            sector=fmt(info.get("sector")),
            industry=fmt(info.get("industry")),
            sector_pe=fmt(info.get("sectorPE") or info.get("trailingPegRatio")),
            current_price=fmt(info.get("regularMarketPrice"), suffix=" USD"),
            week_52_high=fmt(info.get("fiftyTwoWeekHigh"), suffix=" USD"),
            week_52_low=fmt(info.get("fiftyTwoWeekLow"), suffix=" USD"),
            target_price=fmt(consensus.target_price if consensus else None, suffix=" USD"),
            regime_label=fmt(macro.regime_label if macro else None),
            yield_curve_spread=fmt(macro.yield_curve_spread if macro else None),
        )

        try:
            result: ValuationData = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"ValuationAnalyst LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"ValuationAnalyst failed to produce structured output: {e}")

        logger.info(f"ValuationAnalyst complete for {ticker} — label={result.valuation_label}")
        return {"valuation": result.model_dump()}