"""
measure_agreement.py — Analyst Consensus Agreement Measurement
================================================================
Runs the FinanceAgent pipeline across 20 tickers, 3 runs each.
Takes majority vote per ticker (handles LLM non-determinism).
Measures agreement rate vs Wall Street analyst consensus.

Usage:
    python measure_agreement.py

Output:
    - Live progress per ticker
    - Final agreement % 
    - Breakdown table (ticker | votes | majority | agreement)

Branch: feature/measure-agreement
"""

import time
import os
import logging
from collections import Counter
from dotenv import load_dotenv

from src.graphs.graph_builder import GraphBuilder
from src.exceptions import FinanceAgentError

# ── Setup ─────────────────────────────────────────────────────────────────────

load_dotenv()

os.environ["LANGSMITH_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")

logging.basicConfig(
    level=logging.WARNING,          # suppress agent-level INFO noise during batch run
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

TICKERS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "META",
    "TSLA", "AMZN", "JPM", "JNJ",  "V",
    "UNH",  "XOM",  "WMT", "MA",   "PG",
    "HD",   "CVX",  "MRK", "ABBV", "PFE",
]

RUNS_PER_TICKER   = 1       # majority vote over this many runs
SLEEP_BETWEEN_RUNS = 1      # seconds — OpenAI limit buffer
TIMEFRAME          = "3mo"
ASSET_CLASS        = "equity"

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_agreement(analyst_agreement: str) -> str:
    """
    Extract 'Agreed' or 'Disagreed' from the free-text analyst_agreement field.
    FinancialReport populates this as e.g.:
        'Agreed — both recommend Buy'
        'Disagreed — pipeline says Buy, analysts say Hold'
    Returns 'Agreed', 'Disagreed', or 'Unknown'.
    """
    text = analyst_agreement.lower()
    if text.startswith("agreed"):
        return "Agreed"
    elif text.startswith("disagreed"):
        return "Disagreed"
    # Fallback: scan for the word anywhere
    if "agreed" in text and "disagreed" not in text:
        return "Agreed"
    if "disagreed" in text:
        return "Disagreed"
    return "Unknown"


def run_pipeline(graph, ticker: str, _retry: bool = False) -> str | None:
    """
    Run the pipeline once for a ticker.
    Returns 'Agreed' | 'Disagreed' | 'Unknown', or None on pipeline error.

    Handles OPENAI rate limit types:
    """
    try:
        state = graph.invoke({
            "ticker":      ticker,
            "timeframe":   TIMEFRAME,
            "asset_class": ASSET_CLASS,
        })
        report = state.get("report")
        if report is None:
            logger.warning(f"{ticker}: report is None")
            return None

    
        consensus = state.get("analyst_consensus") or {}
        analyst_rec  = consensus.get("recommendation", "N/A")
        pipeline_rec = report.recommendations
        print(f"     Pipeline: {pipeline_rec} | Analyst: {analyst_rec} | {report.analyst_agreement}")

        return parse_agreement(report.analyst_agreement)

    except FinanceAgentError as e:
        logger.warning(f"{ticker}: FinanceAgentError — {e}")
        return None
    except Exception as e:
        err_str = str(e).lower()
        if "rate limit" in err_str or "too many requests" in err_str:
            if not _retry:
                wait = 60
                print(f"  ⏳ Rate limit hit — waiting {wait}s before retry...")
                time.sleep(wait)
                return run_pipeline(graph, ticker, _retry=True)
            else:
                print(f"  ❌ Rate limit still hit after retry — skipping {ticker}")
                return None
        logger.warning(f"{ticker}: Unexpected error — {e}")
        return None


def majority_vote(votes: list[str]) -> str:
    """
    Return the majority outcome from a list of 'Agreed'/'Disagreed'/'Unknown'.
    Ignores None values (failed runs). Falls back to 'Unknown' if no valid votes.
    """
    valid = [v for v in votes if v is not None]
    if not valid:
        return "Unknown"
    return Counter(valid).most_common(1)[0][0]


