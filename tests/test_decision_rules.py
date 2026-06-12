"""
tests/test_decision_rules.py — Deterministic policy engine tests

Tests apply_equity_rules and apply_crypto_rules directly — no LLM involved.
Every rule is explicit and pinned here so changes to policy are always
deliberate and visible in the test diff.
"""

from src.decision.rules import apply_equity_rules, apply_crypto_rules, RuleVerdict


# ── Helper ─────────────────────────────────────────────────────────────────────

def equity(
    bull="Medium", bear="Medium",
    val="Fairly Valued", regime="Risk-On Tightening",
    sentiment=0.0, revenue="Modest growth (5% YoY)",
    consensus="Buy"
) -> RuleVerdict:
    return apply_equity_rules(
        bull_confidence=bull,
        bear_confidence=bear,
        valuation_label=val,
        regime_label=regime,
        sentiment_score=sentiment,
        revenue_trend=revenue,
        analyst_recommendation=consensus,
    )


def crypto(
    fg=50, health="Moderate", sentiment=0.0,
    p30=0.0, regime="Risk-On Tightening", dev=100.0
) -> RuleVerdict:
    return apply_crypto_rules(
        fear_greed_score=fg,
        network_health=health,
        sentiment_score=sentiment,
        price_change_30d=p30,
        regime_label=regime,
        developer_momentum=dev,
    )


# ── Tiebreaker rules (Rule 1) ──────────────────────────────────────────────────

def test_tiebreaker_overvalued_declining_revenue_sells():
    v = equity(bull="High", bear="High", val="Overvalued", revenue="Declining (-5% YoY)")
    assert v.recommendation == "Sell"
    assert v.confidence_floor == "High"
    assert "tiebreaker" in v.rule_fired


def test_tiebreaker_overvalued_stagnant_revenue_sells():
    v = equity(bull="High", bear="High", val="Overvalued", revenue="Stagnant (0% YoY)")
    assert v.recommendation == "Sell"


def test_tiebreaker_overvalued_modest_growth_holds():
    v = equity(bull="High", bear="High", val="Overvalued", revenue="Modest growth (5% YoY)")
    assert v.recommendation == "Hold"


def test_tiebreaker_overvalued_high_growth_holds():
    v = equity(bull="High", bear="High", val="Overvalued", revenue="High growth (30% YoY)")
    assert v.recommendation == "Hold"


def test_tiebreaker_fairly_valued_holds():
    v = equity(bull="High", bear="High", val="Fairly Valued")
    assert v.recommendation == "Hold"


def test_tiebreaker_undervalued_declining_holds():
    v = equity(bull="High", bear="High", val="Undervalued", revenue="Declining (-5% YoY)")
    assert v.recommendation == "Hold"
    assert "cheap for a reason" in v.rule_detail.lower()


def test_tiebreaker_undervalued_growth_buys():
    v = equity(bull="High", bear="High", val="Undervalued", revenue="High growth (30% YoY)")
    assert v.recommendation == "Buy"
    assert v.confidence_floor == "High"


# ── Strong directional confidence (Rule 2) ────────────────────────────────────

def test_strong_bear_weak_bull_sells():
    v = equity(bull="Low", bear="High")
    assert v.recommendation == "Sell"
    assert v.confidence_floor == "High"
    assert "strong_bear" in v.rule_fired


def test_strong_bull_weak_bear_buys():
    v = equity(bull="High", bear="Low")
    assert v.recommendation == "Buy"
    assert v.confidence_floor == "High"


def test_strong_bull_risk_off_holds():
    """Bull High but Risk-Off macro reduces Buy to Hold."""
    v = equity(bull="High", bear="Low", regime="Risk-Off Tightening")
    assert v.recommendation == "Hold"
    assert "risk_off" in v.rule_fired


# ── Explicit Sell conditions (Rule 3) ─────────────────────────────────────────

def test_explicit_sell_bear_high_overvalued_bearish():
    v = equity(bull="Medium", bear="High", val="Overvalued", sentiment=-0.7)
    assert v.recommendation == "Sell"
    assert "explicit_sell" in v.rule_fired


