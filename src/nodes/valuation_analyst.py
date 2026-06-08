import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, ValuationData
from src.exceptions import LLMStructuredOutputError

logger = logging.getLogger(__name__)

# Sector P/E benchmarks — used when yfinance doesn't return sectorPE
# Based on trailing 5-year median sector P/E ratios (S&P 500 Damodaran data)
SECTOR_PE_BENCHMARKS = {
    "Technology": 32,
    "Healthcare": 20,
    "Financial Services": 14,
    "Consumer Cyclical": 22,
    "Consumer Defensive": 23,
    "Industrials": 21,
    "Energy": 13,
    "Utilities": 18,
    "Real Estate": 35,
    "Basic Materials": 16,
    "Communication Services": 20,
}

VALUATION_REFERENCE = """
PEG = P/E / revenue_growth_pct. The most important valuation metric.
PEG <1.0 = undervalued | 1.0-2.0 = fair | >2.5 = overvalued vs growth
Amazon traded P/E >100x correctly because AWS growth justified it.
P/E is MEANINGLESS with negative EPS — use price/sales instead.

SECTOR P/E BENCHMARKS (2025-2026 forward P/E actuals):
Tech 28-32x | Healthcare 15-18x | Financials 14-18x | Energy 12-16x
Consumer Disc 25-30x | Industrials 20-24x | Utilities 16-20x | Staples 20-24x
Russell 1000 Growth ~39x | Russell 1000 Value ~20x

INTEREST RATE ADJUSTMENT:
Fed Funds >4%: compress fair P/E by ~15-20% vs historical norms
Fed Funds 2-4%: normal historical P/E ranges apply
Fed Funds <2%: P/E expansion justified
"""

VALUATION_PROMPT = """You are the Valuation Analyst at an elite investment research firm.
Your job is to determine whether {ticker} is Overvalued, Fairly Valued, or Undervalued relative to fundamentals.
Be precise. Vague labels with generic justifications are useless. Every conclusion must cite a specific number.

{valuation_reference}
VALUATION DECISION FRAMEWORK:
Apply this in order:

STEP 1 — P/E vs Sector comparison:
- If P/E > sector P/E by >50%: strong Overvalued signal (premium must be justified by growth)
- If P/E within ±20% of sector: Fairly Valued on this metric
- If P/E < sector P/E by >20%: Undervalued signal (check if justified by deteriorating growth)

STEP 2 — Growth-adjusted valuation (PEG approximation):
- If revenue_growth > 20% AND P/E < 40: growth justifies premium → Fairly Valued or Undervalued
- If revenue_growth < 10% AND P/E > 30: growth doesn't justify premium → Overvalued
- If revenue_growth < 0% AND P/E > 15: contracting business at a premium → Overvalued

STEP 3 — Price target vs current price:
- If current price > analyst target by >15%: Overvalued signal
- If current price within ±10% of analyst target: Fairly Valued
- If current price < analyst target by >15%: Undervalued signal

STEP 4 — 52-week range context:
- Trading >90% of 52-week range: momentum priced in, Overvalued risk
- Trading <20% of 52-week range: distressed or deep value

STEP 5 — Macro discount rate adjustment:
- High rates (Fed Funds >4%): compress fair P/E by ~15-20% vs historical norms
- Low rates (Fed Funds <2%): expand fair P/E premium acceptable

FINAL DECISION: Weight all signals. If 3+ signals point the same direction, use that label with High confidence. Mixed signals → Fairly Valued. Never label Overvalued if growth is accelerating above 25%.

--- FUNDAMENTALS ---
P/E Ratio: {pe_ratio}
EPS: {eps}
Revenue Growth (YoY): {revenue_growth}
Debt-to-Equity: {debt_to_equity}

--- SECTOR CONTEXT ---
Sector: {sector} | Industry: {industry} | Sector P/E: {sector_pe}

--- PRICE CONTEXT ---
Current Price: {current_price} USD
52-Week High: {week_52_high} USD | 52-Week Low: {week_52_low} USD
Mean Analyst Price Target: {target_price} USD

--- MACRO CONTEXT ---
Regime: {regime_label} | Fed Funds Rate: {fed_funds_rate}% | Yield Curve: {yield_curve_spread}

Your output:
- pe_vs_sector: One precise sentence with actual numbers. Example: "Trading at 28x vs sector median of 18x — a 56% premium requiring 20%+ growth to justify."
- intrinsic_value_estimate: One sentence using price target and 52-week range. Example: "Analyst consensus target of $185 implies 12% upside from current $165, with support at 52-week low of $142."
- valuation_label: EXACTLY one of 'Overvalued', 'Fairly Valued', or 'Undervalued'
- valuation_summary: 2-3 sentences explaining which steps drove the label and what the risk/reward looks like from current levels.
"""


class ValuationAnalyst:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(ValuationData)

    def analyze(self, state: FinanceState, **kwargs) -> dict:
        ticker = state["ticker"]
        logger.info(f"ValuationAnalyst starting for {ticker}...")

        info = state["raw_data"].get("info", {})
        fundamentals = state.get("fundamentals") or {}
        consensus = state.get("analyst_consensus") or {}
        macro = state.get("macro") or {}

        def fmt(val, prefix="", suffix="") -> str:
            return f"{prefix}{val}{suffix}" if val is not None else "unavailable"

        f = fundamentals if isinstance(fundamentals, dict) else (fundamentals.model_dump() if hasattr(fundamentals, 'model_dump') else {})
        m = macro if isinstance(macro, dict) else {}
        c = consensus if isinstance(consensus, dict) else (consensus.model_dump() if hasattr(consensus, 'model_dump') else {})

        # Use sector benchmark when yfinance doesn't return sectorPE
        sector = info.get("sector", "")
        sector_pe_raw = info.get("sectorPE") or info.get("trailingPegRatio")
        if not sector_pe_raw and sector in SECTOR_PE_BENCHMARKS:
            sector_pe_val = f"{SECTOR_PE_BENCHMARKS[sector]} (sector benchmark)"
        elif sector_pe_raw:
            sector_pe_val = str(sector_pe_raw)
        else:
            sector_pe_val = "unavailable"

        prompt = VALUATION_PROMPT.format(
            ticker=ticker,
            valuation_reference=VALUATION_REFERENCE,
            pe_ratio=fmt(f.get("PE_ratio")),
            eps=fmt(f.get("EPS"), suffix=" USD"),
            revenue_growth=fmt(f.get("revenue_growth")),
            debt_to_equity=fmt(f.get("debt_to_equity")),
            sector=fmt(sector),
            industry=fmt(info.get("industry")),
            sector_pe=sector_pe_val,
            current_price=fmt(info.get("regularMarketPrice") or info.get("currentPrice"), prefix="$"),
            week_52_high=fmt(info.get("fiftyTwoWeekHigh"), prefix="$"),
            week_52_low=fmt(info.get("fiftyTwoWeekLow"), prefix="$"),
            target_price=fmt(c.get("target_price"), prefix="$"),
            regime_label=fmt(m.get("regime_label")),
            fed_funds_rate=fmt(m.get("fed_funds_rate")),
            yield_curve_spread=fmt(m.get("yield_curve_spread")),
        )

        try:
            result: ValuationData = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"ValuationAnalyst LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"ValuationAnalyst failed: {e}")

        logger.info(f"ValuationAnalyst complete for {ticker} — label={result.valuation_label}")
        return {"valuation": result.model_dump()}