import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, MacroRegimeData
from src.exceptions import LLMStructuredOutputError

logger = logging.getLogger(__name__)

VALID_REGIME_LABELS = [
    "Risk-On Easing",
    "Risk-On Tightening",
    "Risk-Off Easing",
    "Risk-Off Tightening",
    "Stagflation",
    "Early Recovery",
]

MACRO_REFERENCE = """
REGIME → ASSET IMPLICATIONS (from financial analysis cheat sheet):
Risk-On Easing:      Equity BULLISH | Crypto BULLISH     | Fed falling, inflation contained
Risk-On Tightening:  Equity SELECTIVE | Crypto CAUTIOUS   | Fed high, economy resilient
Risk-Off Easing:     Equity BEARISH  | Crypto BEARISH     | Fed cutting due to weakness
Risk-Off Tightening: Equity BEARISH  | Crypto STRONGLY BEARISH | High rates + slowing growth
Stagflation:         Equity BEARISH  | Crypto MIXED       | High inflation + stalling growth
Early Recovery:      Equity BULLISH  | Crypto BULLISH     | Low rates, unemployment falling

YIELD CURVE: >0.5% = normal | 0-0.5% = flat/caution | <0 = inverted (recession signal)
FED FUNDS:   >4% = restrictive | 2-4% = neutral | <2% = accommodative
CPI:         >3.5% = elevated | 2-3.5% = moderate | <2% = contained
"""

MACRO_PROMPT = """You are the MacroRegime Analyst at an elite investment research firm.
Your classification directly determines how every other analyst weights their thesis.
Getting this wrong cascades through the entire pipeline. Be precise.

INDICATOR DEFINITIONS AND THRESHOLDS:
- Fed Funds Rate: Central bank benchmark rate. >4% = restrictive, 2-4% = neutral, <2% = accommodative
- CPI YoY: Inflation. >3.5% = elevated, 2-3.5% = elevated-moderate, <2% = contained
- Yield Curve Spread (10Y-2Y): Growth expectations. <0 = inverted (recession signal), 0-0.5 = flat (caution), >0.5 = normal
- Unemployment: Labor market. <4% = tight (inflationary pressure), 4-5% = balanced, >5% = slack (growth concern)

REGIME DECISION MATRIX (use this to reason):
- Risk-On Easing: rates falling/low AND inflation cooling AND curve normal → buy risk assets
- Risk-On Tightening: rates high BUT economy resilient, markets performing → cautious risk-on
- Risk-Off Easing: rates being CUT in response to weakness → flight to safety, cuts not yet stimulative
- Risk-Off Tightening: rates high + growth slowing + curve inverted → HIGH recession risk, defensive
- Stagflation: CPI >3.5% AND growth slowing simultaneously → worst of both worlds, hard assets win
- Early Recovery: rates low + CPI stable + unemployment falling → maximum risk-on, early cycle

{macro_reference}
CURRENT DATA:
- Fed Funds Rate: {fed_funds_rate}%
- CPI YoY: {cpi_yoy}%
- Yield Curve Spread (10Y-2Y): {yield_curve_spread} percentage points
- Unemployment Rate: {unemployment_rate}%

REASONING STEPS (work through this before concluding):
1. Is inflation elevated or contained?
2. Is the yield curve normal, flat, or inverted?
3. Is monetary policy restrictive or accommodative?
4. Is growth accelerating or decelerating?
5. Which regime label best fits the combination?

Your output:
- regime_label: EXACTLY one of: {valid_labels}
- regime_summary: 2-3 sentences. State the regime, explain the key indicator combination that determined it, and specify the implication for BOTH equity AND crypto markets with directional clarity.
"""


class MacroRegimeAgent:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(MacroRegimeData)

    def analyze(self, state: FinanceState, **kwargs) -> dict:
        logger.info("MacroRegimeAgent starting...")

        fred = state["raw_data"].get("fred")
        if not fred:
            logger.warning("No FRED data in state — MacroRegimeAgent returning None")
            return {"macro": None}

        fed_funds_rate = fred.get("fed_funds_rate")
        cpi_yoy = fred.get("cpi_yoy")
        yield_curve_spread = fred.get("yield_curve_spread")
        unemployment_rate = fred.get("unemployment_rate")

        def fmt(val) -> str:
            return str(val) if val is not None else "unavailable"

        prompt = MACRO_PROMPT.format(
            macro_reference=MACRO_REFERENCE,
            valid_labels=", ".join(f"'{l}'" for l in VALID_REGIME_LABELS),
            fed_funds_rate=fmt(fed_funds_rate),
            cpi_yoy=fmt(cpi_yoy),
            yield_curve_spread=fmt(yield_curve_spread),
            unemployment_rate=fmt(unemployment_rate),
        )

        try:
            result: MacroRegimeData = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"MacroRegimeAgent LLM call failed: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"MacroRegimeAgent failed: {e}")

        result.fed_funds_rate = fed_funds_rate
        result.cpi_yoy = cpi_yoy
        result.yield_curve_spread = yield_curve_spread
        result.unemployment_rate = unemployment_rate

        logger.info(
            f"MacroRegimeAgent complete — regime='{result.regime_label}' "
            f"fed={fed_funds_rate} cpi={cpi_yoy} spread={yield_curve_spread} u={unemployment_rate}"
        )
        return {"macro": result.model_dump()}
