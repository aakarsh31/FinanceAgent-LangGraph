import uvicorn
import sqlite3
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy import text
from src.graphs.graph_builder import GraphBuilder
from src.exceptions import FinanceAgentError
from langgraph.checkpoint.sqlite import SqliteSaver
from ingestion.db import get_engine, init_db
from evaluation.db_eval import init_eval_db
from evaluation.signal_store import record_signal
from alpaca_broker.trade_executor import maybe_execute_trade
from alpaca_broker.client import AlpacaClient, AlpacaError
from alpaca_broker.portfolio_stats import compute_win_rate

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


# ── Checkpoint helpers (SQLite path only — scoped to local dev) ───────────────

def init_meta_table(db_path="checkpoints.db"):
    """Create tracking table and enable WAL mode for concurrent access."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoint_meta (
                thread_id  TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
        """)


def record_thread(thread_id: str, db_path="checkpoints.db"):
    """Record a thread_id — SQLite only. No-op when using PostgresSaver."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                INSERT OR IGNORE INTO checkpoint_meta (thread_id, created_at)
                VALUES (?, ?)
            """, (thread_id, datetime.now(timezone.utc).isoformat()))
    except sqlite3.OperationalError:
        pass  # Table doesn't exist — using PostgresSaver, nothing to record


def cleanup_old_checkpoints(db_path="checkpoints.db", days=7):
    """Delete checkpoints older than `days` days (SQLite path only)."""
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
    database_url = os.getenv("DATABASE_URL")

    # Initialize Postgres — non-fatal if DATABASE_URL is not set
    if database_url:
        try:
            pg_engine = get_engine()
            init_db(pg_engine)
            init_eval_db(pg_engine)
            logger.info("Postgres engine initialized (ingestion + eval tables)")
        except Exception as e:
            logger.warning(f"Postgres initialization failed (non-fatal): {e} — agents will use live API fallback")
    else:
        logger.warning("DATABASE_URL not set — running without Postgres cache")

    # ── Checkpointer: Postgres in prod, SQLite in local dev ──────────────────
    # PostgresSaver uses the existing Railway Postgres instance — checkpoints
    # survive redeploys. SqliteSaver is the local-dev fallback (ephemeral disk
    # is fine for development since no production approvals are at risk).
    if database_url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            # Use psycopg connection string format
            conn_str = database_url.replace("postgresql://", "postgresql+psycopg://") if "postgresql+psycopg" not in database_url else database_url
            # PostgresSaver needs plain psycopg format (not SQLAlchemy)
            pg_conn_str = database_url
            if pg_conn_str.startswith("postgresql+"):
                pg_conn_str = "postgresql" + pg_conn_str[pg_conn_str.index("://"):]

            with PostgresSaver.from_conn_string(pg_conn_str) as checkpointer:
                checkpointer.setup()  # creates checkpoint tables if not exist
                graph_builder = GraphBuilder(engine=pg_engine)
                graph = graph_builder.setup_graph(checkpointer=checkpointer)
                logger.info("Graph compiled with PostgresSaver checkpointer")
                yield
        except Exception as e:
            logger.warning(f"PostgresSaver failed ({e}) — falling back to SqliteSaver")
            init_meta_table()
            cleanup_old_checkpoints()
            with SqliteSaver.from_conn_string("checkpoints.db") as checkpointer:
                graph_builder = GraphBuilder(engine=pg_engine)
                graph = graph_builder.setup_graph(checkpointer=checkpointer)
                logger.info("Graph compiled with SqliteSaver checkpointer (Postgres fallback)")
                yield
    else:
        init_meta_table()
        cleanup_old_checkpoints()
        with SqliteSaver.from_conn_string("checkpoints.db?mode=wal") as checkpointer:
            graph_builder = GraphBuilder(engine=pg_engine)
            graph = graph_builder.setup_graph(checkpointer=checkpointer)
            logger.info("Graph compiled with SqliteSaver checkpointer (local dev)")
            yield

    if pg_engine:
        pg_engine.dispose()
        logger.info("Postgres engine disposed")
    logger.info("Checkpointer connection closed")


