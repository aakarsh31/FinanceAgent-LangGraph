import uvicorn
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from src.graphs.graph_builder import GraphBuilder
from src.exceptions import FinanceAgentError
from langgraph.checkpoint.sqlite import SqliteSaver
from ingestion.db import get_engine, init_db

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
    global graph
    init_meta_table()
    cleanup_old_checkpoints()

    # Initialize Postgres — non-fatal if DATABASE_URL is not set
    # (allows local dev without Postgres; agents fall back to live API)
    pg_engine = None
    if os.getenv("DATABASE_URL"):
        try:
            pg_engine = get_engine()
            init_db(pg_engine)
            logger.info("Postgres engine initialized")
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
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def serve_frontend():
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

    # Pipeline suspended before supervisor_agent — return intermediate data
    asset_class = state.get("asset_class", "equity")
    logger.info(f"Pipeline paused before supervisor_agent — ticker={ticker} asset_class={asset_class}")

    # Build intermediate payload — asset-class aware
    intermediate: dict = {
        "asset_class": asset_class,
        "macro": state.get("macro"),
        "risk": state.get("risk"),
        "sentiment": state.get("sentiment"),
        "analyst_consensus": state.get("analyst_consensus"),
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
        "intermediate": intermediate,
        "data_provenance": state.get("data_provenance", {}),
    }


# ── /approve/{thread_id} ──────────────────────────────────────────────────────

@app.post("/approve/{thread_id}")
async def approve(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    logger.info(f"/approve — thread_id={thread_id}")

    try:
        # None input = resume existing thread from last checkpoint
        state = graph.invoke(None, config=config)
    except FinanceAgentError as e:
        logger.error(f"Supervisor generation failed for {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error resuming {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal pipeline error")

    logger.info(f"Supervisor report generated for thread_id={thread_id}")

    return {
        "status": "complete",
        "thread_id": thread_id,
        "ticker": state["ticker"],
        "asset_class": state.get("asset_class"),
        "supervisor_report": state["supervisor_report"],
    }


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)