def test_explicit_sell_declining_revenue_overvalued_bearish():
    v = equity(bull="Low", bear="Medium", val="Overvalued",
               revenue="Declining (-5% YoY)", sentiment=-0.6)
    assert v.recommendation == "Sell"


def test_explicit_sell_risk_off_bear_bearish():
    v = equity(bull="Medium", bear="High", val="Fairly Valued",
               regime="Risk-Off Tightening", sentiment=-0.7)
    assert v.recommendation == "Sell"


# ── Explicit Buy conditions (Rule 4) ──────────────────────────────────────────

def test_explicit_buy_bull_undervalued_risk_on():
    v = equity(bull="High", bear="Low", val="Undervalued",
               regime="Risk-On Tightening", sentiment=0.3)
    assert v.recommendation == "Buy"


def test_explicit_buy_growth_consensus_bullish():
    v = equity(bull="Medium", bear="Low",
               revenue="High growth (30% YoY)",
               consensus="Buy", sentiment=0.6)
    assert v.recommendation == "Buy"


# ── Default Hold (Rule 6) ─────────────────────────────────────────────────────

def test_default_hold_mixed_signals():
    v = equity(bull="Medium", bear="Medium", val="Fairly Valued", sentiment=0.0)
    assert v.recommendation == "Hold"
    assert "default_hold" in v.rule_fired


# ── Analyst override flag ─────────────────────────────────────────────────────

def test_analyst_override_true_when_diverging():
    v = equity(bull="Low", bear="High", consensus="Buy")
    assert v.recommendation == "Sell"
    assert v.analyst_override is True


def test_analyst_override_false_when_agreeing():
    v = equity(bull="High", bear="Low", consensus="Buy",
               regime="Risk-On Tightening")
    assert v.recommendation == "Buy"
    assert v.analyst_override is False


# ── Input normalisation ────────────────────────────────────────────────────────

def test_confidence_casing_normalised():
    """HIGH, high, High all treated identically."""
    v1 = equity(bull="HIGH", bear="LOW")
    v2 = equity(bull="high", bear="low")
    v3 = equity(bull="High", bear="Low")
    assert v1.recommendation == v2.recommendation == v3.recommendation


def test_none_inputs_dont_crash():
    """None inputs should return a valid verdict, not raise."""
    v = equity(bull=None, bear=None, val=None, regime=None,
               sentiment=None, revenue=None, consensus=None)
    assert v.recommendation in ("Buy", "Hold", "Sell")
    assert v.confidence_floor in ("High", "Medium", "Low")


# ── Crypto rules ───────────────────────────────────────────────────────────────

def test_crypto_extreme_fear_healthy_risk_on_buys():
    v = crypto(fg=15, health="Strong", sentiment=0.0,
               regime="Risk-On Tightening")
    assert v.recommendation == "Buy"
    assert v.confidence_floor == "High"


def test_crypto_extreme_greed_overextended_risk_off_sells():
    v = crypto(fg=82, health="Strong", p30=35.0,
               regime="Risk-Off Tightening")
    assert v.recommendation == "Sell"


def test_crypto_weak_network_bearish_declining_sells():
    v = crypto(fg=40, health="Weak", sentiment=-0.7, p30=-15.0)
    assert v.recommendation == "Sell"


def test_crypto_neutral_fg_holds():
    v = crypto(fg=50, health="Moderate", sentiment=0.1)
    assert v.recommendation == "Hold"


def test_crypto_deep_pullback_strong_network_buys():
    v = crypto(fg=40, health="Strong", sentiment=0.1, p30=-25.0,
               regime="Risk-On Tightening")
    assert v.recommendation == "Buy"


def test_crypto_none_inputs_dont_crash():
    v = crypto(fg=None, health=None, sentiment=None,
               p30=None, regime=None, dev=None)
    assert v.recommendation in ("Buy", "Hold", "Sell")
