import logging
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState, SupervisorReport
from src.exceptions import LLMStructuredOutputError
from src.decision.rules import apply_equity_rules, apply_crypto_rules


logger = logging.getLogger(__name__)

class _D(dict):
    def __getattr__(self, k): return self.get(k)
    def __bool__(self): return len(self) > 0

def _w(v): return _D(v) if isinstance(v, dict) else v

SUPERVISOR_EQUITY_REFERENCE = """
INVESTMENT PRINCIPLES (reason FROM these, do not follow as a checklist):
1. Valuation is relative. PEG = P/E / revenue_growth_pct. PEG >2.5 = overvalued vs growth.
2. Negative EPS makes P/E meaningless. A company losing money cannot be valued on earnings multiples.
3. Macro regime sets the ceiling. Risk-Off Tightening compresses what P/E the market will pay by 15-20%.
4. Sentiment reflects NOW. Fundamentals reflect LATER. When they diverge, fundamentals win over 6-12 months.
5. High conviction both directions = genuine uncertainty. Explain which scenario is more likely given the preponderance of evidence.
6. Revenue growth trend matters more than level. Inflecting from -5% to +5% is valuable. Decelerating from +20% to +5% is a warning.
7. Analyst consensus systematically lags reality. When you diverge, explain why clearly.
8. The best Sell calls cite specific numbers. "P/E 64x with negative EPS and 7% growth = market pricing in a turnaround not yet happening."
9. Hold is appropriate when signals genuinely conflict with no dominant direction — not as a default.
10. Never let a single bullish headline override fundamental deterioration.
11. SPECIALIST TRUST: The ValuationAnalyst is the designated specialist for P/E vs sector comparisons.
    Trust its valuation_label over P/E claims made by BullAnalyst or BearAnalyst.
    Those agents assess direction and risks — valuation is not their specialty.
    If BearAnalyst says "overvalued" but ValuationAnalyst says "Fairly Valued", defer to ValuationAnalyst.
"""

SUPERVISOR_PROMPT_EQUITY = """You are the Portfolio Manager at an elite investment research firm.
Your team has submitted their research on {ticker}. The investment decision has already been made
by the policy engine — your job is to write the investment memo explaining it.

CRITICAL DATA BOUNDARY — READ FIRST:
You have NOT been given P/E ratio data, sector median P/E, or PEG ratio data.
Do NOT invent, recall, or estimate any P/E ratio, sector multiple, or sector comparison.
The ValuationAnalyst's label (Overvalued/Fairly Valued/Undervalued) is the ONLY valuation signal you have.

POLICY DECISION (DO NOT OVERRIDE):
Recommendation: {policy_recommendation}
Confidence Floor: {policy_confidence_floor}
Rule Fired: {policy_rule_fired}
Rule Detail: {policy_rule_detail}
Analyst Override: {policy_analyst_override}

YOUR ROLE — Explain, don't decide:
1. Write a memo that explains WHY the policy made this call using the research below
2. Your recommendation field MUST match the policy recommendation exactly
3. Your confidence field must be >= the confidence floor (you may raise it, never lower it)
4. If analyst_override is True, explain explicitly why this diverges from Wall Street consensus
5. Reference the specific signals that drove the rule — make it clear and defensible

{supervisor_equity_reference}
--- MACRO REGIME ---
Regime: {regime_label}
Fed Funds: {fed_funds_rate}% | CPI: {cpi_yoy}% | Yield Curve: {yield_curve_spread} pts
Summary: {regime_summary}

--- FUNDAMENTALS (qualitative) ---
Earnings: {earnings_health} | Revenue: {revenue_trend} | Leverage: {leverage_level}

--- VALUATION ---
Label: {valuation_label} | Qualitative Assessment: {valuation_drivers}

--- BULL CASE ---
Confidence: {bull_confidence}
Thesis: {bull_thesis}
Catalysts: {bull_catalysts}

--- BEAR CASE ---
Confidence: {bear_confidence}
Thesis: {bear_thesis}
Risks: {bear_risks}

--- RISK ---
Volatility: {volatility}% | Beta: {beta}
Risk Flags: {risk_flags}

--- SENTIMENT ---
Score: {sentiment_score} | Label: {sentiment_label}
Reasoning: {sentiment_reasoning}

--- WALL STREET CONSENSUS ---
Recommendation: {analyst_recommendation} | Target: {target_price} | Analysts: {num_analysts}

Your output:
- summary: 3-4 sentences. Explain which signals drove the policy decision. Reference the rule detail.
- macro_context: 1-2 sentences on how {regime_label} specifically affects this asset class and ticker.
- bull_case: 2-3 sentences — the strongest bull arguments with the actual metrics.
- bear_case: 2-3 sentences — the strongest bear arguments with the actual metrics.
- recommendation: EXACTLY '{policy_recommendation}' — set by policy, do not change it
- confidence: '{policy_confidence_floor}' or higher — never lower than the floor
- key_metrics: list of 6-8 metrics with actual values. Example: ['Earnings: Profitable ($8.26 EPS)', 'Revenue: Healthy growth (17%)', 'Regime: Risk-Off Tightening', 'Bear Confidence: High', 'Valuation: Overvalued', 'Sentiment: -0.65 (Bearish)', 'Volatility: 23.6%', 'Beta: 1.09']
- analyst_agreement: one sentence comparing your call to Wall Street consensus and explaining any divergence
"""

