"""
tests/test_routing.py — Route-by-asset-class tests

Pins the exact node lists returned for equity and crypto so diagram
drift can never silently come back. A failing test here means someone
changed graph_builder.py without updating the README agent count.
"""

import pytest
from src.graphs.graph_builder import route_by_asset_class


def _make_state(asset_class: str) -> dict:
    """Minimal state dict for routing tests."""
    return {"asset_class": asset_class}


# ── Equity routing ─────────────────────────────────────────────────────────────

def test_equity_returns_four_nodes():
    result = route_by_asset_class(_make_state("equity"))
    assert len(result) == 4


def test_equity_includes_technical_analyst():
    """TechnicalAnalyst must be in equity Wave 1 — catches diagram drift."""
    result = route_by_asset_class(_make_state("equity"))
    assert "technical_analyst" in result


def test_equity_includes_all_wave1_nodes():
    result = route_by_asset_class(_make_state("equity"))
    assert set(result) == {"fundamentals_agent", "sentiment_agent", "risk_agent", "technical_analyst"}


# ── Crypto routing ─────────────────────────────────────────────────────────────

def test_crypto_returns_three_nodes():
    result = route_by_asset_class(_make_state("crypto"))
    assert len(result) == 3


def test_crypto_includes_onchain_analyst():
    result = route_by_asset_class(_make_state("crypto"))
    assert "onchain_analyst" in result


def test_crypto_excludes_technical_analyst():
    """TechnicalAnalyst must NOT run for crypto — it uses OnchainAnalyst instead."""
    result = route_by_asset_class(_make_state("crypto"))
    assert "technical_analyst" not in result


def test_crypto_includes_all_wave1_nodes():
    result = route_by_asset_class(_make_state("crypto"))
    assert set(result) == {"onchain_analyst", "sentiment_agent", "risk_agent"}


# ── Invalid asset class ────────────────────────────────────────────────────────

def test_invalid_asset_class_raises_value_error():
    with pytest.raises(ValueError, match="Invalid asset_class"):
        route_by_asset_class(_make_state("futures"))


def test_empty_asset_class_raises_value_error():
    with pytest.raises((ValueError, KeyError)):
        route_by_asset_class(_make_state(""))


def test_none_asset_class_raises():
    with pytest.raises((ValueError, KeyError, TypeError)):
        route_by_asset_class({"asset_class": None})
