import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, SupervisorReport
from src.exceptions import LLMStructuredOutputError


logger = logging.getLogger(__name__)

class _D(dict):
    def __getattr__(self, k): return self.get(k)
    def __bool__(self): return len(self) > 0

def _w(v): return _D(v) if isinstance(v, dict) else v

SUPERVISOR_PROMPT_EQUITY = """You are the Portfolio Manager at an investment research firm.
Your team has completed their research on {ticker}. Synthesise all outputs into a
single balanced investment memo. You only have access to your analysts' conclusions.

--- MACRO REGIME ---
Regime: {regime_label}
Fed Funds Rate: {fed_funds_rate}%
CPI YoY: {cpi_yoy}%
Yield Curve Spread: {yield_curve_spread} pts
Summary: {regime_summary}

--- FUNDAMENTALS ---
P/E Ratio: {pe_ratio}
EPS: {eps}
Revenue Growth (YoY): {revenue_growth}
Debt-to-Equity: {debt_to_equity}

--- VALUATION ---
Label: {valuation_label}
P/E vs Sector: {pe_vs_sector}
Intrinsic Value: {intrinsic_value}
Summary: {valuation_summary}

--- BULL CASE ---
Confidence: {bull_confidence}
Thesis: {bull_thesis}
Key Catalysts: {bull_catalysts}

--- BEAR CASE ---
Confidence: {bear_confidence}
Thesis: {bear_thesis}
Key Risks: {bear_risks}

--- RISK ---
Annualized Volatility: {volatility}%
Beta: {beta}
Risk Flags: {risk_flags}

--- SENTIMENT ---
Score: {sentiment_score} (-1.0 to 1.0)
Label: {sentiment_label}
Reasoning: {sentiment_reasoning}

--- WALL STREET CONSENSUS ---
Recommendation: {analyst_recommendation}
Mean Price Target: {target_price}
Number of Analysts: {num_analysts}

Your output:
- summary: 3-4 sentence executive summary synthesising all analyst inputs
- macro_context: 1-2 sentences on how the {regime_label} regime affects this asset
- bull_case: 2-3 sentences distilling the strongest bull arguments
- bear_case: 2-3 sentences distilling the strongest bear arguments
- recommendation: exactly one of 'Buy', 'Hold', or 'Sell'
  If bull confidence is High and analyst consensus is Buy, 
  lean Buy unless valuation is Overvalued or macro is Risk-Off.
  Avoid defaulting to Hold when evidence is mixed — make a call.
- confidence: 'High', 'Medium', or 'Low'
- key_metrics: list of 6-8 metrics with actual values e.g. ['P/E: 31.2', 'Beta: 1.4', 'Regime: Risk-Off Tightening']
- analyst_agreement: compare your recommendation to Wall Street consensus
"""

SUPERVISOR_PROMPT_CRYPTO = """You are the Portfolio Manager at an investment research firm.
Your team has completed their research on {ticker}. Synthesise all outputs into a
single balanced investment memo. You only have access to your analysts' conclusions.

--- MACRO REGIME ---
Regime: {regime_label}
Fed Funds Rate: {fed_funds_rate}%
CPI YoY: {cpi_yoy}%
Yield Curve Spread: {yield_curve_spread} pts
Summary: {regime_summary}

--- ON-CHAIN ---
Market Cap: ${market_cap}
24h Volume: ${volume_24h}
7-Day Price Change: {price_change_7d}%
Developer Activity Score: {developer_score}/100
Community Score: {community_score}/100
Network Health: {network_health}
Summary: {onchain_summary}

--- SENTIMENT ---
Score: {sentiment_score} (-1.0 to 1.0)
Label: {sentiment_label}
Reasoning: {sentiment_reasoning}

--- RISK ---
Annualized Volatility: {volatility}%
Beta: {beta}
Risk Flags: {risk_flags}

Your output:
- summary: 3-4 sentence executive summary synthesising all analyst inputs
- macro_context: 1-2 sentences on how the {regime_label} regime affects crypto markets
- bull_case: 2-3 sentences on the strongest bullish signals
- bear_case: 2-3 sentences on the strongest bearish signals
- recommendation: exactly one of 'Buy', 'Hold', or 'Sell'
- confidence: 'High', 'Medium', or 'Low'
- key_metrics: list of 6-8 metrics with actual values
- analyst_agreement: 'N/A — Wall Street analyst consensus not available for crypto assets'
"""


