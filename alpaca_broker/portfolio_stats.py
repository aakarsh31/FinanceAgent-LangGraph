"""
alpaca_broker/portfolio_stats.py — Portfolio statistics helpers

Pure functions for computing portfolio metrics from order history.
Imported by both app.py /portfolio endpoint and the test suite —
ensuring tests exercise the real production logic, not a copy.
"""


def compute_win_rate(orders: list[dict]) -> dict:
    """
    Compute win rate from closed round trips only.

    A win = a sell that closed at a higher price than the average entry
    price of preceding buys for that symbol. Open positions are excluded —
    unrealized P&L is reported separately.

    Args:
        orders: list of order dicts from AlpacaClient.get_orders()

    Returns:
        {
            "win_rate":      float | None  — None when no closed round trips exist
            "closed_trades": int           — number of closed round trips
            "wins":          int           — number of winning round trips
        }
    """
    filled = [o for o in orders if o["status"] == "filled"]
    buys   = [o for o in filled if o["side"] == "buy"  and o.get("filled_avg_price")]
    sells  = [o for o in filled if o["side"] == "sell" and o.get("filled_avg_price")]

    # Build avg entry price per symbol from buy fills (chronological order)
    entry_prices: dict[str, list[float]] = {}
    for o in sorted(buys, key=lambda x: x.get("submitted_at") or ""):
        sym = o["ticker"]
        entry_prices.setdefault(sym, []).append(float(o["filled_avg_price"]))

    # For each sell, compare against avg entry price of that symbol's buys
    closed_trades = []
    for o in sells:
        sym = o["ticker"]
        entries = entry_prices.get(sym)
        if entries:
            avg_entry = sum(entries) / len(entries)
            exit_price = float(o["filled_avg_price"])
            closed_trades.append({
                "ticker":     sym,
                "avg_entry":  round(avg_entry, 4),
                "exit_price": round(exit_price, 4),
                "is_win":     exit_price > avg_entry,
            })

    wins = [t for t in closed_trades if t["is_win"]]
    win_rate = round(len(wins) / len(closed_trades), 4) if closed_trades else None

    return {
        "win_rate":      win_rate,
        "closed_trades": len(closed_trades),
        "wins":          len(wins),
    }