"""
benchmark.py — Sequential vs Parallel Execution Benchmark
==========================================================
Measures two things

1. CONCURRENCY PROOF — per-node start/end timestamps showing
   agents overlap in parallel mode but not in sequential mode.

2. AGGREGATE LATENCY — average wall-clock time across 5 tickers,
   with per-agent breakdown so the speedup is explainable.

Usage:
    python benchmark.py
"""

import time
import statistics
import threading
from functools import wraps
from src.graphs.graph_builder import GraphBuilder
from src.llms.llm_client import LLMClient
from src.nodes.fundamentals_agent import FundamentalsAgent
from src.nodes.sentiment_agent import SentimentAgent
from src.nodes.risk_agent import RiskDataAgent
from src.nodes.report_agent import ReportAgent
from src.nodes.data_fetch import DataFetchAgent
from langgraph.graph import StateGraph, START, END
from src.states.financestate import FinanceState
from src.graphs.graph_builder import route_by_asset_class

# ── Config ────────────────────────────────────────────────────────────────────

TICKERS   = ["AAPL", "MSFT", "NVDA", "GOOGL", "META"]
TIMEFRAME = "3mo"
ASSET_CLASS = "equity"

# ── Timing Registry ───────────────────────────────────────────────────────────

_lock = threading.Lock()
_timeline: list[dict] = []

def record(agent: str, event: str):
    with _lock:
        _timeline.append({"agent": agent, "event": event, "t": time.time()})

def clear_timeline():
    with _lock:
        _timeline.clear()

def get_timeline():
    with _lock:
        return list(_timeline)

# ── Instrumented Agent Wrappers ───────────────────────────────────────────────

def timed(name, fn):
    """Wrap an agent's analyze/fetch method with start/end recording."""
    @wraps(fn)
    def wrapper(state):
        record(name, "start")
        result = fn(state)
        record(name, "end")
        return result
    return wrapper

def build_instrumented_graph(mode: str, checkpointer=None):
    """Build a graph with timing wrappers around every node."""
    llm_client = LLMClient()
    fast_llm   = llm_client.get_llm("fast")
    smart_llm  = llm_client.get_llm("smart")

    data_fetch   = DataFetchAgent()
    fundamentals = FundamentalsAgent(fast_llm)
    sentiment    = SentimentAgent(fast_llm)
    risk         = RiskDataAgent(fast_llm)
    report       = ReportAgent(smart_llm)

    graph = StateGraph(FinanceState)

    graph.add_node("data_fetch",        timed("data_fetch",        data_fetch.fetch))
    graph.add_node("fundamentals_agent",timed("fundamentals_agent",fundamentals.analyze))
    graph.add_node("sentiment_agent",   timed("sentiment_agent",   sentiment.analyze))
    graph.add_node("risk_agent",        timed("risk_agent",        risk.analyze))
    graph.add_node("report_agent",      timed("report_agent",      report.analyze))

    graph.add_edge(START, "data_fetch")

    if mode == "parallel":
        graph.add_conditional_edges("data_fetch", route_by_asset_class)
        graph.add_edge("fundamentals_agent", "report_agent")
        graph.add_edge("sentiment_agent",    "report_agent")
        graph.add_edge("risk_agent",         "report_agent")
    else:
        graph.add_edge("data_fetch",          "fundamentals_agent")
        graph.add_edge("fundamentals_agent",  "sentiment_agent")
        graph.add_edge("sentiment_agent",     "risk_agent")
        graph.add_edge("risk_agent",          "report_agent")

    graph.add_edge("report_agent", END)
    return graph.compile(checkpointer=checkpointer)

# ── Timeline Printer ──────────────────────────────────────────────────────────

