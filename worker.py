"""
worker.py — Railway worker service entry point

This is the second Railway service. It:
1. Loads environment variables
2. Creates the Postgres engine
3. Initializes tables (safe to run on every startup)
4. Optionally runs a immediate warm-up ingestion on first boot
5. Starts the blocking scheduler

Start command in Railway:
    python worker.py

Environment variables required (same as web service):
    DATABASE_URL
    FMP_API_KEY
    FRED_API_KEY
"""

import logging
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from dotenv import load_dotenv


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for Railway health checks."""
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        pass  # suppress access logs


def start_health_server(port: int = 8080):
    """Start health check HTTP server in a background thread."""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server

load_dotenv()

# ── Logging setup ─────────────────────────────────────────────────────────────
# Configure before any other imports so all loggers inherit this format.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("worker")

# ── Imports after logging ─────────────────────────────────────────────────────

from ingestion.db import get_engine, init_db
from evaluation.db_eval import init_eval_db
from evaluation.eval_job import run_eval_maturation
from ingestion.scheduler import (
    build_scheduler,
    run_fundamentals_ingestion,
    run_macro_ingestion,
    run_news_ingestion,
)


def run_checkpoint_cleanup(engine, days: int = 7):
    """
    Delete LangGraph checkpoint rows older than `days` days from Postgres.
    PostgresSaver stores checkpoints in checkpoints, checkpoint_blobs, and
    checkpoint_writes tables. We clean up by thread_id based on checkpoint
    timestamp. Non-fatal — logs failure and continues.
    """
    from sqlalchemy import text
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.isoformat()

    try:
        with engine.connect() as conn:
            # Get thread_ids older than cutoff
            result = conn.execute(text("""
                SELECT DISTINCT thread_id FROM checkpoints
                WHERE metadata->>'created_at' < :cutoff
                   OR checkpoint_id IN (
                       SELECT checkpoint_id FROM checkpoints
                       WHERE (metadata->>'ts')::timestamptz < :cutoff_ts
                   )
            """), {"cutoff": cutoff_str, "cutoff_ts": cutoff_str})
            old_threads = [row[0] for row in result.fetchall()]

            if not old_threads:
                logger.info(f"[checkpoint_cleanup] Nothing to remove (cutoff: {cutoff_str[:10]})")
                return

            # Delete from all three checkpoint tables
            for table in ["checkpoint_writes", "checkpoint_blobs", "checkpoints"]:
                conn.execute(text(f"""
                    DELETE FROM {table}
                    WHERE thread_id = ANY(:threads)
                """), {"threads": old_threads})

            conn.commit()
            logger.info(f"[checkpoint_cleanup] Removed {len(old_threads)} threads older than {days} days")
    except Exception as e:
        logger.warning(f"[checkpoint_cleanup] Failed (non-fatal): {e}")


def main():
    logger.info("Worker starting up...")

    # Start health check server — prevents Railway from restarting the worker
    # Railway pings this endpoint to verify the service is alive
    health_port = int(os.getenv("PORT", "8080"))
    start_health_server(health_port)
    logger.info(f"Health check server started on port {health_port}")

    # Validate required env vars before doing anything
    required = ["DATABASE_URL", "FMP_API_KEY", "FRED_API_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logger.error(f"Missing required environment variables: {missing}")
        sys.exit(1)

    # Initialize Postgres
    logger.info("Connecting to Postgres...")
    engine = get_engine()
    init_db(engine)
    init_eval_db(engine)
    logger.info("Database ready (ingestion + eval tables)")

    # ── Warm-up ingestion ─────────────────────────────────────────────────────
    # On first deploy, Postgres is empty. Run an immediate ingestion pass
    # so the cache is warm before the first user request hits.
    # Controlled by WARM_UP_ON_START env var — set to "true" on first deploy,
    # remove it after (nightly scheduler takes over).

    if os.getenv("WARM_UP_ON_START", "").lower() == "true":
        logger.info("WARM_UP_ON_START=true — running immediate ingestion before scheduler")
        try:
            logger.info("Warm-up: starting fundamentals...")
            f_result = run_fundamentals_ingestion(engine)
            logger.info(f"Warm-up fundamentals: {f_result.summary()}")

            logger.info("Warm-up: starting news...")
            n_result = run_news_ingestion(engine)
            logger.info(f"Warm-up news: {n_result.summary()}")

            logger.info("Warm-up: starting macro...")
            m_result = run_macro_ingestion(engine)
            logger.info(f"Warm-up macro: {m_result.summary()}")

            logger.info("Warm-up ingestion complete — handing off to scheduler")
        except Exception as e:
            # Warm-up failure is non-fatal — scheduler still starts
            logger.error(f"Warm-up ingestion failed: {e} — continuing to scheduler", exc_info=True)
    else:
        logger.info("WARM_UP_ON_START not set — skipping warm-up, scheduler will run at 02:00 UTC")

    # ── Start scheduler ───────────────────────────────────────────────────────
    scheduler = build_scheduler(engine)

    # Add Postgres checkpoint cleanup — runs nightly at 03:00 UTC
    # Deletes suspended graph state older than 7 days to prevent unbounded growth
    scheduler.add_job(
        lambda: run_checkpoint_cleanup(engine, days=7),
        trigger="cron",
        hour=3,
        minute=0,
        id="checkpoint_cleanup",
        name="Nightly Postgres Checkpoint Cleanup",
        replace_existing=True,
    )
    logger.info("Scheduler built — jobs registered:")
    for job in scheduler.get_jobs():
        logger.info(f"  {job.name} → id: {job.id}")

    logger.info("Starting blocking scheduler — worker is live")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker shutting down gracefully")
        scheduler.shutdown()


if __name__ == "__main__":
    main()