def print_progress(ticker: str, run: int, result: str | None):
    symbol = "✅" if result == "Agreed" else "❌" if result == "Disagreed" else "⚠️"
    label  = result if result else "FAILED"
    print(f"  Run {run}/{RUNS_PER_TICKER}: {symbol} {label}")


def print_results_table(results: list[dict]):
    """Print final breakdown table."""
    col_widths = {"ticker": 8, "votes": 22, "majority": 12, "agreement": 10}
    header = (
        f"{'Ticker':<{col_widths['ticker']}} "
        f"{'Votes (A/D/U/F)':<{col_widths['votes']}} "
        f"{'Majority':<{col_widths['majority']}} "
        f"{'Agreement':<{col_widths['agreement']}}"
    )
    divider = "-" * len(header)

    print(f"\n{divider}")
    print(header)
    print(divider)

    agreed_count    = 0
    disagreed_count = 0
    unknown_count   = 0

    for r in results:
        votes     = r["votes"]           # list of RUNS_PER_TICKER results (may include None)
        majority  = r["majority"]
        ticker    = r["ticker"]

        a = votes.count("Agreed")
        d = votes.count("Disagreed")
        u = votes.count("Unknown")
        f = votes.count(None)            # failed runs

        vote_str  = f"A={a} D={d} U={u} F={f}"
        agree_sym = "✅ Yes" if majority == "Agreed" else "❌ No" if majority == "Disagreed" else "⚠️  —"

        print(
            f"{ticker:<{col_widths['ticker']}} "
            f"{vote_str:<{col_widths['votes']}} "
            f"{majority:<{col_widths['majority']}} "
            f"{agree_sym:<{col_widths['agreement']}}"
        )

        if majority == "Agreed":
            agreed_count += 1
        elif majority == "Disagreed":
            disagreed_count += 1
        else:
            unknown_count += 1

    print(divider)

    total_decided = agreed_count + disagreed_count
    total         = len(results)
    pct           = (agreed_count / total_decided * 100) if total_decided > 0 else 0.0

    print(f"\n📊 FINAL RESULTS")
    print(f"   Tickers evaluated : {total}")
    print(f"   Agreed            : {agreed_count}")
    print(f"   Disagreed         : {disagreed_count}")
    print(f"   Unknown / failed  : {unknown_count}")
    print(f"\n   Agreement rate    : {pct:.1f}%  ({agreed_count}/{total_decided} decided tickers)")

    print(f'   "Pipeline achieved {pct:.0f}% agreement with Wall Street consensus across {total_decided} tickers"\n')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  FinanceAgent — Analyst Consensus Agreement Measurement")
    print(f"  Tickers: {len(TICKERS)} | Runs each: {RUNS_PER_TICKER} | Timeframe: {TIMEFRAME}")
    print("=" * 60)

    # Build graph once — reuse across all tickers and runs
    print("\n🔧 Initialising pipeline...")
    graph = GraphBuilder().setup_graph()
    print("✅ Pipeline ready\n")

    results = []

    for i, ticker in enumerate(TICKERS, 1):
        print(f"[{i:02d}/{len(TICKERS)}] {ticker}")
        votes = []

        for run in range(1, RUNS_PER_TICKER + 1):
            result = run_pipeline(graph, ticker)
            votes.append(result)
            print_progress(ticker, run, result)

            # Sleep between runs (skip after the last run of the last ticker)
            is_last_run    = run == RUNS_PER_TICKER
            is_last_ticker = i == len(TICKERS)
            if not (is_last_run and is_last_ticker):
                time.sleep(SLEEP_BETWEEN_RUNS)

        majority = majority_vote(votes)
        results.append({"ticker": ticker, "votes": votes, "majority": majority})
        print(f"  → Majority: {majority}\n")

    print_results_table(results)


if __name__ == "__main__":
    main()