import uvicorn
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from src.graphs.graph_builder import GraphBuilder
from src.exceptions import FinanceAgentError
from langgraph.checkpoint.sqlite import SqliteSaver
from ingestion.db import get_engine, init_db
from evaluation.db_eval import init_eval_db
from evaluation.signal_store import record_signal
from alpaca_broker.trade_executor import maybe_execute_trade
from alpaca_broker.client import AlpacaClient, AlpacaError

import os
from dotenv import load_dotenv
import logging

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s:%(funcName)s:%(lineno)d %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)

os.environ["LANGSMITH_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "FinanceAgent-MultiAgent")

VALID_TIMEFRAMES = ["1mo", "3mo", "6mo", "1y", "2y"]

graph = None
pg_engine = None


# ── Checkpoint helpers ────────────────────────────────────────────────────────

def init_meta_table(db_path="checkpoints.db"):
    """Create our own tracking table if it doesn't exist yet."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoint_meta (
                thread_id  TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
        """)


def record_thread(thread_id: str, db_path="checkpoints.db"):
    """Record a thread_id with the current timestamp."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO checkpoint_meta (thread_id, created_at)
            VALUES (?, ?)
        """, (thread_id, datetime.now(timezone.utc).isoformat()))


def cleanup_old_checkpoints(db_path="checkpoints.db", days=7):
    """Delete checkpoints older than `days` days and reclaim disk space."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        with sqlite3.connect(db_path) as conn:
            old_threads = [
                row[0] for row in conn.execute(
                    "SELECT thread_id FROM checkpoint_meta WHERE created_at < ?",
                    (cutoff,)
                ).fetchall()
            ]
            if old_threads:
                placeholders = ",".join("?" * len(old_threads))
                conn.execute(f"DELETE FROM checkpoints   WHERE thread_id IN ({placeholders})", old_threads)
                conn.execute(f"DELETE FROM writes        WHERE thread_id IN ({placeholders})", old_threads)
                conn.execute(f"DELETE FROM checkpoint_meta WHERE thread_id IN ({placeholders})", old_threads)
                conn.execute("VACUUM")
                logger.info(f"Checkpoint cleanup: removed {len(old_threads)} threads older than {days} days")
            else:
                logger.info("Checkpoint cleanup: nothing to remove")
    except Exception as e:
        logger.warning(f"Checkpoint cleanup failed (non-fatal): {e}")


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph, pg_engine
    init_meta_table()
    cleanup_old_checkpoints()

    # Initialize Postgres — non-fatal if DATABASE_URL is not set
    # (allows local dev without Postgres; agents fall back to live API)
    if os.getenv("DATABASE_URL"):
        try:
            pg_engine = get_engine()
            init_db(pg_engine)
            init_eval_db(pg_engine)
            logger.info("Postgres engine initialized (ingestion + eval tables)")
        except Exception as e:
            logger.warning(f"Postgres initialization failed (non-fatal): {e} — agents will use live API fallback")
    else:
        logger.warning("DATABASE_URL not set — running without Postgres cache")

    with SqliteSaver.from_conn_string("checkpoints.db") as checkpointer:
        graph_builder = GraphBuilder(engine=pg_engine)
        graph = graph_builder.setup_graph(checkpointer=checkpointer)
        logger.info("Graph compiled with SqliteSaver checkpointer")
        yield

    if pg_engine:
        pg_engine.dispose()
        logger.info("Postgres engine disposed")
    logger.info("Checkpointer connection closed")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="frontend"), name="static")

# Serve React build if it exists, fall back to old frontend
import pathlib
REACT_DIST = pathlib.Path("frontend-react/dist")

if REACT_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(REACT_DIST / "assets")), name="assets")

@app.get("/")
async def serve_frontend():
    if REACT_DIST.exists():
        return FileResponse(str(REACT_DIST / "index.html"))
    return FileResponse("frontend/index.html")


# ── /analyze ─────────────────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze(request: Request):
    data = await request.json()

    # ── Validate inputs ───────────────────────────────────────────────────────
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        raise HTTPException(status_code=422, detail="Ticker cannot be empty")

    timeframe = data.get("timeframe", "")
    if timeframe not in VALID_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid timeframe '{timeframe}'. Valid: {VALID_TIMEFRAMES}"
        )

    thread_id = data.get("thread_id", "").strip()
    if not thread_id:
        raise HTTPException(status_code=422, detail="thread_id cannot be empty")

    # asset_class is intentionally NOT accepted from the request body —
    # DataFetchAgent detects it from yfinance quoteType

    logger.info(f"/analyze — ticker={ticker} timeframe={timeframe} thread_id={thread_id}")
    record_thread(thread_id)
    config = {"configurable": {"thread_id": thread_id}}

    try:
        state = graph.invoke(
            {"ticker": ticker, "timeframe": timeframe},
            config=config,
        )
    except FinanceAgentError as e:
        logger.error(f"Pipeline failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal pipeline error")

    # Pipeline suspended before trade_gate — supervisor has already run
    asset_class = state.get("asset_class", "equity")
    supervisor_report = state.get("supervisor_report", {})
    logger.info(f"Pipeline paused before trade_gate — ticker={ticker} asset_class={asset_class} recommendation={supervisor_report.get('recommendation') if supervisor_report else 'N/A'}")

    # Build intermediate payload — asset-class aware
    intermediate: dict = {
        "asset_class": asset_class,
        "macro": state.get("macro"),
        "risk": state.get("risk"),
        "sentiment": state.get("sentiment"),
        "analyst_consensus": state.get("analyst_consensus"),
        "technical": state.get("technical"),
    }

    if asset_class == "equity":
        intermediate.update({
            "fundamentals": state.get("fundamentals"),
            "bull_thesis": state.get("bull_thesis"),
            "bear_thesis": state.get("bear_thesis"),
            "valuation": state.get("valuation"),
        })
    else:
        intermediate.update({
            "onchain": state.get("onchain"),
        })

    return {
        "status": "pending_approval",
        "thread_id": thread_id,
        "ticker": ticker,
        "asset_class": asset_class,
        "supervisor_report": supervisor_report,
        "intermediate": intermediate,
        "data_provenance": state.get("data_provenance", {}),
    }


# ── /approve/{thread_id} ──────────────────────────────────────────────────────

@app.post("/approve/{thread_id}")
async def approve(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    logger.info(f"/approve — thread_id={thread_id}")

    try:
        # Resume past trade_gate — supervisor already ran in /analyze
        state = graph.invoke(None, config=config)
    except FinanceAgentError as e:
        logger.error(f"Approval failed for {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error resuming {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal pipeline error")

    ticker = state["ticker"]
    asset_class = state.get("asset_class", "equity")
    supervisor_report = state.get("supervisor_report", {})

    logger.info(f"Trade gate approved for thread_id={thread_id} — recommendation={supervisor_report.get('recommendation') if supervisor_report else 'N/A'}")

    # Record signal for evaluation — non-fatal
    if pg_engine and supervisor_report:
        try:
            run_id = record_signal(
                engine=pg_engine,
                ticker=ticker,
                asset_class=asset_class,
                supervisor_report=supervisor_report,
                data_provenance=state.get("data_provenance", {}),
            )
            if run_id:
                logger.info(f"Signal recorded — run_id={run_id}")
        except Exception as e:
            logger.error(f"Signal recording failed for {ticker} (non-fatal): {e}")

    # Execute paper trade — High confidence equity signals only, non-fatal
    trade_result = {"traded": False, "skipped_reason": "alpaca_not_configured", "order": None, "stop_loss": None, "error": None}
    if os.getenv("APCA_API_KEY_ID") and supervisor_report:
        trade_result = maybe_execute_trade(
            ticker=ticker,
            asset_class=asset_class,
            supervisor_report=supervisor_report,
        )
        if trade_result["traded"]:
            logger.info(f"Paper trade executed — {ticker} order_id={trade_result['order']['order_id']}")
        elif trade_result["error"]:
            logger.warning(f"Paper trade failed for {ticker}: {trade_result['error']}")
        else:
            logger.info(f"Paper trade skipped — {ticker} reason={trade_result['skipped_reason']}")

    return {
        "status": "complete",
        "thread_id": thread_id,
        "ticker": ticker,
        "asset_class": asset_class,
        "supervisor_report": supervisor_report,
        "trade": trade_result,
    }


# ── /evaluate ─────────────────────────────────────────────────────────────────

@app.get("/evaluate")
async def evaluate():
    """
    Returns the latest hit rate metrics from eval_runs.
    Also triggers maturation of any pending signals on-demand.
    """
    from evaluation.eval_job import run_eval_maturation
    from sqlalchemy import text as sql_text

    if not pg_engine:
        raise HTTPException(status_code=503, detail="Database not available")

    # Run maturation for any newly eligible signals
    summary = run_eval_maturation(pg_engine)

    # Fetch latest eval_run
    try:
        with pg_engine.connect() as conn:
            latest = conn.execute(sql_text("""
                SELECT * FROM eval_runs
                ORDER BY created_at DESC
                LIMIT 1
            """)).fetchone()

            total_pending = conn.execute(sql_text("""
                SELECT COUNT(*) FROM pipeline_signals WHERE eval_status = 'pending'
            """)).scalar()

            total_evaluated = conn.execute(sql_text("""
                SELECT COUNT(*) FROM pipeline_signals WHERE eval_status = 'evaluated'
            """)).scalar()

        if not latest:
            return {
                "status": "no_data",
                "message": "No evaluated signals yet. Signals mature after 30 days.",
                "pending_signals": total_pending,
                "maturation_summary": summary,
            }

        return {
            "status": "ok",
            "model_version": latest.model_version,
            "signals_evaluated": latest.signals_evaluated,
            "pending_signals": total_pending,
            "total_evaluated": total_evaluated,
            "hit_rate": {
                "overall": round(latest.hit_rate_overall * 100, 1) if latest.hit_rate_overall else None,
                "buy": round(latest.hit_rate_buy * 100, 1) if latest.hit_rate_buy else None,
                "sell": round(latest.hit_rate_sell * 100, 1) if latest.hit_rate_sell else None,
                "high_confidence": round(latest.hit_rate_high_confidence * 100, 1) if latest.hit_rate_high_confidence else None,
                "medium_confidence": round(latest.hit_rate_medium_confidence * 100, 1) if latest.hit_rate_medium_confidence else None,
            },
            "returns": {
                "avg_return_buy_pct": round(latest.avg_return_buy * 100, 2) if latest.avg_return_buy else None,
                "avg_return_sell_pct": round(latest.avg_return_sell * 100, 2) if latest.avg_return_sell else None,
                "avg_alpha_buy_pct": round(latest.avg_alpha_buy * 100, 2) if latest.avg_alpha_buy else None,
            },
            "last_eval": latest.created_at.isoformat() if latest.created_at else None,
            "maturation_summary": summary,
        }
    except Exception as e:
        logger.error(f"/evaluate failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Eval query failed")



# ── /portfolio ────────────────────────────────────────────────────────────────

@app.get("/portfolio")
async def portfolio():
    """
    Returns Alpaca paper trading portfolio — account summary, open positions,
    equity curve, and recent orders. Powers the frontend Portfolio panel.
    """
    if not os.getenv("APCA_API_KEY_ID"):
        raise HTTPException(status_code=503, detail="Alpaca not configured")

    try:
        client = AlpacaClient()
        account = client.get_account()
        positions = client.get_positions()
        history = client.get_portfolio_history(period="1M", timeframe="1D")
        orders = client.get_orders(limit=20)

        # Compute total P&L from base value
        base_value = history.get("base_value") or account["equity"]
        total_pnl = round(account["equity"] - base_value, 2)
        total_pnl_pct = round((total_pnl / base_value) * 100, 2) if base_value > 0 else 0.0

        # Win rate — filled orders only
        filled = [o for o in orders if o["status"] == "filled"]
        wins = [
            o for o in filled
            if (o["side"] == "buy" and (o.get("filled_avg_price") or 0) > 0)
        ]

        return {
            "status": "ok",
            "account": {
                **account,
                "total_pnl": total_pnl,
                "total_pnl_pct": total_pnl_pct,
            },
            "positions": positions,
            "equity_curve": history,
            "recent_orders": orders,
            "stats": {
                "trades_placed": len(filled),
                "open_positions": len(positions),
            },
        }
    except AlpacaError as e:
        logger.error(f"/portfolio failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.error(f"/portfolio unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Portfolio fetch failed")


# ── /orders ───────────────────────────────────────────────────────────────────

@app.get("/orders")
async def orders():
    """Returns recent paper trading order history."""
    if not os.getenv("APCA_API_KEY_ID"):
        raise HTTPException(status_code=503, detail="Alpaca not configured")

    try:
        client = AlpacaClient()
        return {
            "status": "ok",
            "orders": client.get_orders(limit=50),
        }
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)