app = FastAPI(lifespan=lifespan)

# CORS — base origins cover local dev. Set CORS_ORIGINS env var (comma-separated)
# to add production URLs without hardcoding, e.g.:
#   CORS_ORIGINS=https://financeagent-langgraph-production.up.railway.app
_base_origins = ["http://localhost:5173", "http://localhost:3000"]
_extra_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
_allowed_origins = _base_origins + _extra_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
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


@app.get("/api")
async def api_info():
    """API root — returns system info and disclaimer."""
    return {
        "name": "FinanceAgent-LangGraph",
        "version": "1.0.0",
        "disclaimer": "NOT INVESTMENT ADVICE. This is a research and educational system using paper trading only. Not registered under the Investment Advisers Act.",
        "endpoints": ["/analyze", "/approve/{thread_id}", "/portfolio", "/orders", "/evaluate"],
    }


# ── /analyze ─────────────────────────────────────────────────────────────────

# ── /analyze/stream ───────────────────────────────────────────────────────────
#
# SSE endpoint — streams node-by-node updates as the graph executes.
# Each agent completion fires a JSON event the frontend consumes immediately,
# so the Pipeline lights up in real time instead of all at once after 30-60s.
#
# Event types:
#   {type: "node_complete", node: "<name>", data: {<relevant state slice>}}
#   {type: "done",          result: <full /analyze response payload>}
#   {type: "error",         detail: "<message>"}
#
# The frontend opens an EventSource to GET /analyze/stream?ticker=AAPL&...
# We use GET (not POST) because EventSource only supports GET.

# Maps internal LangGraph node names → frontend Pipeline node ids
_NODE_ID_MAP = {
    "data_fetch":        "data_fetch",
    "macro_regime_agent": "macro",
    "fundamentals_agent": "fundamentals",
    "sentiment_agent":   "sentiment",
    "risk_agent":        "risk",
    "technical_analyst": "technical",
    "bull_analyst":      "bull",
    "bear_analyst":      "bear",
    "valuation_analyst": "valuation",
    "onchain_analyst":   "onchain",
    "supervisor_agent":  "supervisor",
}

# Extracts a small display snippet from the state slice each node writes
def _node_snippet(node_name: str, state_update: dict) -> dict:
    """Pull out the 1-2 most useful fields from each node's state update."""
    s = state_update
    if node_name == "data_fetch":
        return {"asset_class": s.get("asset_class", "equity")}
    if node_name == "macro_regime_agent":
        m = s.get("macro") or {}
        if hasattr(m, "regime_label"):  # Pydantic model
            return {"regime_label": m.regime_label}
        return {"regime_label": m.get("regime_label", "")}
    if node_name == "fundamentals_agent":
        f = s.get("fundamentals") or {}
        if hasattr(f, "EPS"):
            return {"EPS": f.EPS, "revenue_growth": f.revenue_growth}
        return {"EPS": f.get("EPS"), "revenue_growth": f.get("revenue_growth")}
    if node_name == "sentiment_agent":
        sent = s.get("sentiment") or {}
        if hasattr(sent, "sentiment_label"):
            return {"label": sent.sentiment_label, "score": sent.sentiment_score}
        return {"label": sent.get("sentiment_label", ""), "score": sent.get("sentiment_score")}
    if node_name == "risk_agent":
        r = s.get("risk") or {}
        if hasattr(r, "volatility"):
            return {"volatility": r.volatility, "beta": r.beta}
        return {"volatility": r.get("volatility"), "beta": r.get("beta")}
    if node_name == "technical_analyst":
        t = s.get("technical") or {}
        return {"signal": t.get("signal", ""), "rsi": t.get("rsi")}
    if node_name == "bull_analyst":
        b = s.get("bull_thesis") or {}
        if hasattr(b, "confidence"):
            return {"confidence": b.confidence}
        return {"confidence": b.get("confidence", "")}
    if node_name == "bear_analyst":
        b = s.get("bear_thesis") or {}
        if hasattr(b, "confidence"):
            return {"confidence": b.confidence}
        return {"confidence": b.get("confidence", "")}
    if node_name == "valuation_analyst":
        v = s.get("valuation") or {}
        if hasattr(v, "valuation_label"):
            return {"label": v.valuation_label}
        return {"label": v.get("valuation_label", "")}
    if node_name == "onchain_analyst":
        o = s.get("onchain") or {}
        if hasattr(o, "network_health"):
            return {"network_health": o.network_health, "fear_greed": o.fear_greed_score}
        return {"network_health": o.get("network_health", ""), "fear_greed": o.get("fear_greed_score")}
    if node_name == "supervisor_agent":
        rep = s.get("supervisor_report") or {}
        if hasattr(rep, "recommendation"):
            return {"recommendation": rep.recommendation, "confidence": rep.confidence}
        return {"recommendation": rep.get("recommendation", ""), "confidence": rep.get("confidence", "")}
    return {}


