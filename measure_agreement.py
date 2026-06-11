"""
measure_agreement.py — Analyst Consensus Agreement Measurement
================================================================
Runs the pipeline across 20 equity tickers and measures how often
the supervisor recommendation agrees with Wall Street consensus.

Usage:
    python measure_agreement.py
"""

import time
import os
import logging
from collections import Counter
from dotenv import load_dotenv

from src.graphs.graph_builder import GraphBuilder
from src.exceptions import FinanceAgentError

load_dotenv()

os.environ["LANGSMITH_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TICKERS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "META",
    "TSLA", "AMZN", "JPM",  "JNJ",  "V",
    "UNH",  "XOM",  "WMT",  "MA",   "PG",
    "HD",   "CVX",  "MRK",  "ABBV", "PFE",
]

RUNS_PER_TICKER = 1
SLEEP_BETWEEN_RUNS = 1
TIMEFRAME = "3mo"


def parse_agreement(analyst_agreement: str) -> str:
    text = analyst_agreement.lower()
    if text.startswith("agreed"):
        return "Agreed"
    if text.startswith("disagreed"):
        return "Disagreed"
    if "agreed" in text and "disagreed" not in text:
        return "Agreed"
    if "disagreed" in text:
        return "Disagreed"
    return "Unknown"


def run_pipeline(graph, ticker: str, retry: bool = False) -> str | None:
    try:
        state = graph.invoke({"ticker": ticker, "timeframe": TIMEFRAME})

        report = state.get("supervisor_report")
        if report is None:
            logger.warning(f"{ticker}: supervisor_report is None")
            return None

        consensus = state.get("analyst_consensus") or {}
        analyst_rec = consensus.get("recommendation", "N/A")
        pipeline_rec = report.get("recommendation", "N/A")
        agreement = report.get("analyst_agreement", "")

        print(f"     Pipeline: {pipeline_rec} | Analyst: {analyst_rec} | {agreement}")
        return parse_agreement(agreement)

    except FinanceAgentError as e:
        logger.warning(f"{ticker}: FinanceAgentError — {e}")
        return None
    except Exception as e:
        err = str(e).lower()
        if "rate limit" in err or "too many requests" in err:
            if not retry:
                print("  Rate limit — waiting 60s before retry...")
                time.sleep(60)
                return run_pipeline(graph, ticker, retry=True)
            print(f"  Rate limit persists — skipping {ticker}")
            return None
        logger.warning(f"{ticker}: Unexpected error — {e}")
        return None


def majority_vote(votes: list) -> str:
    valid = [v for v in votes if v is not None]
    if not valid:
        return "Unknown"
    return Counter(valid).most_common(1)[0][0]


def print_results_table(results: list[dict]):
    header = f"{'Ticker':<8} {'Votes (A/D/U/F)':<22} {'Majority':<12} {'Agreement':<10}"
    divider = "-" * len(header)

    print(f"\n{divider}")
    print(header)
    print(divider)

    agreed = disagreed = unknown = 0

    for r in results:
        votes = r["votes"]
        majority = r["majority"]
        a = votes.count("Agreed")
        d = votes.count("Disagreed")
        u = votes.count("Unknown")
        f = votes.count(None)
        vote_str = f"A={a} D={d} U={u} F={f}"
        sym = "Yes" if majority == "Agreed" else "No" if majority == "Disagreed" else "—"
        print(f"{r['ticker']:<8} {vote_str:<22} {majority:<12} {sym:<10}")

        if majority == "Agreed":
            agreed += 1
        elif majority == "Disagreed":
            disagreed += 1
        else:
            unknown += 1

    print(divider)
    total = len(results)
    decided = agreed + disagreed
    pct = (agreed / decided * 100) if decided > 0 else 0.0

    print("\nRESULTS")
    print(f"  Tickers    : {total}")
    print(f"  Agreed     : {agreed}")
    print(f"  Disagreed  : {disagreed}")
    print(f"  Unknown    : {unknown}")
    print(f"  Agreement  : {pct:.1f}%  ({agreed}/{decided} decided)\n")


def main():
    print("=" * 60)
    print("  FinanceAgent — Analyst Consensus Agreement Measurement")
    print(f"  Tickers: {len(TICKERS)} | Runs each: {RUNS_PER_TICKER} | Timeframe: {TIMEFRAME}")
    print("=" * 60)

    print("\nInitialising pipeline...")
    graph = GraphBuilder().setup_graph(hitl=False)
    print("Pipeline ready\n")

    results = []

    for i, ticker in enumerate(TICKERS, 1):
        print(f"[{i:02d}/{len(TICKERS)}] {ticker}")
        votes = []

        for run in range(1, RUNS_PER_TICKER + 1):
            result = run_pipeline(graph, ticker)
            votes.append(result)
            sym = "OK" if result == "Agreed" else "NO" if result == "Disagreed" else "??"
            print(f"  Run {run}: [{sym}] {result or 'FAILED'}")

            is_last = run == RUNS_PER_TICKER and i == len(TICKERS)
            if not is_last:
                time.sleep(SLEEP_BETWEEN_RUNS)

        majority = majority_vote(votes)
        results.append({"ticker": ticker, "votes": votes, "majority": majority})
        print(f"  Majority: {majority}\n")

    print_results_table(results)


if __name__ == "__main__":
    main()
