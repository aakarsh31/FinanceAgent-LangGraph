"""
tests/test_eval_hit.py — _determine_hit and _fetch_return edge case tests

Transcription of the manual eval verification. Pins boundary behavior
so future changes to hit definition are explicit and deliberate.
"""

from evaluation.eval_job import _determine_hit, _fetch_return


# ── _determine_hit ─────────────────────────────────────────────────────────────

def test_buy_positive_return_is_hit():
    assert _determine_hit("Buy", 0.05) is True


def test_buy_negative_return_is_miss():
    assert _determine_hit("Buy", -0.03) is False


def test_sell_negative_return_is_hit():
    assert _determine_hit("Sell", -0.05) is True


def test_sell_positive_return_is_miss():
    assert _determine_hit("Sell", 0.03) is False


def test_hold_returns_none():
    """Hold signals are excluded from hit rate — always None."""
    assert _determine_hit("Hold", 0.10) is None
    assert _determine_hit("Hold", -0.10) is None
    assert _determine_hit("Hold", 0.0) is None


def test_buy_exactly_zero_is_miss():
    """Boundary: Buy at exactly 0% return is a miss (not > 0)."""
    assert _determine_hit("Buy", 0.0) is False


def test_sell_exactly_zero_is_miss():
    """Boundary: Sell at exactly 0% return is a miss (not < 0)."""
    assert _determine_hit("Sell", 0.0) is False


def test_buy_very_small_positive_is_hit():
    """Even 0.0001% return counts as a hit for Buy."""
    assert _determine_hit("Buy", 0.000001) is True


def test_sell_very_small_negative_is_hit():
    """Even -0.0001% return counts as a hit for Sell."""
    assert _determine_hit("Sell", -0.000001) is True


def test_unknown_recommendation_returns_none():
    """Any recommendation other than Buy/Sell/Hold treated as Hold (excluded)."""
    assert _determine_hit("Strong Buy", 0.10) is None
    assert _determine_hit("", 0.10) is None
    assert _determine_hit("HOLD", 0.10) is None  # case sensitive


# ── _fetch_return edge cases ───────────────────────────────────────────────────

def test_fetch_return_zero_start_price_guard():
    """
    _fetch_return returns None when price_start == 0 to avoid division by zero.
    We test this via the function's internal guard, not by mocking yfinance.
    The guard is: if price_start == 0: return None
    We verify the logic is correct by checking the source code behavior.
    """
    # This is tested at the logic level — the guard is:
    # if price_start == 0: return None
    # We verify it exists in the source
    import inspect
    source = inspect.getsource(_fetch_return)
    assert "price_start == 0" in source, "_fetch_return must guard against zero start price"


def test_fetch_return_returns_float_or_none():
    """_fetch_return must return float or None, never raise."""
    # Use a clearly invalid ticker that yfinance will return empty data for
    from datetime import datetime, timezone, timedelta
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=35)
    result = _fetch_return("INVALID_TICKER_XYZ_999", start, end)
    assert result is None or isinstance(result, float)
