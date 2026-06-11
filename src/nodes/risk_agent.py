import logging
import pandas as pd
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, RiskData
from src.exceptions import LLMStructuredOutputError


logger = logging.getLogger(__name__)

class _D(dict):
    def __getattr__(self, k): return self.get(k)
    def __bool__(self): return len(self) > 0

def _w(v): return _D(v) if isinstance(v, dict) else v

RISK_PROMPT = """You are the Risk Manager at an investment research firm.
Identify specific, quantitative risk flags for {ticker} based on its volatility,
beta, and the current macro environment. Be precise — name the risk, not generically.

--- QUANTITATIVE METRICS ---
Annualized Volatility: {volatility}%
Beta (vs S&P 500): {beta}

Volatility context:
- < 15%: low volatility, stable asset
- 15-30%: moderate volatility, typical for large-cap equities
- 30-50%: elevated volatility, significant price swings
- > 50%: high volatility — typical for crypto, small-caps, distressed assets

Beta context:
- < 0: moves inverse to market
- 0-0.5: low correlation, defensive
- 0.5-1.0: moderate market sensitivity
- 1.0-1.5: moves with market, slightly amplified
- > 1.5: high market sensitivity — amplifies both gains and losses

--- MACRO CONTEXT ---
Regime: {regime_label}
Fed Funds Rate: {fed_funds_rate}%
Yield Curve Spread (10Y - 2Y): {yield_curve_spread} pts
CPI YoY: {cpi_yoy}%

Return:
- volatility: the exact float value provided above (do not recalculate)
- beta: the exact float value provided above (do not recalculate)
- risk_flag: a list of 3-5 specific risk flags grounded in the metrics above
"""


class RiskDataAgent:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(RiskData)


    def analyze(self, state: FinanceState, **kwargs) -> dict:
        ticker = state["ticker"]
        logger.info(f"RiskDataAgent starting for {ticker}...")

        history_df = pd.DataFrame(state["raw_data"]["history"])
        daily_returns = history_df["Close"].pct_change()
        volatility = volatility = float(round(daily_returns.std() * (252 ** 0.5) * 100, 2))

        info = state["raw_data"]["info"]
        beta = float(info.get("beta")) if info.get("beta") is not None else None
        macro = _w(state.get("macro"))

        def fmt(val, suffix="") -> str:
            return f"{val}{suffix}" if val is not None else "unavailable"

        prompt = RISK_PROMPT.format(
            ticker=ticker,
            volatility=fmt(volatility),
            beta=fmt(beta),
            regime_label=fmt(macro.regime_label if macro else None),
            fed_funds_rate=fmt(macro.fed_funds_rate if macro else None),
            yield_curve_spread=fmt(macro.yield_curve_spread if macro else None),
            cpi_yoy=fmt(macro.cpi_yoy if macro else None),
        )

        try:
            result: RiskData = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"RiskDataAgent LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"RiskDataAgent failed to produce structured output: {e}")

        result.volatility = volatility
        result.beta = beta

        logger.info(f"RiskDataAgent complete for {ticker} — volatility={volatility}% beta={beta} flags={len(result.risk_flag)}")
        return {"risk": result.model_dump()}
