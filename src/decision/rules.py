"""
src/decision/rules.py — Deterministic investment decision framework

Pure Python policy engine. Takes structured agent outputs and returns
a deterministic recommendation with the rule that fired and a confidence
floor. The SupervisorAgent LLM receives this verdict and explains it —
it does not re-decide.

Design principles:
- Every rule is explicit, testable, and documented
- No LLM involved in this module — zero stochasticity
- The LLM's job is synthesis and narration, not policy execution
- If you change a rule, you change a test — drift is impossible

RuleVerdict fields:
    recommendation:   'Buy', 'Hold', or 'Sell'
    confidence_floor: minimum confidence the LLM should report
                      ('High', 'Medium', 'Low') — LLM may raise it
                      based on additional context, never lower it
    rule_fired:       human-readable name of the rule that decided
    rule_detail:      one-sentence explanation of why this rule fired
    analyst_override: True if recommendation diverges from analyst consensus
                      — signals the LLM to explicitly explain the divergence
"""

from dataclasses import dataclass
from typing import Literal


Recommendation = Literal["Buy", "Hold", "Sell"]
Confidence = Literal["High", "Medium", "Low"]


@dataclass
class RuleVerdict:
    recommendation: Recommendation
    confidence_floor: Confidence
    rule_fired: str
    rule_detail: str
    analyst_override: bool = False


# ── Input normalisation helpers ───────────────────────────────────────────────

def _conf(val: str | None) -> str:
    """Normalise confidence string to 'High', 'Medium', or 'Low'."""
    if not val:
        return "Low"
    v = val.strip().capitalize()
    return v if v in ("High", "Medium", "Low") else "Low"


def _val(val: str | None) -> str:
    """Normalise valuation label."""
    if not val:
        return "Fairly Valued"
    v = val.strip()
    if "over" in v.lower():
        return "Overvalued"
    if "under" in v.lower():
        return "Undervalued"
    return "Fairly Valued"


def _regime(val: str | None) -> str:
    """Normalise macro regime label."""
    if not val:
        return "Unknown"
    v = val.strip()
    # Known regimes
    if any(x in v for x in ["Risk-Off Tightening", "Stagflation"]):
        return "Risk-Off"
    if "Risk-On Easing" in v:
        return "Risk-On Easing"
    if "Risk-On Tightening" in v:
        return "Risk-On Tightening"
    if "Risk-On" in v:
        return "Risk-On"
    return "Unknown"


def _sentiment(score) -> str:
    """Classify sentiment score into label."""
    if score is None:
        return "Neutral"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "Neutral"
    if s <= -0.5:
        return "Bearish"
    if s >= 0.5:
        return "Bullish"
    return "Neutral"


def _revenue(label: str | None) -> str:
    """Classify revenue trend label into high/modest/stagnant/declining."""
    if not label:
        return "unknown"
    l = label.lower()
    if "high growth" in l or "healthy growth" in l:
        return "high"
    if "modest" in l:
        return "modest"
    if "stagnant" in l:
        return "stagnant"
    if "declining" in l or "significant decline" in l:
        return "declining"
    return "unknown"


# ── Equity decision framework ─────────────────────────────────────────────────