def _sse(event_type: str, payload: dict) -> str:
    """Format a single SSE message."""
    import json
    data = json.dumps({"type": event_type, **payload})
    return f"data: {data}\n\n"


@app.get("/analyze/stream")
async def analyze_stream(
    ticker: str,
    timeframe: str,
    thread_id: str,
    request: Request,
):
    """
    SSE stream of agent completions for a given ticker analysis.
    Frontend opens as: new EventSource('/analyze/stream?ticker=AAPL&timeframe=3mo&thread_id=...')
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=422, detail="ticker required")
    if timeframe not in VALID_TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"Invalid timeframe. Valid: {VALID_TIMEFRAMES}")
    if not thread_id.strip():
        raise HTTPException(status_code=422, detail="thread_id required")

    logger.info(f"/analyze/stream — ticker={ticker} timeframe={timeframe} thread_id={thread_id}")
    record_thread(thread_id)
    config = {"configurable": {"thread_id": thread_id}}

    async def event_generator():
        import json

        try:
            # graph.stream yields {node_name: state_update} after each node completes
            # We run it in a thread since LangGraph's stream() is synchronous
            # Bridge: sync graph.stream() → async queue
            # queue.put_nowait is NOT thread-safe — use call_soon_threadsafe
            # so the background thread can safely wake the event loop.
            queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            def _put(item):
                loop.call_soon_threadsafe(queue.put_nowait, item)

            def stream_worker():
                try:
                    for chunk in graph.stream(
                        {"ticker": ticker, "timeframe": timeframe},
                        config,
                        stream_mode="updates",
                    ):
                        _put(("chunk", chunk))
                    _put(("done", None))
                except Exception as e:
                    _put(("error", str(e)))

            # Start stream in background thread
            loop.run_in_executor(None, stream_worker)

            final_state = None

            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info(f"SSE client disconnected — {ticker} {thread_id}")
                    break

                try:
                    msg_type, payload = await asyncio.wait_for(queue.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    yield _sse("error", {"detail": "Pipeline timeout"})
                    break

                if msg_type == "error":
                    yield _sse("error", {"detail": payload})
                    break

                if msg_type == "done":
                    break

                if msg_type == "chunk":
                    chunk = payload
                    for node_name, state_update in chunk.items():
                        if node_name == "__interrupt__":
                            continue  # trade_gate interrupt — handled below
                        frontend_id = _NODE_ID_MAP.get(node_name)
                        if not frontend_id:
                            continue
                        snippet = _node_snippet(node_name, state_update)
                        yield _sse("node_complete", {
                            "node": frontend_id,
                            "node_raw": node_name,
                            "data": snippet,
                        })

            # Graph is paused at trade_gate — read the final suspended state
            try:
                snapshot = await asyncio.to_thread(graph.get_state, config)
                state = snapshot.values
            except Exception as e:
                yield _sse("error", {"detail": f"Could not read final state: {e}"})
                return

            asset_class = state.get("asset_class", "equity")
            supervisor_report = state.get("supervisor_report", {})

            intermediate = {
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
                intermediate.update({"onchain": state.get("onchain")})

            # Pydantic models → dicts for JSON serialisation
            def _to_dict(v):
                if v is None:
                    return None
                if hasattr(v, "model_dump"):
                    return v.model_dump()
                return v

            intermediate = {k: _to_dict(v) for k, v in intermediate.items()}
            if hasattr(supervisor_report, "model_dump"):
                supervisor_report = supervisor_report.model_dump()

            yield _sse("done", {
                "result": {
                    "status": "pending_approval",
                    "thread_id": thread_id,
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "supervisor_report": supervisor_report,
                    "intermediate": intermediate,
                    "data_provenance": _to_dict(state.get("data_provenance", {})),
                }
            })

        except FinanceAgentError as e:
            logger.error(f"SSE pipeline failed for {ticker}: {e}", exc_info=True)
            yield _sse("error", {"detail": str(e)})
        except Exception as e:
            logger.error(f"SSE unexpected error for {ticker}: {e}", exc_info=True)
            yield _sse("error", {"detail": "Internal pipeline error"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # tells nginx not to buffer SSE
            "Connection": "keep-alive",
        },
    )


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
        state = await asyncio.to_thread(
            graph.invoke,
            {"ticker": ticker, "timeframe": timeframe},
            config,
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


# ── HITL auth + audit ────────────────────────────────────────────────────────

def verify_approver_key(x_api_key: str = Header(default=None)):
    """
    Require X-API-Key header matching APPROVER_API_KEY env var.

    Fail-closed by default:
    - APPROVER_API_KEY set → key must match or 401
    - AUTH_DISABLED=true  → auth explicitly disabled (dev only)
    - Neither set         → 503 (misconfigured, refuse to serve)

    Returns key fingerprint (last 8 chars) for audit log attribution.
    """
    required_key = os.getenv("APPROVER_API_KEY")
    auth_disabled = os.getenv("AUTH_DISABLED", "").lower() == "true"

    if auth_disabled:
        logger.warning("HITL auth disabled via AUTH_DISABLED=true — dev mode only")
        return "auth_disabled"

    if not required_key:
        logger.error("APPROVER_API_KEY not set and AUTH_DISABLED not true — refusing to serve approval endpoints")
        raise HTTPException(
            status_code=503,
            detail="Approval endpoint is misconfigured — APPROVER_API_KEY not set. Set AUTH_DISABLED=true to explicitly disable auth in dev."
        )

    if x_api_key != required_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    # Return last 8 chars of key as fingerprint for audit attribution
    return f"key:...{x_api_key[-8:]}" if x_api_key else "unknown"


def _get_existing_decision(thread_id: str) -> str | None:
    """Return existing decision for thread_id from audit log, or None."""
    if not pg_engine:
        return None
    try:
        with pg_engine.connect() as conn:
            row = conn.execute(
                text("SELECT decision FROM approval_audit WHERE thread_id = :tid"),
                {"tid": thread_id}
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _claim_decision(thread_id: str, decision: str, ticker: str, recommendation: str, confidence: str, decided_by: str = "default") -> bool:
    """
    Atomically claim a decision for a thread_id via INSERT.
    Returns True if this caller won the race (insert succeeded).
    Raises HTTPException(409) if the existing decision conflicts.
    """
    if not pg_engine:
        return True  # No DB — allow in local dev without Postgres

    try:
        with pg_engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO approval_audit
                    (thread_id, decision, ticker, recommendation, confidence, decided_by)
                VALUES
                    (:tid, :decision, :ticker, :rec, :conf, :decided_by)
            """), {
                "tid": thread_id,
                "decision": decision,
                "ticker": ticker,
                "rec": recommendation,
                "conf": confidence,
                "decided_by": decided_by,
            })
            conn.commit()
            logger.info(f"Audit log written — thread_id={thread_id} decision={decision} ticker={ticker} decided_by={decided_by}")
            return True
    except Exception as e:
        error_str = str(e).lower()
        if "unique" in error_str or "duplicate" in error_str:
            # Idempotency conflict — 409 with the existing decision
            existing = _get_existing_decision(thread_id)
            if existing == decision:
                raise HTTPException(status_code=409, detail=f"Thread {thread_id} already {decision}")
            else:
                raise HTTPException(status_code=409, detail=f"Thread {thread_id} already {existing} — cannot {decision}")
        # Non-unique DB error — fail closed: can't record decision, won't execute trade
        logger.error(f"Audit log write failed — failing closed, trade not executed: {e}")
        raise HTTPException(status_code=500, detail="Audit log write failed — trade not executed. Retry or contact support.")


