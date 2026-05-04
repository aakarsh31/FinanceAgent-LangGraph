import logging
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

MACRO_PROMPT = """You are the MacroRegime Analyst at an investment research firm.
Interpret the current macroeconomic environment using FRED data and classify it
into a regime label that will inform every other analyst's thesis.

Indicators:
- Fed Funds Rate: current interest rate set by the Federal Reserve
- CPI YoY: year-over-year inflation — above 3% is elevated, below 2% is low
- Yield Curve Spread (10Y - 2Y): negative = inverted = recession signal
- Unemployment Rate: above 5% is elevated, below 4% is tight labour market

Your regime_label MUST be exactly one of:
{valid_labels}

Definitions:
- Risk-On Easing: rates falling or low, inflation cooling, yield curve normalising
- Risk-On Tightening: rates high but economy resilient, markets still performing
- Risk-Off Easing: rates being cut in response to economic weakness
- Risk-Off Tightening: rates high, growth slowing, yield curve inverted — high recession risk
- Stagflation: inflation elevated AND growth slowing simultaneously
- Early Recovery: rates low, CPI stable, unemployment falling

Current FRED Data:
- Fed Funds Rate: {fed_funds_rate}%
- CPI YoY: {cpi_yoy}%
- Yield Curve Spread (10Y - 2Y): {yield_curve_spread} percentage points
- Unemployment Rate: {unemployment_rate}%

Assign exactly one regime_label and write a 2-3 sentence regime_summary explaining
the current environment and its implication for equity and crypto markets.
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
            valid_labels="\n".join(f"  - {l}" for l in VALID_REGIME_LABELS),
            fed_funds_rate=fmt(fed_funds_rate),
            cpi_yoy=fmt(cpi_yoy),
            yield_curve_spread=fmt(yield_curve_spread),
            unemployment_rate=fmt(unemployment_rate),
        )

        try:
            result: MacroRegimeData = self.llm.invoke(prompt)
        except Exception as e:
            logger.error(f"MacroRegimeAgent LLM call failed: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"MacroRegimeAgent failed to produce structured output: {e}")

        result.fed_funds_rate = fed_funds_rate
        result.cpi_yoy = cpi_yoy
        result.yield_curve_spread = yield_curve_spread
        result.unemployment_rate = unemployment_rate

        logger.info(f"MacroRegimeAgent complete — regime='{result.regime_label}' fed={fed_funds_rate} cpi={cpi_yoy} spread={yield_curve_spread} u={unemployment_rate}")
        return {"macro": result.model_dump()}