def apply_equity_rules(
    bull_confidence: str | None,
    bear_confidence: str | None,
    valuation_label: str | None,
    regime_label: str | None,
    sentiment_score,
    revenue_trend: str | None,
    analyst_recommendation: str | None,
) -> RuleVerdict:
    """
    Apply the equity decision framework deterministically.

    Rule priority (highest to lowest):
    1. Tiebreaker (Bear High AND Bull High)
    2. Strong directional confidence (Bear High + Bull Low, or Bull High + Bear Low)
    3. Explicit Sell conditions
    4. Explicit Buy conditions
    5. Valuation + macro overlay
    6. Default Hold

    All inputs are normalised before comparison — casing and whitespace are safe.
    """
    bull = _conf(bull_confidence)
    bear = _conf(bear_confidence)
    val  = _val(valuation_label)
    reg  = _regime(regime_label)
    sent = _sentiment(sentiment_score)
    rev  = _revenue(revenue_trend)
    consensus = (analyst_recommendation or "").strip().capitalize()

    def _override(rec: str) -> bool:
        return consensus not in ("", "unavailable") and consensus != rec

    # ── Rule 1: Tiebreaker (Bear High AND Bull High) ──────────────────────────
    if bull == "High" and bear == "High":
        if val == "Overvalued" and rev in ("stagnant", "declining"):
            return RuleVerdict(
                recommendation="Sell",
                confidence_floor="High",
                rule_fired="tiebreaker_overvalued_weak_growth",
                rule_detail="Both analysts High confidence, overvalued with stagnant/declining revenue — sell signal dominates",
                analyst_override=_override("Sell"),
            )
        if val == "Overvalued" and rev in ("modest", "high", "unknown"):
            return RuleVerdict(
                recommendation="Hold",
                confidence_floor="Medium",
                rule_fired="tiebreaker_overvalued_some_growth",
                rule_detail="Both analysts High confidence, overvalued but growth partially justifies premium",
                analyst_override=_override("Hold"),
            )
        if val == "Fairly Valued":
            return RuleVerdict(
                recommendation="Hold",
                confidence_floor="Medium",
                rule_fired="tiebreaker_fairly_valued",
                rule_detail="Both analysts High confidence, fairly valued — no dominant direction",
                analyst_override=_override("Hold"),
            )
        if val == "Undervalued" and rev == "declining":
            return RuleVerdict(
                recommendation="Hold",
                confidence_floor="Medium",
                rule_fired="tiebreaker_undervalued_declining",
                rule_detail="Both analysts High confidence, undervalued but revenue declining — cheap for a reason",
                analyst_override=_override("Hold"),
            )
        if val == "Undervalued" and rev in ("high", "modest", "stagnant", "unknown"):
            return RuleVerdict(
                recommendation="Buy",
                confidence_floor="High",
                rule_fired="tiebreaker_undervalued_growth",
                rule_detail="Both analysts High confidence, undervalued with growth — strong buy signal",
                analyst_override=_override("Buy"),
            )

    # ── Rule 2: Strong directional confidence ─────────────────────────────────
    if bear == "High" and bull == "Low":
        return RuleVerdict(
            recommendation="Sell",
            confidence_floor="High",
            rule_fired="strong_bear_weak_bull",
            rule_detail="Bear analyst High confidence, Bull analyst Low — clear directional signal",
            analyst_override=_override("Sell"),
        )

    if bull == "High" and bear == "Low":
        # Apply macro filter
        if reg == "Risk-Off":
            return RuleVerdict(
                recommendation="Hold",
                confidence_floor="Medium",
                rule_fired="strong_bull_risk_off_macro",
                rule_detail="Bull analyst High, but Risk-Off macro reduces Buy to Hold",
                analyst_override=_override("Hold"),
            )
        return RuleVerdict(
            recommendation="Buy",
            confidence_floor="High",
            rule_fired="strong_bull_weak_bear",
            rule_detail="Bull analyst High confidence, Bear analyst Low — clear directional signal",
            analyst_override=_override("Buy"),
        )

    # ── Rule 3: Explicit Sell conditions ──────────────────────────────────────
    if bear == "High" and val == "Overvalued" and sent == "Bearish":
        return RuleVerdict(
            recommendation="Sell",
            confidence_floor="High",
            rule_fired="explicit_sell_overvalued_bearish",
            rule_detail="Bear High + Overvalued + Bearish sentiment — three-signal sell alignment",
            analyst_override=_override("Sell"),
        )

    if rev == "declining" and val == "Overvalued" and sent == "Bearish":
        return RuleVerdict(
            recommendation="Sell",
            confidence_floor="High",
            rule_fired="explicit_sell_declining_revenue_overvalued",
            rule_detail="Declining revenue + Overvalued + Bearish sentiment — deteriorating fundamentals",
            analyst_override=_override("Sell"),
        )

    if reg == "Risk-Off" and bear == "High" and sent == "Bearish":
        return RuleVerdict(
            recommendation="Sell",
            confidence_floor="High",
            rule_fired="explicit_sell_risk_off_bear",
            rule_detail="Risk-Off macro + Bear High + Bearish sentiment — macro headwind amplifies bear case",
            analyst_override=_override("Sell"),
        )

    # ── Rule 4: Explicit Buy conditions ───────────────────────────────────────
    if bull == "High" and val in ("Undervalued", "Fairly Valued") and reg in ("Risk-On Easing", "Risk-On Tightening", "Risk-On"):
        return RuleVerdict(
            recommendation="Buy",
            confidence_floor="High",
            rule_fired="explicit_buy_bull_undervalued_risk_on",
            rule_detail="Bull High + Undervalued/Fair value + Risk-On macro — three-signal buy alignment",
            analyst_override=_override("Buy"),
        )

    if rev == "high" and consensus == "Buy" and sent == "Bullish":
        return RuleVerdict(
            recommendation="Buy",
            confidence_floor="Medium",
            rule_fired="explicit_buy_growth_consensus_sentiment",
            rule_detail="High revenue growth + analyst Buy consensus + Bullish sentiment alignment",
            analyst_override=False,  # agrees with consensus
        )

    # ── Rule 5: Valuation + macro overlay ────────────────────────────────────
    if val == "Overvalued" and bear == "High":
        return RuleVerdict(
            recommendation="Sell",
            confidence_floor="Medium",
            rule_fired="valuation_overlay_overvalued_bear",
            rule_detail="Overvalued + Bear High confidence — valuation risk confirmed by analyst",
            analyst_override=_override("Sell"),
        )

    if val == "Undervalued" and bull == "High":
        return RuleVerdict(
            recommendation="Buy",
            confidence_floor="Medium",
            rule_fired="valuation_overlay_undervalued_bull",
            rule_detail="Undervalued + Bull High confidence — value opportunity confirmed",
            analyst_override=_override("Buy"),
        )

    if reg == "Risk-On Easing" and val in ("Undervalued", "Fairly Valued"):
        return RuleVerdict(
            recommendation="Buy",
            confidence_floor="Low",
            rule_fired="macro_overlay_risk_on_easing",
            rule_detail="Risk-On Easing regime upgrades Hold to Buy when valuation supports it",
            analyst_override=_override("Buy"),
        )

    if reg == "Risk-Off" and val == "Overvalued":
        return RuleVerdict(
            recommendation="Sell",
            confidence_floor="Medium",
            rule_fired="macro_overlay_risk_off_overvalued",
            rule_detail="Risk-Off macro + Overvalued — double compression risk",
            analyst_override=_override("Sell"),
        )

    # ── Rule 6: Default Hold ──────────────────────────────────────────────────
    return RuleVerdict(
        recommendation="Hold",
        confidence_floor="Low",
        rule_fired="default_hold_mixed_signals",
        rule_detail="No dominant signal direction — signals genuinely conflict",
        analyst_override=_override("Hold"),
    )


