import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, FundamentalsData
from src.exceptions import LLMStructuredOutputError

logger = logging.getLogger(__name__)

FUNDAMENTALS_REFERENCE = """
PEG RATIO = P/E / revenue_growth_pct. PEG <1.0 = undervalued vs growth. PEG >2.5 = overvalued.
Example: P/E 40x + 40% growth = PEG 1.0 = fair. P/E 40x + 5% growth = PEG 8.0 = overvalued.

SECTOR P/E BENCHMARKS (2025-2026):
Tech 28-32x | Healthcare 15-18x | Financials 14-18x | Energy 12-16x
Consumer Disc 25-30x | Industrials 20-24x | Utilities 16-20x | Consumer Staples 20-24x

DATA QUALITY (yfinance):
- revenueGrowth = TTM YoY decimal (0.05=5%, 0.85=85%). PREFER over earningsQuarterlyGrowth.
- debtToEquity = returned as percentage (125 = 1.25x actual D/E ratio)
- trailingPE preferred over forwardPE
- P/E is MEANINGLESS when EPS is negative
"""

FUNDAMENTALS_PROMPT = """You are the Fundamentals Analyst at an elite investment research firm.
Your job is to extract and interpret the core financial health indicators for {ticker}.
You are given raw yfinance data — extract ONLY the fields listed below. Do not calculate or estimate.

{fundamentals_reference}
EXTRACTION RULES:
- PE_ratio: Use trailingPE if available, forwardPE as fallback. If both null → return null. Never estimate.
- EPS: Use trailingEps. If null → return null.
- revenue_growth: Use revenueGrowth (already a decimal, e.g. 0.12 = 12% growth). If null → return null.
- debt_to_equity: Use debtToEquity. If null → return null. Note: yfinance returns this as a percentage (e.g. 150 = 1.5x D/E ratio).

INTERPRETATION CONTEXT (use for your internal reasoning, not output):
- P/E < 10: Deep value or distress
- P/E 10-20: Reasonable value
- P/E 20-35: Growth premium, justified if revenue growth > 15%
- P/E > 50: Speculation or high-growth priced to perfection
- Revenue growth > 20%: High growth
- Revenue growth 5-20%: Healthy
- Revenue growth < 0%: Contraction — bearish signal
- D/E > 200 (i.e., 2.0x): High leverage — amplifies both risk and return

RAW DATA:
{relevant_data}

Return ONLY what is explicitly present in the data. null is more honest than a guess.
"""


class FundamentalsAgent:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(FundamentalsData)

    def analyze(self, state: FinanceState, **kwargs) -> dict:
        ticker = state["ticker"]
        logger.info(f"FundamentalsAgent starting for {ticker}...")

        info = state["raw_data"].get("info", {})

        relevant_keys = [
            "trailingPE", "forwardPE", "trailingEps",
            "revenueGrowth",             # TTM YoY — preferred
            "earningsGrowth",            # TTM earnings growth
            "debtToEquity", "marketCap", "sector", "industry",
            "returnOnEquity", "returnOnAssets", "grossMargins", "operatingMargins",
            "totalRevenue",              # used to cross-check growth
            "revenueQuarterlyGrowth",    # quarterly — use as secondary only
        ]
        relevant_data = {k: info.get(k) for k in relevant_keys if info.get(k) is not None}

        # Sanity check revenueGrowth — if it looks like a quarterly figure
        # (very different from earningsGrowth direction), flag it.
        # The agent will see both and reason about which is more reliable.
        rev_growth = relevant_data.get("revenueGrowth")
        quarterly_growth = relevant_data.get("revenueQuarterlyGrowth")
        if rev_growth is not None and quarterly_growth is not None:
            # If quarterly and TTM differ by more than 10 percentage points, surface both
            if abs(rev_growth - quarterly_growth) > 0.10:
                relevant_data["_growth_note"] = (
                    f"WARNING: revenueGrowth (TTM={rev_growth:.1%}) and "
                    f"revenueQuarterlyGrowth (Q={quarterly_growth:.1%}) diverge significantly. "
                    f"Use revenueGrowth (TTM) as the authoritative figure."
                )

        # Pre-normalize revenue growth — resolve TTM vs quarterly inconsistency
        # yfinance revenueGrowth is TTM YoY, but occasionally returns quarterly
        # We validate: if revenueGrowth is implausibly small vs earningsGrowth trend,
        # prefer revenueQuarterlyGrowth or flag for the LLM
        rev_ttm = info.get("revenueGrowth")
        rev_quarterly = info.get("revenueQuarterlyGrowth")

        if rev_ttm is not None:
            # Values near 0 (< 0.01 absolute) are suspicious for large caps
            # Cross-check: if quarterly growth >> TTM, TTM might be stale
            if abs(rev_ttm) < 0.01 and rev_quarterly is not None and abs(rev_quarterly) > 0.05:
                # TTM looks stale/wrong — use quarterly as best available
                relevant_data["revenueGrowth"] = rev_quarterly
                relevant_data["_growth_note"] = (
                    f"revenueGrowth TTM ({rev_ttm:.3f}) appears stale. "
                    f"Using revenueQuarterlyGrowth ({rev_quarterly:.3f}) as best available estimate."
                )
            else:
                # TTM looks valid — use it
                relevant_data["revenueGrowth"] = rev_ttm

        prompt = FUNDAMENTALS_PROMPT.format(
            ticker=ticker,
            fundamentals_reference=FUNDAMENTALS_REFERENCE,
            relevant_data=relevant_data
        )

        try:
            result: FundamentalsData = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"FundamentalsAgent LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"FundamentalsAgent failed: {e}")

        logger.info(f"FundamentalsAgent complete for {ticker} — PE={result.PE_ratio} EPS={result.EPS}")
        return {"fundamentals": result}