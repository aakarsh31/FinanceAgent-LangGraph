"""
alpaca_broker/trade_executor.py — Trade execution logic

Sits between /approve and AlpacaClient.
Enforces rules:
  1. Equity only (no crypto — Alpaca paper trading doesn't support it)
  2. High confidence signals only
  3. Buy → market buy order | Sell → close existing position | Hold → no trade
  4. Non-fatal — a trade failure never breaks the /approve response
"""

import logging
from alpaca_broker.client import AlpacaClient, AlpacaError

logger = logging.getLogger(__name__)

# Paper money per trade — $500 notional per signal
NOTIONAL_PER_TRADE_USD = 500.0


def maybe_execute_trade(
    ticker: str,
    asset_class: str,
    supervisor_report: dict,
) -> dict:
    """
    Attempt to place a paper trade based on the supervisor report.
    Always returns a result dict — never raises. Trade failures are logged, not re-raised.

    Returns:
        {
            "traded": bool,
            "skipped_reason": str | None,
            "order": dict | None,
            "error": str | None,
        }
    """
    recommendation = (supervisor_report.get("recommendation") or "").strip().capitalize()
    confidence = (supervisor_report.get("confidence") or "").strip().capitalize()

    # Rule 1: equity only
    if asset_class != "equity":
        logger.info(f"[TradeExecutor] Skipping {ticker} — crypto not supported on Alpaca paper trading")
        return {"traded": False, "skipped_reason": "crypto_not_supported", "order": None, "error": None}

    # Rule 2: High confidence only
    if confidence != "High":
        logger.info(f"[TradeExecutor] Skipping {ticker} — confidence={confidence} (only High confidence trades)")
        return {"traded": False, "skipped_reason": f"confidence_{confidence.lower()}", "order": None, "error": None}

    # Rule 3: Hold → no trade
    if recommendation == "Hold":
        logger.info(f"[TradeExecutor] Skipping {ticker} — recommendation=Hold")
        return {"traded": False, "skipped_reason": "hold_signal", "order": None, "error": None}

    # Map recommendation → order side
    if recommendation == "Buy":
        side = "buy"
    elif recommendation == "Sell":
        side = "sell"
    else:
        logger.warning(f"[TradeExecutor] Unknown recommendation '{recommendation}' for {ticker} — skipping")
        return {"traded": False, "skipped_reason": f"unknown_recommendation_{recommendation}", "order": None, "error": None}

    # Place the trade
    try:
        client = AlpacaClient()

        # For Sell signals — only sell if we actually hold the position
        # Alpaca paper trading doesn't support fractional short selling
        if side == "sell":
            positions = client.get_positions()
            held = next((p for p in positions if p["ticker"] == ticker), None)
            if not held:
                logger.info(f"[TradeExecutor] Skipping SELL {ticker} — no position held")
                return {"traded": False, "skipped_reason": "no_position_to_sell", "order": None, "error": None}
            order = client.place_order(ticker=ticker, side=side, notional_usd=None, qty=held["qty"])
        else:
            order = client.place_order(ticker=ticker, side=side, notional_usd=NOTIONAL_PER_TRADE_USD)

        logger.info(f"[TradeExecutor] Trade placed — {side.upper()} {ticker} | order_id={order['order_id']}")
        return {"traded": True, "skipped_reason": None, "order": order, "error": None}

    except AlpacaError as e:
        logger.error(f"[TradeExecutor] Trade failed for {ticker} (non-fatal): {e}")
        return {"traded": False, "skipped_reason": None, "order": None, "error": str(e)}
    except Exception as e:
        logger.error(f"[TradeExecutor] Unexpected trade error for {ticker} (non-fatal): {e}", exc_info=True)
        return {"traded": False, "skipped_reason": None, "order": None, "error": str(e)}