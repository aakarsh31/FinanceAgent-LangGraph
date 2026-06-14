"""
tests/test_trade_executor.py — Trade execution rules tests

Tests the filtering logic in maybe_execute_trade without placing real orders.
All AlpacaClient calls are mocked — we're testing the rules, not the SDK.
"""

from unittest.mock import MagicMock, patch


def _make_report(recommendation: str, confidence: str) -> dict:
    return {"recommendation": recommendation, "confidence": confidence}


# ── Import the function under test ─────────────────────────────────────────────

from alpaca_broker.trade_executor import maybe_execute_trade


# ── Crypto skip rule ───────────────────────────────────────────────────────────

def test_crypto_always_skipped():
    result = maybe_execute_trade("BTC-USD", "crypto", _make_report("Buy", "High"))
    assert result["traded"] is False
    assert result["skipped_reason"] == "crypto_not_supported"


def test_crypto_skipped_regardless_of_confidence():
    result = maybe_execute_trade("ETH-USD", "crypto", _make_report("Buy", "High"))
    assert result["traded"] is False


# ── Confidence filter ──────────────────────────────────────────────────────────

def test_medium_confidence_skipped():
    result = maybe_execute_trade("AAPL", "equity", _make_report("Buy", "Medium"))
    assert result["traded"] is False
    assert "medium" in result["skipped_reason"]


def test_low_confidence_skipped():
    result = maybe_execute_trade("AAPL", "equity", _make_report("Buy", "Low"))
    assert result["traded"] is False
    assert "low" in result["skipped_reason"]


def test_confidence_casing_high_uppercase():
    """'HIGH' should be treated same as 'High' via .capitalize()."""
    with patch("alpaca_broker.trade_executor.AlpacaClient") as MockClient:
        mock = MagicMock()
        MockClient.return_value = mock
        mock.place_order.return_value = {"order_id": "test-123", "status": "accepted", "side": "buy", "ticker": "AAPL", "type": "market", "notional": 500.0, "qty": None, "filled_qty": 0.0, "filled_avg_price": None, "submitted_at": None, "filled_at": None}
        mock.get_positions.return_value = []
        result = maybe_execute_trade("AAPL", "equity", _make_report("Buy", "HIGH"))
        # "HIGH".capitalize() == "High" → should pass the confidence check
        assert result["traded"] is True


def test_confidence_casing_high_lowercase():
    """'high' should be treated same as 'High' via .capitalize()."""
    with patch("alpaca_broker.trade_executor.AlpacaClient") as MockClient:
        mock = MagicMock()
        MockClient.return_value = mock
        mock.place_order.return_value = {"order_id": "test-456", "status": "accepted", "side": "buy", "ticker": "MSFT", "type": "market", "notional": 500.0, "qty": None, "filled_qty": 0.0, "filled_avg_price": None, "submitted_at": None, "filled_at": None}
        mock.get_positions.return_value = []
        result = maybe_execute_trade("MSFT", "equity", _make_report("Buy", "high"))
        assert result["traded"] is True


def test_missing_confidence_key_skips():
    """Missing confidence key should skip cleanly, not raise."""
    result = maybe_execute_trade("AAPL", "equity", {"recommendation": "Buy"})
    assert result["traded"] is False


# ── Hold skip rule ─────────────────────────────────────────────────────────────

def test_hold_skipped():
    result = maybe_execute_trade("AAPL", "equity", _make_report("Hold", "High"))
    assert result["traded"] is False
    assert result["skipped_reason"] == "hold_signal"


# ── Sell without position ─────────────────────────────────────────────────────

def test_sell_without_position_skipped():
    with patch("alpaca_broker.trade_executor.AlpacaClient") as MockClient:
        mock = MagicMock()
        MockClient.return_value = mock
        mock.get_positions.return_value = []  # no positions held
        result = maybe_execute_trade("AAPL", "equity", _make_report("Sell", "High"))
        assert result["traded"] is False
        assert result["skipped_reason"] == "no_position_to_sell"


# ── Buy executes ───────────────────────────────────────────────────────────────

def test_buy_high_confidence_equity_trades():
    with patch("alpaca_broker.trade_executor.AlpacaClient") as MockClient:
        mock = MagicMock()
        MockClient.return_value = mock
        mock.place_order.return_value = {
            "order_id": "abc-123", "status": "accepted", "side": "buy",
            "ticker": "NVDA", "type": "market", "notional": 500.0,
            "qty": None, "filled_qty": 0.0, "filled_avg_price": None,
            "submitted_at": "2026-01-01T00:00:00Z", "filled_at": None,
        }
        mock.get_positions.return_value = []
        result = maybe_execute_trade("NVDA", "equity", _make_report("Buy", "High"))
        assert result["traded"] is True
        assert result["order"]["order_id"] == "abc-123"
        assert result["error"] is None


# ── Unknown recommendation ────────────────────────────────────────────────────

def test_unknown_recommendation_skipped():
    result = maybe_execute_trade("AAPL", "equity", _make_report("Strong Buy", "High"))
    assert result["traded"] is False
    assert "unknown_recommendation" in result["skipped_reason"]