def print_timeline(timeline: list[dict], run_start: float):
    """Print a Gantt-style timeline showing agent overlap."""
    agents = ["data_fetch", "fundamentals_agent", "sentiment_agent", "risk_agent", "report_agent"]
    spans  = {}

    for agent in agents:
        starts = [e["t"] for e in timeline if e["agent"] == agent and e["event"] == "start"]
        ends   = [e["t"] for e in timeline if e["agent"] == agent and e["event"] == "end"]
        if starts and ends:
            spans[agent] = (starts[0] - run_start, ends[-1] - run_start)

    print(f"\n  {'Agent':<22} {'Start':>6}  {'End':>6}  {'Duration':>8}  Timeline")
    print(f"  {'-'*70}")

    total_end = max(v[1] for v in spans.values()) if spans else 1
    bar_width  = 30

    for agent in agents:
        if agent not in spans:
            continue
        s, e   = spans[agent]
        dur    = e - s
        bar_s  = int(s / total_end * bar_width)
        bar_e  = int(e / total_end * bar_width)
        bar    = " " * bar_s + "█" * max(1, bar_e - bar_s) + " " * (bar_width - bar_e)
        label  = agent.replace("_agent", "").replace("_", " ").title()
        print(f"  {label:<22} {s:>5.2f}s  {e:>5.2f}s  {dur:>6.2f}s  |{bar}|")

# ── Benchmark Runner ──────────────────────────────────────────────────────────

def run_benchmark(mode: str):
    graph  = build_instrumented_graph(mode)
    label  = "SEQUENTIAL" if mode == "sequential" else "PARALLEL"
    times  = []
    all_agent_durations = {
        "fundamentals_agent": [],
        "sentiment_agent":    [],
        "risk_agent":         [],
        "report_agent":       [],
    }

    print(f"\n{'='*60}")
    print(f"  {'🔄' if mode == 'sequential' else '⚡'} {label} GRAPH — {len(TICKERS)} tickers")
    print(f"{'='*60}")

    for i, ticker in enumerate(TICKERS, 1):
        clear_timeline()
        print(f"\n  [{i}/{len(TICKERS)}] {ticker}")

        run_start = time.time()
        graph.invoke({
            "ticker":      ticker,
            "timeframe":   TIMEFRAME,
            "asset_class": ASSET_CLASS,
        })
        elapsed = time.time() - run_start
        times.append(elapsed)

        timeline = get_timeline()
        print_timeline(timeline, run_start)
        print(f"\n  ⏱  Total: {elapsed:.2f}s")

        # Collect per-agent durations
        for agent in all_agent_durations:
            starts = [e["t"] for e in timeline if e["agent"] == agent and e["event"] == "start"]
            ends   = [e["t"] for e in timeline if e["agent"] == agent and e["event"] == "end"]
            if starts and ends:
                all_agent_durations[agent].append(ends[-1] - starts[0])

    avg = statistics.mean(times)
    print(f"\n{'─'*60}")
    print(f"  📊 {label} SUMMARY")
    print(f"{'─'*60}")
    print(f"  Average : {avg:.2f}s  |  Min: {min(times):.2f}s  |  Max: {max(times):.2f}s")
    print(f"\n  Per-agent averages:")
    for agent, durations in all_agent_durations.items():
        if durations:
            label_short = agent.replace("_agent", "").replace("_", " ").title()
            print(f"    {label_short:<20} avg {statistics.mean(durations):.2f}s")

    return avg, times

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  FinanceAgent — Sequential vs Parallel Benchmark")
    print(f"  Tickers: {TICKERS}")
    print("=" * 60)

    seq_avg, seq_times = run_benchmark("sequential")
    par_avg, par_times = run_benchmark("parallel")

    improvement = ((seq_avg - par_avg) / seq_avg) * 100

    print(f"\n{'='*60}")
    print(f"  🚀 FINAL COMPARISON")
    print(f"{'='*60}")
    print(f"  Sequential avg : {seq_avg:.2f}s")
    print(f"  Parallel avg   : {par_avg:.2f}s")
    print(f"  Improvement    : {improvement:.1f}%")
    print()
    if improvement > 0:
        print(f'  Resume bullet →')
        print(f'  "Parallel execution reduced avg latency by {improvement:.0f}% vs sequential')
        print(f'   baseline across {len(TICKERS)} tickers ({seq_avg:.1f}s → {par_avg:.1f}s)"')
    else:
        print(f'  Note: OpenAI network jitter dominates at this scale.')
        print(f'  See per-agent timelines above for concurrency proof.')
    print()