SUPERVISOR_CRYPTO_REFERENCE = """
FEAR & GREED FRAMEWORK:
0-25 Extreme Fear: Contrarian buy zone — historically capitulation. Bullish IF network health intact.
26-45 Fear: Cautious — need fundamental confirmation to buy.
46-55 Neutral: Let other signals dominate.
56-75 Greed: Momentum positive, watch for overextension.
76-100 Extreme Greed: Contrarian sell — market overheated, correction risk elevated.

BTC DOMINANCE:
Rising: Defensive positioning within crypto — altcoins underperform BTC.
Falling: Risk-on within crypto — altcoin season likely.

DEVELOPER ACTIVITY:
>120% of avg: Elevated — protocol upgrade likely. Bullish signal.
80-120%: Normal healthy development.
<50%: Concerning — possible abandonment risk.

PRINCIPLES:
- Extreme Fear alone does NOT guarantee recovery. Prices can fall further.
- Extreme Fear + Strong network health + Risk-On macro = Buy.
- Extreme Fear + Weak network health = Hold (cheap for a reason).
- Extreme Greed + 30-day gain >30% = Sell signal regardless of narrative.
"""

SUPERVISOR_PROMPT_CRYPTO = """You are the Portfolio Manager at an elite investment research firm specializing in digital assets.
Your team has submitted their research on {ticker}. Make a DECISIVE recommendation — Hold is not the default.
Crypto markets are sentiment-driven and volatile. Fear and greed are data points, not noise.

DECISION FRAMEWORK — apply this before writing:

STEP 1 — Fear & Greed is your primary market structure signal:
- Extreme Fear (0-25): Historically strong contrarian buy zone. Weight bullish unless fundamentals collapsing.
- Fear (25-45): Cautious. Need fundamental confirmation to Buy.
- Neutral (45-55): Sentiment alone doesn't drive decision.
- Greed (55-75): Momentum positive but watch for overextension.
- Extreme Greed (75-100): Contrarian Sell signal. Market overheated. Reduce positions.

STEP 2 — Price momentum check:
- 30-day decline >25% in Extreme Fear = potential capitulation → Buy if network health Strong/Moderate
- 30-day gain >40% in Extreme Greed = overextension → Sell or Hold
- Price trending down + developer activity trending down = structural breakdown → Sell

STEP 3 — Network health weighting:
- Strong network health: Hold floor → upgrades to Buy on any Fear signal
- Moderate: neutral — price and sentiment dominate
- Weak: Buy floor → downgrades to Hold on any positive signal. Sell on negative.

EXPLICIT SELL CONDITIONS:
✓ Extreme Greed (F&G >75) + 30-day gain >30% + Risk-Off macro
✓ Network health Weak + sentiment bearish + 30-day decline AND no recovery signal
✓ Developer activity declining + community score declining + bearish sentiment

EXPLICIT BUY CONDITIONS:
✓ Extreme Fear (F&G <25) + Network health Strong/Moderate + macro Risk-On
✓ Fear (F&G 25-45) + developer activity elevated (>110% of avg) + macro supportive
✓ 30-day decline >20% + network health Strong + sentiment showing reversal

DEFAULT TO HOLD when Fear & Greed is neutral and no clear fundamental signal.

{supervisor_crypto_reference}
--- MACRO REGIME ---
Regime: {regime_label} | Summary: {regime_summary}
Fed Funds: {fed_funds_rate}% | CPI: {cpi_yoy}% | Yield Curve: {yield_curve_spread} pts

--- MARKET STRUCTURE ---
Fear & Greed: {fear_greed}
BTC Dominance: {btc_dominance}%
Network Health: {network_health}
Developer Momentum: {github_momentum}

--- ON-CHAIN DATA ---
Market Cap: ${market_cap} | 24h Volume: ${volume_24h}
7-Day Price Change: {price_change_7d}% | 30-Day Price Change: {price_change_30d}%
ATH Distance: {ath_change}%
On-Chain Summary: {onchain_summary}

--- SENTIMENT ---
Score: {sentiment_score} | Label: {sentiment_label}
Reasoning: {sentiment_reasoning}

--- RISK ---
Volatility: {volatility}% | Risk Flags: {risk_flags}

Your output:
- summary: 3-4 sentences. State which signals dominated. Reference Fear & Greed explicitly if below 30 or above 70.
- macro_context: 1-2 sentences on how {regime_label} affects crypto markets specifically.
- bull_case: 2-3 sentences — strongest bullish signals with actual metrics.
- bear_case: 2-3 sentences — strongest bearish signals with actual metrics.
- recommendation: EXACTLY one of 'Buy', 'Hold', or 'Sell' — based on the framework above
- confidence: 'High' if 3+ signals align, 'Medium' if majority align, 'Low' if genuinely mixed
- key_metrics: list of 6-8 metrics with actual values. Always include Fear & Greed, BTC Dominance, 30d price change, network health.
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
            crypto_signals = state["raw_data"].get("crypto_signals") or {}
            fear_greed_val = crypto_signals.get("fear_greed_value")
            fear_greed_label = crypto_signals.get("fear_greed_label")
            fg_str = f"{fear_greed_val}/100 ({fear_greed_label})" if fear_greed_val is not None else "unavailable"
            btc_dom = crypto_signals.get("btc_dominance_pct")
            gh_mom = crypto_signals.get("github_momentum_pct")

            # Apply deterministic crypto policy rules
            crypto_verdict = apply_crypto_rules(
                fear_greed_score=fear_greed_val,
                network_health=onchain.network_health if onchain else None,
                sentiment_score=sentiment.sentiment_score if sentiment else None,
                price_change_30d=crypto_signals.get("price_change_30d"),
                regime_label=macro.regime_label if macro else None,
                developer_momentum=gh_mom,
            )
            logger.info(f"PolicyEngine [{ticker}] crypto → {crypto_verdict.recommendation} ({crypto_verdict.confidence_floor}) rule={crypto_verdict.rule_fired}")

            prompt = SUPERVISOR_PROMPT_CRYPTO.format(
                **shared,
                supervisor_crypto_reference=SUPERVISOR_CRYPTO_REFERENCE,
                fear_greed=fg_str,
                btc_dominance=fmt(round(btc_dom, 1) if btc_dom else None),
                github_momentum=fmt(f"{gh_mom:.0f}% of 52-week avg" if gh_mom else None),
                market_cap=fmt(onchain.market_cap_usd if onchain else None),
                volume_24h=fmt(onchain.volume_24h_usd if onchain else None),
                price_change_7d=fmt(onchain.price_change_7d if onchain else None),
                price_change_30d=fmt(crypto_signals.get("price_change_30d")),
                ath_change=fmt(crypto_signals.get("ath_change_pct")),
                network_health=fmt(onchain.network_health if onchain else None),
                onchain_summary=fmt(onchain.onchain_summary if onchain else None),
            )
        else:
            fundamentals = _w(state.get("fundamentals"))
            bull = _w(state.get("bull_thesis"))
            bear = _w(state.get("bear_thesis"))
            valuation = _w(state.get("valuation"))

            # Convert raw numbers → qualitative buckets (same as bull/bear analysts)
            # Supervisor never sees raw P/E, EPS, or D/E — only qualitative labels
            def _earnings_health(eps) -> str:
                if eps is None: return "unavailable"
                eps = float(eps)
                if eps > 10: return f"Strongly profitable (${eps:.2f} EPS)"
                if eps > 3:  return f"Profitable (${eps:.2f} EPS)"
                if eps > 0:  return f"Marginally profitable (${eps:.2f} EPS)"
                if eps == 0: return "Break-even"
                return f"Unprofitable (${eps:.2f} EPS)"

            def _revenue_trend(g) -> str:
                if g is None: return "unavailable"
                g = float(g) * 100
                if g > 25:  return f"High growth ({g:.0f}% YoY)"
                if g > 10:  return f"Healthy growth ({g:.0f}% YoY)"
                if g > 3:   return f"Modest growth ({g:.0f}% YoY)"
                if g >= 0:  return f"Stagnant ({g:.0f}% YoY)"
                if g > -10: return f"Declining ({g:.0f}% YoY)"
                return f"Significant decline ({g:.0f}% YoY)"

            def _leverage_level(de) -> str:
                if de is None: return "unavailable"
                de = float(de)
                if de < 0:   return "Negative equity"
                if de < 0.3: return f"Low leverage ({de:.1f}x D/E)"
                if de < 0.8: return f"Moderate leverage ({de:.1f}x D/E)"
                if de < 2.0: return f"Elevated leverage ({de:.1f}x D/E)"
                return f"High leverage ({de:.1f}x D/E)"

            revenue_trend_label = _revenue_trend(fundamentals.revenue_growth if fundamentals else None)

            # Apply deterministic policy rules — LLM explains the verdict, doesn't compute it
            verdict = apply_equity_rules(
                bull_confidence=bull.confidence if bull else None,
                bear_confidence=bear.confidence if bear else None,
                valuation_label=valuation.valuation_label if valuation else None,
                regime_label=macro.regime_label if macro else None,
                sentiment_score=sentiment.sentiment_score if sentiment else None,
                revenue_trend=revenue_trend_label,
                analyst_recommendation=consensus.recommendation if consensus else None,
            )
            logger.info(f"PolicyEngine [{ticker}] → {verdict.recommendation} ({verdict.confidence_floor}) rule={verdict.rule_fired}")

            prompt = SUPERVISOR_PROMPT_EQUITY.format(
                **shared,
                supervisor_equity_reference=SUPERVISOR_EQUITY_REFERENCE,
                policy_recommendation=verdict.recommendation,
                policy_confidence_floor=verdict.confidence_floor,
                policy_rule_fired=verdict.rule_fired,
                policy_rule_detail=verdict.rule_detail,
                policy_analyst_override=verdict.analyst_override,
                earnings_health=_earnings_health(fundamentals.EPS if fundamentals else None),
                revenue_trend=revenue_trend_label,
                leverage_level=_leverage_level(fundamentals.debt_to_equity if fundamentals else None),
                valuation_label=fmt(valuation.valuation_label if valuation else None),
                valuation_drivers=fmt(valuation.qualitative_drivers if valuation else None),
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

        # ── Schema enforcement — stamp verdict fields, overwrite recommendation ──
        # The LLM may have populated recommendation correctly or tried to override.
        # We enforce the policy verdict either way and log any mismatch as telemetry.
        if asset_class == "crypto":
            active_verdict = crypto_verdict
        else:
            active_verdict = verdict

        llm_matched = result.recommendation == active_verdict.recommendation
        if not llm_matched:
            logger.warning(
                f"SupervisorAgent [{ticker}] LLM recommendation '{result.recommendation}' "
                f"overridden by policy '{active_verdict.recommendation}' "
                f"(rule={active_verdict.rule_fired})"
            )

        # Overwrite with policy values — this is the enforcement, not the instruction
        result.recommendation        = active_verdict.recommendation
        result.policy_recommendation = active_verdict.recommendation
        result.policy_confidence_floor = active_verdict.confidence_floor
        result.policy_rule_fired     = active_verdict.rule_fired
        result.policy_analyst_override = active_verdict.analyst_override
        result.llm_recommendation_matched = llm_matched

        logger.info(
            f"SupervisorAgent complete for {ticker} — "
            f"recommendation={result.recommendation} "
            f"confidence={result.confidence} "
            f"floor={active_verdict.confidence_floor} "
            f"llm_matched={llm_matched}"
        )
        return {"supervisor_report": result.model_dump()}
