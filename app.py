import uvicorn
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from src.graphs.graph_builder import GraphBuilder
from src.exceptions import FinanceAgentError
from langgraph.checkpoint.sqlite import SqliteSaver

import os
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s:%(funcName)s:%(lineno)d %(message)s",
    force=True
)

os.environ['LANGSMITH_API_KEY'] = os.getenv("LANGCHAIN_API_KEY")

VALID_TIMEFRAMES = ["1mo", "3mo", "6mo", "1y", "2y"]
VALID_ASSET_CLASSES = ["equity", "crypto", "macro"]

graph = None

def init_meta_table(db_path="checkpoints.db"):
    """Create our own tracking table if it doesn't exist yet."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoint_meta (
                thread_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
        """)

def record_thread(thread_id: str, db_path="checkpoints.db"):
    """Record a thread_id with the current timestamp."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO checkpoint_meta (thread_id, created_at)
            VALUES (?, ?)
        """, (thread_id, datetime.utcnow().isoformat()))

def cleanup_old_checkpoints(db_path="checkpoints.db", days=7):
    """Delete checkpoints older than `days` days and reclaim disk space."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
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
                conn.execute(f"DELETE FROM checkpoints WHERE thread_id IN ({placeholders})", old_threads)
                conn.execute(f"DELETE FROM writes WHERE thread_id IN ({placeholders})", old_threads)
                conn.execute(f"DELETE FROM checkpoint_meta WHERE thread_id IN ({placeholders})", old_threads)
                conn.execute("VACUUM")
                logger.info(f"Checkpoint cleanup: removed {len(old_threads)} threads older than {days} days")
            else:
                logger.info("Checkpoint cleanup: nothing to remove")
    except Exception as e:
        logger.warning(f"Checkpoint cleanup failed (non-fatal): {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    init_meta_table()
    cleanup_old_checkpoints()
    with SqliteSaver.from_conn_string("checkpoints.db") as checkpointer:
        graph_builder = GraphBuilder()
        graph = graph_builder.setup_graph(checkpointer=checkpointer)
        logger.info("Graph compiled with SqliteSaver checkpointer")
        yield
    logger.info("Checkpointer connection closed")

app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/")
async def serve_frontend():
    return FileResponse("frontend/index.html")

@app.post("/analyze")
async def analyze_stock(request: Request):
    data = await request.json()

    ticker = data.get("ticker", "")
    if not ticker:
        logger.error("No ticker provided in request")
        raise HTTPException(status_code=422, detail="Ticker cannot be empty")

    timeframe = data.get("timeframe", "")
    if timeframe not in VALID_TIMEFRAMES:
        logger.error(f"Invalid timeframe '{timeframe}'")
        raise HTTPException(status_code=422, detail=f"Invalid timeframe: {timeframe}")

    asset_class = data.get("asset_class", "equity")
    if asset_class not in VALID_ASSET_CLASSES:
        logger.error(f"Invalid asset class '{asset_class}'")
        raise HTTPException(status_code=422, detail=f"Invalid asset class: {asset_class}")

    thread_id = data.get("thread_id")
    if not thread_id:
        logger.error("No thread_id provided in request")
        raise HTTPException(status_code=422, detail="thread_id cannot be empty")

    record_thread(thread_id)
    config = {"configurable": {"thread_id": thread_id}}

    try:
        state = graph.invoke({
            "ticker": ticker,
            "timeframe": timeframe,
            "asset_class": asset_class
        }, config=config)
    except FinanceAgentError as e:
        logger.error(f"Pipeline failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal pipeline error")

    logger.info(f"Pipeline paused before report_agent for {ticker} — awaiting approval")
    return {
        "status": "pending_approval",
        "thread_id": thread_id,
        "ticker": ticker,
        "intermediate": {
            "fundamentals": state.get("fundamentals"),
            "sentiment": state.get("sentiment"),
            "risk": state.get("risk"),
            "analyst_consensus": state.get("analyst_consensus")
        }
    }

@app.post("/approve/{thread_id}")
async def approve_report(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}

    try:
        state = graph.invoke(None, config=config)
    except FinanceAgentError as e:
        logger.error(f"Report generation failed for {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error resuming {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal pipeline error")

    logger.info(f"Report approved and generated for thread {thread_id}")
    return {
        "status": "complete",
        "thread_id": thread_id,
        "ticker": state["ticker"],
        "report": state["report"]
    }


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)