# ── /approve/{thread_id} ──────────────────────────────────────────────────────

@app.post("/approve/{thread_id}")
async def approve(thread_id: str, decided_by: str = Depends(verify_approver_key)):
    config = {"configurable": {"thread_id": thread_id}}
    logger.info(f"/approve — thread_id={thread_id}")

    # Pre-check: if a decision already exists, 409 immediately — before resuming graph
    # This closes the resume hole: rejected threads must never advance past trade_gate
    existing = _get_existing_decision(thread_id)
    if existing == "approved":
        raise HTTPException(status_code=409, detail=f"Thread {thread_id} already approved")
    if existing == "rejected":
        raise HTTPException(status_code=409, detail=f"Thread {thread_id} was rejected — cannot approve")

    try:
        # Resume past trade_gate — supervisor already ran in /analyze
        state = await asyncio.to_thread(graph.invoke, None, config)
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

    # Claim the decision atomically — INSERT first, 409 on conflict
    # This is the idempotency guard: whoever wins the INSERT proceeds to trade
    _claim_decision(
        thread_id=thread_id,
        decision="approved",
        ticker=ticker,
        recommendation=supervisor_report.get("recommendation", "") if supervisor_report else "",
        confidence=supervisor_report.get("confidence", "") if supervisor_report else "",
        decided_by=decided_by or "default",
    )

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
        trade_result = await asyncio.to_thread(
            maybe_execute_trade,
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


# ── /reject/{thread_id} ───────────────────────────────────────────────────────

@app.post("/reject/{thread_id}")
async def reject(thread_id: str, decided_by: str = Depends(verify_approver_key)):
    """
    Reject a pending analysis — records the decision and blocks future approval.
    The graph state is NOT resumed — trade_gate is never passed.
    """
    logger.info(f"/reject — thread_id={thread_id}")

    # Get state to extract ticker/recommendation for audit log
    config = {"configurable": {"thread_id": thread_id}}
    ticker = "unknown"
    recommendation = ""
    confidence = ""
    try:
        # graph.get_state does I/O (Postgres checkpointer) — must not block the event loop
        snapshot = await asyncio.to_thread(graph.get_state, config)
        state = snapshot.values
        ticker = state.get("ticker", "unknown")
        report = state.get("supervisor_report") or {}
        recommendation = report.get("recommendation", "") if isinstance(report, dict) else ""
        confidence = report.get("confidence", "") if isinstance(report, dict) else ""
    except Exception as e:
        logger.warning(f"Could not read state for rejection audit: {e}")

    # Atomically claim the rejection — INSERT first, 409 on conflict
    _claim_decision(
        thread_id=thread_id,
        decision="rejected",
        ticker=ticker,
        recommendation=recommendation,
        confidence=confidence,
        decided_by=decided_by or "default",
    )

    return {
        "status": "rejected",
        "thread_id": thread_id,
        "ticker": ticker,
        "recommendation": recommendation,
        "message": "Analysis rejected — no trade will be placed. Thread cannot be subsequently approved.",
    }




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

    # Fetch latest eval_run — fresh connection after maturation
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

            # Per-rule attribution query
            from evaluation.eval_job import _binomial_ci
            rule_rows = conn.execute(sql_text("""
                SELECT
                    policy_rule_fired,
                    COUNT(*) as n,
                    SUM(CASE WHEN hit = true THEN 1 ELSE 0 END) as hits,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END) as hit_rate,
                    AVG(return_30d - spy_return_30d)
                        FILTER (WHERE spy_return_30d IS NOT NULL) as avg_alpha
                FROM pipeline_signals
                WHERE eval_status = 'evaluated'
                  AND recommendation != 'Hold'
                  AND policy_rule_fired IS NOT NULL
                GROUP BY policy_rule_fired
                ORDER BY n DESC
            """)).fetchall()

            # Divergence stats
            divergence = conn.execute(sql_text("""
                SELECT
                    COUNT(*) FILTER (WHERE analyst_override = true) as override_n,
                    SUM(CASE WHEN hit = true AND analyst_override = true THEN 1 ELSE 0 END) as override_hits,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE analyst_override = true) as override_hit_rate,
                    AVG(CASE WHEN hit = true THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE analyst_override = false) as consensus_hit_rate,
                    AVG(spy_return_30d) FILTER (WHERE spy_return_30d IS NOT NULL) as avg_spy_return
                FROM pipeline_signals
                WHERE eval_status = 'evaluated' AND recommendation != 'Hold'
            """)).fetchone()

            # Confidence intervals
            n_overall = latest.signals_evaluated or 0
            hits_overall = round((latest.hit_rate_overall or 0) * n_overall)
            ci_overall = _binomial_ci(hits_overall, n_overall)

            return {
                "status": "ok",
                "model_version": latest.model_version,
                "signals_evaluated": latest.signals_evaluated,
                "pending_signals": total_pending,
                "total_evaluated": total_evaluated,
                "sample_size_note": "n too small for statistical significance" if n_overall < 30 else None,
                "hit_rate": {
                    "overall": round(float(latest.hit_rate_overall) * 100, 1) if latest.hit_rate_overall is not None else 0.0,
                    "buy": round(float(latest.hit_rate_buy) * 100, 1) if latest.hit_rate_buy is not None else 0.0,
                    "sell": round(float(latest.hit_rate_sell) * 100, 1) if latest.hit_rate_sell is not None else None,
                    "high_confidence": round(float(latest.hit_rate_high_confidence) * 100, 1) if latest.hit_rate_high_confidence is not None else None,
                    "medium_confidence": round(float(latest.hit_rate_medium_confidence) * 100, 1) if latest.hit_rate_medium_confidence is not None else None,
                    "ci_95": [round(ci_overall[0] * 100, 1), round(ci_overall[1] * 100, 1)],
                    "definition": "SPY-relative: Buy is a hit if it beat SPY over 30 days",
                },
                "baselines": {
                    "always_buy_spy_rate": round(divergence.avg_spy_return * 100, 2) if divergence and divergence.avg_spy_return else None,
                    "note": "always_buy_spy_rate = avg SPY return over evaluated windows — the passive benchmark",
                },
                "divergence": {
                    "override_n": divergence.override_n if divergence else 0,
                    "override_hit_rate": round(divergence.override_hit_rate * 100, 1) if divergence and divergence.override_hit_rate else None,
                    "consensus_hit_rate": round(divergence.consensus_hit_rate * 100, 1) if divergence and divergence.consensus_hit_rate else None,
                    "note": "override = signals where pipeline diverged from Wall Street consensus",
                },
                "returns": {
                    "avg_return_buy_pct": round(latest.avg_return_buy * 100, 2) if latest.avg_return_buy else None,
                    "avg_return_sell_pct": round(latest.avg_return_sell * 100, 2) if latest.avg_return_sell else None,
                    "avg_alpha_buy_pct": round(latest.avg_alpha_buy * 100, 2) if latest.avg_alpha_buy else None,
                },
                "per_rule": [
                    {
                        "rule": r.policy_rule_fired,
                        "n": r.n,
                        "hit_rate": round(float(r.hit_rate) * 100, 1) if r.hit_rate is not None else None,
                        "avg_alpha_pct": round(float(r.avg_alpha) * 100, 2) if r.avg_alpha is not None else None,
                        "ci_95": [
                            round(_binomial_ci(r.hits or 0, r.n)[0] * 100, 1),
                            round(_binomial_ci(r.hits or 0, r.n)[1] * 100, 1),
                        ],
                    }
                    for r in rule_rows
                ],
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

        # Win rate — computed from closed round trips only via portfolio_stats
        win_stats = compute_win_rate(orders)
        filled = [o for o in orders if o["status"] == "filled"]

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
                "closed_trades": win_stats["closed_trades"],
                "win_rate": win_stats["win_rate"],
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