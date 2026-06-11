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

# Stop-loss threshold — 7% below entry price
STOP_LOSS_PCT = 0.07


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
        return {"traded": False, "skipped_reason": "crypto_not_supported", "order": None, "stop_loss": None, "error": None}

    # Rule 2: High confidence only
    if confidence != "High":
        logger.info(f"[TradeExecutor] Skipping {ticker} — confidence={confidence} (only High confidence trades)")
        return {"traded": False, "skipped_reason": f"confidence_{confidence.lower()}", "order": None, "stop_loss": None, "error": None}

    # Rule 3: Hold → no trade
    if recommendation == "Hold":
        logger.info(f"[TradeExecutor] Skipping {ticker} — recommendation=Hold")
        return {"traded": False, "skipped_reason": "hold_signal", "order": None, "stop_loss": None, "error": None}

    # Map recommendation → order side
    if recommendation == "Buy":
        side = "buy"
    elif recommendation == "Sell":
        side = "sell"
    else:
        logger.warning(f"[TradeExecutor] Unknown recommendation '{recommendation}' for {ticker} — skipping")
        return {"traded": False, "skipped_reason": f"unknown_recommendation_{recommendation}", "order": None, "stop_loss": None, "error": None}

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
                return {"traded": False, "skipped_reason": "no_position_to_sell", "order": None, "stop_loss": None, "error": None}
            order = client.place_order(ticker=ticker, side=side, notional_usd=None, qty=held["qty"])
        else:
            order = client.place_order(ticker=ticker, side=side, notional_usd=NOTIONAL_PER_TRADE_USD)

        logger.info(f"[TradeExecutor] Trade placed — {side.upper()} {ticker} | order_id={order['order_id']}")

        # Place stop-loss for buy orders — 7% below current price
        # Uses price from supervisor_report (recorded at signal time) — no need to wait for fill
        stop_result = None
        if side == "buy":
            try:
                import yfinance as yf
                current_price = None

                # Try to get current price from yfinance
                try:
                    tick = yf.Ticker(ticker)
                    hist = tick.history(period="1d")
                    if not hist.empty:
                        current_price = float(hist["Close"].iloc[-1])
                except Exception:
                    pass

                if current_price and current_price > 0:
                    stop_price = round(current_price * (1 - STOP_LOSS_PCT), 2)
                    # Estimate qty from notional and current price
                    estimated_qty = round(NOTIONAL_PER_TRADE_USD / current_price, 6)
                    stop_result = client.place_stop_loss(
                        ticker=ticker,
                        qty=estimated_qty,
                        stop_price=stop_price,
                    )
                    if stop_result:
                        logger.info(f"[TradeExecutor] Stop-loss placed — {ticker} @ ${stop_price:.2f} ({STOP_LOSS_PCT*100:.0f}% below ${current_price:.2f})")
                else:
                    logger.warning(f"[TradeExecutor] Could not determine price for stop-loss on {ticker} — skipping")
            except Exception as e:
                logger.warning(f"[TradeExecutor] Stop-loss setup failed for {ticker} (non-fatal): {e}")

        return {"traded": True, "skipped_reason": None, "order": order, "stop_loss": stop_result, "error": None}

    except AlpacaError as e:
        logger.error(f"[TradeExecutor] Trade failed for {ticker} (non-fatal): {e}")
        return {"traded": False, "skipped_reason": None, "order": None, "stop_loss": None, "error": str(e)}
    except Exception as e:
        logger.error(f"[TradeExecutor] Unexpected trade error for {ticker} (non-fatal): {e}", exc_info=True)
        return {"traded": False, "skipped_reason": None, "order": None, "stop_loss": None, "error": str(e)}
