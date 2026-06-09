"""
alpaca/client.py — Alpaca Paper Trading SDK wrapper

Thin wrapper around alpaca-py. Handles:
- Account info
- Placing market orders (Buy/Sell)
- Fetching open positions
- Portfolio history (equity curve)
- Recent orders

All methods return plain dicts — no SDK objects leak outside this module.
Raises AlpacaError on any failure so callers can handle gracefully.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PAPER_BASE_URL = "https://paper-api.alpaca.markets"


class AlpacaError(Exception):
    pass


class AlpacaClient:

    def __init__(self):
        api_key = os.getenv("APCA_API_KEY_ID")
        secret_key = os.getenv("APCA_API_SECRET_KEY")

        if not api_key or not secret_key:
            raise AlpacaError("APCA_API_KEY_ID and APCA_API_SECRET_KEY must be set")

        try:
            from alpaca.trading.client import TradingClient
            from alpaca.data.historical import StockHistoricalDataClient
            self._trading = TradingClient(
                api_key=api_key,
                secret_key=secret_key,
                paper=True,
            )
            self._data = StockHistoricalDataClient(
                api_key=api_key,
                secret_key=secret_key,
            )
        except ImportError:
            raise AlpacaError("alpaca-py not installed — run: pip install alpaca-py")
        except Exception as e:
            raise AlpacaError(f"Failed to initialize Alpaca client: {e}")

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        """Return key account metrics."""
        try:
            acct = self._trading.get_account()
            return {
                "equity": float(acct.equity),
                "cash": float(acct.cash),
                "buying_power": float(acct.buying_power),
                "portfolio_value": float(acct.portfolio_value),
                "day_pnl": float(acct.equity) - float(acct.last_equity),
                "day_pnl_pct": round(
                    (float(acct.equity) - float(acct.last_equity)) / float(acct.last_equity) * 100, 2
                ) if float(acct.last_equity) > 0 else 0.0,
                "status": acct.status.value if hasattr(acct.status, "value") else str(acct.status),
            }
        except AlpacaError:
            raise
        except Exception as e:
            raise AlpacaError(f"get_account failed: {e}")

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(self, ticker: str, side: str, notional_usd: float = 500.0, qty: float = None) -> dict:
        """
        Place a market order by notional amount (buys) or qty (sells).

        Args:
            ticker:       e.g. 'AAPL'
            side:         'buy' or 'sell'
            notional_usd: dollar amount for buys (default $500)
            qty:          share quantity for sells (closes existing position)
        """
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        side = side.lower()
        if side not in ("buy", "sell"):
            raise AlpacaError(f"Invalid order side '{side}' — must be 'buy' or 'sell'")

        try:
            order_request = MarketOrderRequest(
                symbol=ticker,
                qty=round(float(qty), 6) if qty is not None else None,
                notional=round(notional_usd, 2) if qty is None else None,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = self._trading.submit_order(order_request)
            logger.info(f"[Alpaca] Order placed — {side.upper()} {ticker} | id={order.id} status={order.status}")
            return _serialize_order(order)
        except AlpacaError:
            raise
        except Exception as e:
            raise AlpacaError(f"place_order failed for {ticker}: {e}")

    def place_stop_loss(self, ticker: str, qty: float, stop_price: float) -> dict | None:
        """
        Place a stop-loss order for an existing position.
        Non-fatal — logs failure but doesn't raise.

        Args:
            ticker:      e.g. 'AAPL'
            qty:         number of shares to protect
            stop_price:  price at which to trigger the sell
        """
        from alpaca.trading.requests import StopOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        try:
            stop_request = StopOrderRequest(
                symbol=ticker,
                qty=round(float(qty), 6),
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,  # Good Till Cancelled
                stop_price=round(stop_price, 2),
            )
            order = self._trading.submit_order(stop_request)
            logger.info(f"[Alpaca] Stop-loss placed — SELL {qty} {ticker} @ ${stop_price:.2f} | id={order.id}")
            return _serialize_order(order)
        except Exception as e:
            logger.warning(f"[Alpaca] Stop-loss placement failed for {ticker} (non-fatal): {e}")
            return None

    def get_orders(self, limit: int = 20) -> list[dict]:
        """Return recent orders (filled + pending)."""
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        try:
            request = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit)
            orders = self._trading.get_orders(filter=request)
            return [_serialize_order(o) for o in orders]
        except Exception as e:
            raise AlpacaError(f"get_orders failed: {e}")

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        """Return all open positions."""
        try:
            positions = self._trading.get_all_positions()
            return [_serialize_position(p) for p in positions]
        except Exception as e:
            raise AlpacaError(f"get_positions failed: {e}")

    # ── Portfolio history ─────────────────────────────────────────────────────

    def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> dict:
        """
        Return equity curve for the P&L chart.

        period:    '1D', '1W', '1M', '3M', '6M', '1A'
        timeframe: '1Min', '5Min', '15Min', '1H', '1D'
        """
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        try:
            request = GetPortfolioHistoryRequest(
                period=period,
                timeframe=timeframe,
                intraday_reporting="market_hours",
            )
            history = self._trading.get_portfolio_history(request)
            return {
                "timestamps": [
                    datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    for ts in (history.timestamp or [])
                ],
                "equity": [round(float(v), 2) if v is not None else None for v in (history.equity or [])],
                "profit_loss": [round(float(v), 2) if v is not None else None for v in (history.profit_loss or [])],
                "profit_loss_pct": [round(float(v) * 100, 3) if v is not None else None for v in (history.profit_loss_pct or [])],
                "base_value": float(history.base_value) if history.base_value else None,
            }
        except Exception as e:
            raise AlpacaError(f"get_portfolio_history failed: {e}")


# ── Serializers ───────────────────────────────────────────────────────────────

def _serialize_order(order) -> dict:
    return {
        "order_id": str(order.id),
        "ticker": order.symbol,
        "side": order.side.value if hasattr(order.side, "value") else str(order.side),
        "type": order.type.value if hasattr(order.type, "value") else str(order.type),
        "status": order.status.value if hasattr(order.status, "value") else str(order.status),
        "notional": float(order.notional) if order.notional else None,
        "qty": float(order.qty) if order.qty else None,
        "filled_qty": float(order.filled_qty) if order.filled_qty else None,
        "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
        "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
        "filled_at": order.filled_at.isoformat() if order.filled_at else None,
    }


def _serialize_position(pos) -> dict:
    return {
        "ticker": pos.symbol,
        "qty": float(pos.qty),
        "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
        "avg_entry_price": float(pos.avg_entry_price),
        "current_price": float(pos.current_price) if pos.current_price else None,
        "market_value": float(pos.market_value) if pos.market_value else None,
        "cost_basis": float(pos.cost_basis) if pos.cost_basis else None,
        "unrealized_pl": float(pos.unrealized_pl) if pos.unrealized_pl else None,
        "unrealized_plpc": round(float(pos.unrealized_plpc) * 100, 2) if pos.unrealized_plpc else None,
        "change_today": round(float(pos.change_today) * 100, 2) if pos.change_today else None,
    }