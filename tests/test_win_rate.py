"""
tests/test_win_rate.py — Win rate computation tests

Tests the round-trip win/loss logic against a synthetic order history
with known outcomes. Imports from alpaca_broker.portfolio_stats —
the real production module used by /portfolio — so tests are load-bearing.
"""

import pytest
from alpaca_broker.portfolio_stats import compute_win_rate


def _compute_win_rate(orders):
    """Thin wrapper to keep test bodies unchanged."""
    return compute_win_rate(orders)


def _order(ticker, side, price, status="filled", submitted_at="2026-01-01T00:00:00Z"):
    return {
        "ticker": ticker,
        "side": side,
        "filled_avg_price": price,
        "status": status,
        "submitted_at": submitted_at,
    }


# ── No closed trades ──────────────────────────────────────────────────────────

def test_no_orders_win_rate_is_none():
    result = _compute_win_rate([])
    assert result["win_rate"] is None
    assert result["closed_trades"] == 0


def test_only_open_buys_win_rate_is_none():
    """Open positions excluded — win rate should be None."""
    orders = [_order("AAPL", "buy", 180.0, status="accepted")]
    result = _compute_win_rate(orders)
    assert result["win_rate"] is None


def test_only_filled_buys_no_sells_win_rate_is_none():
    """Filled buys with no sells = no closed round trips = None win rate."""
    orders = [
        _order("AAPL", "buy", 180.0),
        _order("NVDA", "buy", 200.0),
    ]
    result = _compute_win_rate(orders)
    assert result["win_rate"] is None
    assert result["closed_trades"] == 0


# ── The old bug — all buys would be 100% win rate ─────────────────────────────

def test_filled_buys_are_not_wins():
    """
    Critical: filled buys must NOT count as wins.
    This was the original bug — every filled buy was a "win".
    """
    orders = [
        _order("AAPL", "buy", 180.0),
        _order("MSFT", "buy", 400.0),
        _order("NVDA", "buy", 210.0),
    ]
    result = _compute_win_rate(orders)
    # No sells → no closed round trips → win rate is None, not 100%
    assert result["win_rate"] is None
    assert result["wins"] == 0


# ── Winning round trips ────────────────────────────────────────────────────────

def test_one_winning_round_trip():
    orders = [
        _order("AAPL", "buy", 180.0),
        _order("AAPL", "sell", 190.0),  # sold higher → win
    ]
    result = _compute_win_rate(orders)
    assert result["win_rate"] == 1.0
    assert result["closed_trades"] == 1
    assert result["wins"] == 1


def test_one_losing_round_trip():
    orders = [
        _order("AAPL", "buy", 180.0),
        _order("AAPL", "sell", 170.0),  # sold lower → loss
    ]
    result = _compute_win_rate(orders)
    assert result["win_rate"] == 0.0
    assert result["closed_trades"] == 1
    assert result["wins"] == 0


def test_mixed_wins_and_losses():
    orders = [
        _order("AAPL", "buy", 180.0),
        _order("AAPL", "sell", 190.0),   # win
        _order("MSFT", "buy", 400.0),
        _order("MSFT", "sell", 380.0),   # loss
        _order("NVDA", "buy", 200.0),
        _order("NVDA", "sell", 220.0),   # win
    ]
    result = _compute_win_rate(orders)
    assert result["closed_trades"] == 3
    assert result["wins"] == 2
    assert result["win_rate"] == round(2/3, 4)


def test_avg_entry_price_used_for_multiple_buys():
    """Multiple buys of same ticker → avg entry price used for win calc."""
    orders = [
        _order("AAPL", "buy", 180.0, submitted_at="2026-01-01T00:00:00Z"),
        _order("AAPL", "buy", 200.0, submitted_at="2026-01-02T00:00:00Z"),
        # avg entry = 190.0
        _order("AAPL", "sell", 195.0),  # above avg entry 190 → win
    ]
    result = _compute_win_rate(orders)
    assert result["wins"] == 1
    assert result["win_rate"] == 1.0


def test_sell_without_prior_buy_excluded():
    """A sell with no corresponding buy history is not counted."""
    orders = [
        _order("AAPL", "sell", 190.0),  # no buy → excluded
    ]
    result = _compute_win_rate(orders)
    assert result["closed_trades"] == 0
    assert result["win_rate"] is None


def test_break_even_sell_is_loss():
    """Sell at exactly entry price is a loss (exit > entry required for win)."""
    orders = [
        _order("AAPL", "buy", 180.0),
        _order("AAPL", "sell", 180.0),  # exactly break-even → not a win
    ]
    result = _compute_win_rate(orders)
    assert result["wins"] == 0
    assert result["win_rate"] == 0.0