# ── Crypto decision framework ─────────────────────────────────────────────────

def apply_crypto_rules(
    fear_greed_score: int | None,
    network_health: str | None,
    sentiment_score,
    price_change_30d: float | None,
    regime_label: str | None,
    developer_momentum: float | None,
) -> RuleVerdict:
    """
    Apply the crypto decision framework deterministically.
    Fear & Greed is the primary signal for crypto.
    """
    fg = int(fear_greed_score) if fear_greed_score is not None else 50
    reg = _regime(regime_label)
    sent = _sentiment(sentiment_score)
    health = (network_health or "").strip().capitalize()
    p30 = float(price_change_30d) if price_change_30d is not None else 0.0
    dev = float(developer_momentum) if developer_momentum is not None else 100.0

    # ── Explicit Sell conditions ───────────────────────────────────────────────
    if fg > 75 and p30 > 30 and reg == "Risk-Off":
        return RuleVerdict(
            recommendation="Sell",
            confidence_floor="High",
            rule_fired="crypto_extreme_greed_overextended_risk_off",
            rule_detail="Extreme Greed + 30d gain >30% + Risk-Off macro — overheated with headwind",
        )

    if health.lower() == "weak" and sent == "Bearish" and p30 < 0:
        return RuleVerdict(
            recommendation="Sell",
            confidence_floor="High",
            rule_fired="crypto_weak_network_bearish_declining",
            rule_detail="Weak network health + bearish sentiment + negative price momentum — structural breakdown",
        )

    if dev < 50 and sent == "Bearish":
        return RuleVerdict(
            recommendation="Sell",
            confidence_floor="Medium",
            rule_fired="crypto_declining_dev_bearish",
            rule_detail="Developer activity below 50% of average + bearish sentiment — abandonment risk",
        )

    # ── Explicit Buy conditions ────────────────────────────────────────────────
    if fg < 25 and health.lower() in ("strong", "moderate") and reg in ("Risk-On Easing", "Risk-On Tightening", "Risk-On"):
        return RuleVerdict(
            recommendation="Buy",
            confidence_floor="High",
            rule_fired="crypto_extreme_fear_healthy_network_risk_on",
            rule_detail="Extreme Fear + healthy network + Risk-On macro — classic contrarian buy setup",
        )

    if 25 <= fg <= 45 and dev > 110 and reg in ("Risk-On Easing", "Risk-On Tightening", "Risk-On"):
        return RuleVerdict(
            recommendation="Buy",
            confidence_floor="Medium",
            rule_fired="crypto_fear_elevated_dev_risk_on",
            rule_detail="Fear zone + elevated developer activity + supportive macro",
        )

    if p30 < -20 and health.lower() == "strong" and sent != "Bearish":
        return RuleVerdict(
            recommendation="Buy",
            confidence_floor="Medium",
            rule_fired="crypto_deep_pullback_strong_network",
            rule_detail="30d decline >20% with strong network health — dip in a healthy protocol",
        )

    # ── Default Hold ───────────────────────────────────────────────────────────
    return RuleVerdict(
        recommendation="Hold",
        confidence_floor="Low",
        rule_fired="crypto_default_hold_neutral",
        rule_detail="Fear & Greed neutral and no clear fundamental signal",
    )
