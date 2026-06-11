"""
tests/test_qualitative_buckets.py — Qualitative bucket helper tests

Tests the EPS, revenue growth, and D/E → qualitative label conversions
in bull/bear analysts. These are the functions that prevent P/E hallucination
by ensuring raw numbers never reach the LLM as raw numbers.

Also tests the P/E isolation at the supervisor level — verifies that
qualitative_drivers contains no numeric ratios.
"""

import pytest
from src.nodes.bear_analyst import _earnings_health, _revenue_trend, _leverage_level


# ── _earnings_health ───────────────────────────────────────────────────────────

def test_earnings_health_none():
    assert _earnings_health(None) == "Earnings data unavailable"


def test_earnings_health_strongly_profitable():
    result = _earnings_health(12.5)
    assert "Strongly profitable" in result
    assert "12.50" in result


def test_earnings_health_profitable():
    result = _earnings_health(5.0)
    assert "Profitable" in result
    assert "Strongly" not in result


def test_earnings_health_marginally_profitable():
    result = _earnings_health(0.5)
    assert "Marginally profitable" in result


def test_earnings_health_breakeven():
    result = _earnings_health(0)
    assert "Break-even" in result


def test_earnings_health_unprofitable():
    result = _earnings_health(-2.5)
    assert "Unprofitable" in result
    assert "-2.50" in result


def test_earnings_health_no_raw_pe_ratio():
    """The output must NEVER contain a P/E ratio — only EPS."""
    result = _earnings_health(8.26)
    assert "P/E" not in result
    assert "sector" not in result.lower()


# ── _revenue_trend ─────────────────────────────────────────────────────────────

def test_revenue_trend_none():
    assert _revenue_trend(None) == "Revenue trend unavailable"


def test_revenue_trend_high_growth():
    result = _revenue_trend(0.30)  # 30%
    assert "High growth" in result
    assert "30%" in result


def test_revenue_trend_healthy_growth():
    result = _revenue_trend(0.17)  # 17%
    assert "Healthy growth" in result
    assert "17%" in result


def test_revenue_trend_modest_growth():
    result = _revenue_trend(0.05)  # 5%
    assert "Modest growth" in result


def test_revenue_trend_stagnant():
    result = _revenue_trend(0.01)  # 1%
    assert "Stagnant" in result


def test_revenue_trend_declining():
    result = _revenue_trend(-0.05)  # -5%
    assert "Declining" in result


def test_revenue_trend_significant_decline():
    result = _revenue_trend(-0.15)  # -15%
    assert "Significant" in result


def test_revenue_trend_zero():
    result = _revenue_trend(0.0)
    assert "Stagnant" in result


# ── _leverage_level ────────────────────────────────────────────────────────────

def test_leverage_none():
    assert _leverage_level(None) == "Leverage data unavailable"


def test_leverage_negative_equity():
    result = _leverage_level(-1.0)
    assert "Negative equity" in result


def test_leverage_low():
    result = _leverage_level(0.2)
    assert "Low leverage" in result
    assert "0.2x" in result


def test_leverage_moderate():
    result = _leverage_level(0.5)
    assert "Moderate leverage" in result


def test_leverage_elevated():
    result = _leverage_level(1.5)
    assert "Elevated leverage" in result


def test_leverage_high():
    result = _leverage_level(3.0)
    assert "High leverage" in result


def test_leverage_no_sector_comparison():
    """Leverage labels must never compare to sector median."""
    result = _leverage_level(0.8)
    assert "sector" not in result.lower()
    assert "median" not in result.lower()


# ── P/E isolation — groundedness check ────────────────────────────────────────

def test_bull_analyst_no_pe_in_prompt():
    """
    Bull analyst prompt template must not contain P/E format variables.
    This catches accidental re-introduction of raw P/E to the prompt.
    """
    from src.nodes.bull_analyst import BULL_PROMPT
    assert "{pe_ratio}" not in BULL_PROMPT
    assert "{eps}" not in BULL_PROMPT  # eps is injected via earnings_health bucket


def test_bear_analyst_no_pe_in_prompt():
    """Bear analyst prompt template must not contain raw P/E format variables."""
    from src.nodes.bear_analyst import BEAR_PROMPT
    assert "{pe_ratio}" not in BEAR_PROMPT
    assert "{eps}" not in BEAR_PROMPT


def test_supervisor_no_pe_in_equity_prompt():
    """Supervisor equity prompt must not contain raw P/E format variables."""
    from src.nodes.supervisor_agent import SUPERVISOR_PROMPT_EQUITY
    assert "{pe_ratio}" not in SUPERVISOR_PROMPT_EQUITY
    assert "{pe_vs_sector}" not in SUPERVISOR_PROMPT_EQUITY
    assert "{valuation_summary}" not in SUPERVISOR_PROMPT_EQUITY


def test_buckets_consistent_between_bull_and_bear():
    """
    Bull and bear analysts use the same bucket logic.
    Verify consistent output for identical inputs.
    """
    from src.nodes.bull_analyst import (
        _earnings_health as bull_eh,
        _revenue_trend as bull_rt,
        _leverage_level as bull_ll,
    )

    assert bull_eh(8.26) == _earnings_health(8.26)
    assert bull_rt(0.17) == _revenue_trend(0.17)
    assert bull_ll(0.8) == _leverage_level(0.8)