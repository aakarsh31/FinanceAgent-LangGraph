"""
tests/test_eval_hit.py — _determine_hit and _fetch_return edge case tests

Note: _determine_hit now defaults to SPY-relative (relative=True).
Tests explicitly pass relative=False for absolute-direction tests.
"""

from evaluation.eval_job import _determine_hit, _fetch_return, _binomial_ci


# ── _determine_hit — absolute (relative=False) ────────────────────────────────

def test_buy_positive_return_is_hit():
    assert _determine_hit("Buy", 0.05, relative=False) is True

def test_buy_negative_return_is_miss():
    assert _determine_hit("Buy", -0.03, relative=False) is False

def test_sell_negative_return_is_hit():
    assert _determine_hit("Sell", -0.05, relative=False) is True

def test_sell_positive_return_is_miss():
    assert _determine_hit("Sell", 0.03, relative=False) is False

def test_hold_returns_none():
    assert _determine_hit("Hold", 0.10, relative=False) is None
    assert _determine_hit("Hold", -0.10, relative=False) is None
    assert _determine_hit("Hold", 0.0, relative=False) is None

def test_buy_exactly_zero_is_miss():
    assert _determine_hit("Buy", 0.0, relative=False) is False

def test_sell_exactly_zero_is_miss():
    assert _determine_hit("Sell", 0.0, relative=False) is False

def test_buy_very_small_positive_is_hit():
    assert _determine_hit("Buy", 0.000001, relative=False) is True

def test_sell_very_small_negative_is_hit():
    assert _determine_hit("Sell", -0.000001, relative=False) is True

def test_unknown_recommendation_returns_none():
    assert _determine_hit("Strong Buy", 0.10, relative=False) is None
    assert _determine_hit("", 0.10, relative=False) is None
    assert _determine_hit("HOLD", 0.10, relative=False) is None


# ── _determine_hit — SPY-relative (default) ────────────────────────────────────

def test_buy_beats_spy_is_hit():
    assert _determine_hit("Buy", 0.05, spy_return_30d=0.02) is True

def test_buy_lags_spy_is_miss():
    assert _determine_hit("Buy", 0.02, spy_return_30d=0.05) is False

def test_sell_lags_spy_is_hit():
    assert _determine_hit("Sell", -0.08, spy_return_30d=-0.03) is True

def test_sell_beats_spy_is_miss():
    assert _determine_hit("Sell", 0.05, spy_return_30d=0.02) is False

def test_relative_fallback_without_spy():
    assert _determine_hit("Buy", 0.05, spy_return_30d=None) is True
    assert _determine_hit("Buy", -0.05, spy_return_30d=None) is False


# ── _binomial_ci ──────────────────────────────────────────────────────────────

def test_binomial_ci_zero_n():
    lo, hi = _binomial_ci(0, 0)
    assert lo == 0.0 and hi == 1.0

def test_binomial_ci_all_hits():
    lo, hi = _binomial_ci(10, 10)
    assert lo > 0.6 and hi == 1.0

def test_binomial_ci_half_hits():
    lo, hi = _binomial_ci(50, 100)
    assert 0.40 < lo < 0.50
    assert 0.50 < hi < 0.60

def test_binomial_ci_bounds_valid():
    for hits, n in [(0, 5), (3, 10), (7, 10), (10, 10), (50, 200)]:
        lo, hi = _binomial_ci(hits, n)
        assert 0.0 <= lo <= hi <= 1.0


# ── _fetch_return edge cases ──────────────────────────────────────────────────

def test_fetch_return_zero_start_price_guard():
    import inspect
    source = inspect.getsource(_fetch_return)
    assert "price_start == 0" in source

def test_fetch_return_returns_float_or_none():
    from datetime import datetime, timezone, timedelta
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=35)
    result = _fetch_return("INVALID_TICKER_XYZ_999", start, end)
    assert result is None or isinstance(result, float)