class SupervisorAgent:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(SupervisorReport)


    def analyze(self, state: FinanceState, **kwargs) -> dict:
        ticker = state["ticker"]
        asset_class = state.get("asset_class", "equity")
        logger.info(f"SupervisorAgent starting for {ticker} (asset_class={asset_class})...")

        macro = _w(state.get("macro"))
        risk = _w(state.get("risk"))
        sentiment = _w(state.get("sentiment"))
        consensus = _w(state.get("analyst_consensus"))

        def fmt(val, prefix="", suffix="") -> str:
            return f"{prefix}{val}{suffix}" if val is not None else "unavailable"

        def fmt_list(items: list) -> str:
            return "\n".join(f"  - {item}" for item in items) if items else "unavailable"

        shared = dict(
            ticker=ticker,
            regime_label=fmt(macro.regime_label if macro else None),
            regime_summary=fmt(macro.regime_summary if macro else None),
            fed_funds_rate=fmt(macro.fed_funds_rate if macro else None),
            cpi_yoy=fmt(macro.cpi_yoy if macro else None),
            yield_curve_spread=fmt(macro.yield_curve_spread if macro else None),
            volatility=fmt(f"{risk.volatility:.2f}" if risk and risk.volatility else None),
            beta=fmt(risk.beta if risk else None),
            risk_flags=fmt_list(risk.risk_flag if risk else []),
            sentiment_score=fmt(sentiment.sentiment_score if sentiment else None),
            sentiment_label=fmt(sentiment.sentiment_label if sentiment else None),
            sentiment_reasoning=fmt(sentiment.sentiment_reasoning if sentiment else None),
        )

        if asset_class == "crypto":
            onchain = _w(state.get("onchain"))
            prompt = SUPERVISOR_PROMPT_CRYPTO.format(
                **shared,
                market_cap=fmt(onchain.market_cap_usd if onchain else None),
                volume_24h=fmt(onchain.volume_24h_usd if onchain else None),
                price_change_7d=fmt(onchain.price_change_7d if onchain else None),
                developer_score=fmt(onchain.developer_activity_score if onchain else None),
                community_score=fmt(onchain.community_score if onchain else None),
                network_health=fmt(onchain.network_health if onchain else None),
                onchain_summary=fmt(onchain.onchain_summary if onchain else None),
            )
        else:
            fundamentals = _w(state.get("fundamentals"))
            bull = _w(state.get("bull_thesis"))
            bear = _w(state.get("bear_thesis"))
            valuation = _w(state.get("valuation"))

            prompt = SUPERVISOR_PROMPT_EQUITY.format(
                **shared,
                pe_ratio=fmt(fundamentals.PE_ratio if fundamentals else None),
                eps=fmt(fundamentals.EPS if fundamentals else None, suffix=" USD"),
                revenue_growth=fmt(fundamentals.revenue_growth if fundamentals else None),
                debt_to_equity=fmt(fundamentals.debt_to_equity if fundamentals else None),
                valuation_label=fmt(valuation.valuation_label if valuation else None),
                pe_vs_sector=fmt(valuation.pe_vs_sector if valuation else None),
                intrinsic_value=fmt(valuation.intrinsic_value_estimate if valuation else None),
                valuation_summary=fmt(valuation.valuation_summary if valuation else None),
                bull_confidence=fmt(bull.confidence if bull else None),
                bull_thesis=fmt(bull.thesis if bull else None),
                bull_catalysts=fmt_list(bull.key_catalysts if bull else []),
                bear_confidence=fmt(bear.confidence if bear else None),
                bear_thesis=fmt(bear.thesis if bear else None),
                bear_risks=fmt_list(bear.key_risks if bear else []),
                analyst_recommendation=fmt(consensus.recommendation if consensus else None),
                target_price=fmt(consensus.target_price if consensus else None, suffix=" USD"),
                num_analysts=fmt(consensus.num_analysts if consensus else None),
            )

        try:
            result: SupervisorReport = self.llm.invoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.error(f"SupervisorAgent LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"SupervisorAgent failed to produce structured output: {e}")

        logger.info(f"SupervisorAgent complete for {ticker} — recommendation={result.recommendation} confidence={result.confidence}")
        return {"supervisor_report": result.model_dump()}