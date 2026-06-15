"""
tests/test_routing.py — Route-by-asset-class tests

Pins the exact node lists returned for equity and crypto so diagram
drift can never silently come back. A failing test here means someone
changed graph_builder.py without updating the README agent count.
"""

import pytest
from src.graphs.graph_builder import route_by_asset_class, GraphBuilder


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


# ── Graph compilation — catches missing edges ──────────────────────────────────

def _make_builder():
    """Return a GraphBuilder with LLM calls mocked out — no OPENAI_API_KEY needed."""
    from unittest.mock import MagicMock, patch
    mock_llm = MagicMock()
    with patch("src.graphs.graph_builder.LLMClient") as MockClient:
        MockClient.return_value.get_llm.return_value = mock_llm
        builder = GraphBuilder(engine=None)
    return builder


def test_graph_compiles_without_checkpointer():
    """GraphBuilder.build() must compile cleanly — catches structural errors
    like missing edges that LangGraph detects at compile time."""
    builder = _make_builder()
    graph = builder.setup_graph(checkpointer=None, hitl=False)
    assert graph is not None


def test_crypto_nodes_all_have_outgoing_edges():
    """Regression test for the bug where sentiment_agent and risk_agent had no
    outgoing edges in the crypto path, causing the graph to hang.

    Verifies by inspecting the compiled graph's node map — every node that
    route_by_asset_class returns for crypto must appear as a source in the
    edge list (i.e. have at least one outgoing edge)."""
    builder = _make_builder()
    raw_graph = builder.build()

    # Nodes routed for crypto
    crypto_wave1 = {"onchain_analyst", "sentiment_agent", "risk_agent"}

    # Get all nodes that have at least one outgoing edge defined in the graph
    # raw_graph.edges is a set of (source, target) tuples
    nodes_with_outgoing = {src for src, _ in raw_graph.edges}
    for node in crypto_wave1:
        assert node in nodes_with_outgoing, (
            f"{node} has no outgoing edges — crypto pipeline would hang at this node"
        )


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