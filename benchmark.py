"""
benchmark.py — Parallel Execution Benchmark
============================================
Measures per-agent latency and concurrency across 5 equity tickers.
Renders a Gantt-style ASCII timeline per run showing agent overlap.

Usage:
    python benchmark.py
"""

import time
import statistics
import threading
from functools import wraps
from dotenv import load_dotenv

from langgraph.graph import StateGraph, START, END
from src.states.financestate import FinanceState
from src.llms.llm_client import LLMClient
from src.graphs.graph_builder import route_by_asset_class
from src.nodes.data_fetch import DataFetchAgent
from src.nodes.macro_regime_agent import MacroRegimeAgent
from src.nodes.fundamentals_agent import FundamentalsAgent
from src.nodes.sentiment_agent import SentimentAgent
from src.nodes.risk_agent import RiskDataAgent
from src.nodes.technical_analyst import TechnicalAnalyst
from src.nodes.bull_analyst import BullAnalyst
from src.nodes.bear_analyst import BearAnalyst
from src.nodes.valuation_analyst import ValuationAnalyst
from src.nodes.onchain_analyst import OnChainAnalyst
from src.nodes.supervisor_agent import SupervisorAgent

load_dotenv()

TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "META"]
TIMEFRAME = "3mo"

# 10 specialist agents + data_fetch as infrastructure node
# trade_gate is excluded — it's a pure HITL checkpoint, zero compute
AGENTS_IN_ORDER = [
    "data_fetch",
    "macro_regime_agent",
    "fundamentals_agent",
    "sentiment_agent",
    "risk_agent",
    "technical_analyst",
    "bull_analyst",
    "bear_analyst",
    "valuation_analyst",
    "supervisor_agent",
]

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


def timed(name, fn):
    @wraps(fn)
    def wrapper(state):
        record(name, "start")
        result = fn(state)
        record(name, "end")
        return result
    return wrapper


def build_instrumented_graph():
    llm_client = LLMClient()
    fast_llm = llm_client.get_llm("fast")
    smart_llm = llm_client.get_llm("smart")

    data_fetch  = DataFetchAgent()
    macro       = MacroRegimeAgent(fast_llm)
    fundamentals = FundamentalsAgent(fast_llm)
    sentiment   = SentimentAgent(fast_llm)
    risk        = RiskDataAgent(fast_llm)
    technical   = TechnicalAnalyst(fast_llm)
    bull        = BullAnalyst(fast_llm)
    bear        = BearAnalyst(fast_llm)
    valuation   = ValuationAnalyst(fast_llm)
    onchain     = OnChainAnalyst(fast_llm)
    supervisor  = SupervisorAgent(smart_llm)

    graph = StateGraph(FinanceState)

    graph.add_node("data_fetch",         timed("data_fetch",         data_fetch.fetch))
    graph.add_node("macro_regime_agent", timed("macro_regime_agent", macro.analyze))
    graph.add_node("fundamentals_agent", timed("fundamentals_agent", fundamentals.analyze))
    graph.add_node("sentiment_agent",    timed("sentiment_agent",    sentiment.analyze))
    graph.add_node("risk_agent",         timed("risk_agent",         risk.analyze))
    graph.add_node("technical_analyst",  timed("technical_analyst",  technical.analyze))
    graph.add_node("bull_analyst",       timed("bull_analyst",       bull.analyze))
    graph.add_node("bear_analyst",       timed("bear_analyst",       bear.analyze))
    graph.add_node("valuation_analyst",  timed("valuation_analyst",  valuation.analyze))
    graph.add_node("onchain_analyst",    timed("onchain_analyst",    onchain.analyze))
    graph.add_node("supervisor_agent",   timed("supervisor_agent",   supervisor.analyze))

    graph.add_edge(START, "data_fetch")
    graph.add_edge("data_fetch", "macro_regime_agent")
    graph.add_conditional_edges("macro_regime_agent", route_by_asset_class)

    # Wave 1 → Wave 2 (equity)
    graph.add_edge("fundamentals_agent",  "bull_analyst")
    graph.add_edge("fundamentals_agent",  "bear_analyst")
    graph.add_edge("fundamentals_agent",  "valuation_analyst")
    graph.add_edge("sentiment_agent",     "bull_analyst")
    graph.add_edge("sentiment_agent",     "bear_analyst")
    graph.add_edge("risk_agent",          "bull_analyst")
    graph.add_edge("risk_agent",          "bear_analyst")
    graph.add_edge("technical_analyst",   "bull_analyst")
    graph.add_edge("technical_analyst",   "bear_analyst")

    graph.add_edge("bull_analyst",        "supervisor_agent")
    graph.add_edge("bear_analyst",        "supervisor_agent")
    graph.add_edge("valuation_analyst",   "supervisor_agent")
    graph.add_edge("onchain_analyst",     "supervisor_agent")

    graph.add_edge("supervisor_agent", END)

    return graph.compile()


def print_timeline(timeline: list[dict], run_start: float):
    spans = {}
    for agent in AGENTS_IN_ORDER:
        starts = [e["t"] for e in timeline if e["agent"] == agent and e["event"] == "start"]
        ends   = [e["t"] for e in timeline if e["agent"] == agent and e["event"] == "end"]
        if starts and ends:
            spans[agent] = (starts[0] - run_start, ends[-1] - run_start)

    if not spans:
        return

    total_end = max(v[1] for v in spans.values())
    bar_width = 32

    print(f"\n  {'Agent':<22} {'Start':>6}  {'End':>6}  {'Dur':>6}  Timeline")
    print(f"  {'-'*72}")

    for agent in AGENTS_IN_ORDER:
        if agent not in spans:
            continue
        s, e = spans[agent]
        dur = e - s
        bar_s = int(s / total_end * bar_width)
        bar_e = int(e / total_end * bar_width)
        bar = " " * bar_s + "█" * max(1, bar_e - bar_s) + " " * (bar_width - bar_e)
        label = agent.replace("_agent", "").replace("_analyst", "").replace("_", " ").title()
        print(f"  {label:<22} {s:>5.2f}s  {e:>5.2f}s  {dur:>4.2f}s  |{bar}|")


def run_benchmark():
    graph = build_instrumented_graph()
    times = []
    agent_durations = {a: [] for a in AGENTS_IN_ORDER}

    print(f"\n{'='*60}")
    print(f"  FinanceAgent — Parallel Benchmark — {len(TICKERS)} tickers")
    print(f"{'='*60}")

    for i, ticker in enumerate(TICKERS, 1):
        clear_timeline()
        print(f"\n  [{i}/{len(TICKERS)}] {ticker}")

        run_start = time.time()
        graph.invoke({"ticker": ticker, "timeframe": TIMEFRAME})
        elapsed = time.time() - run_start
        times.append(elapsed)

        timeline = get_timeline()
        print_timeline(timeline, run_start)
        print(f"\n  Total: {elapsed:.2f}s")

        for agent in AGENTS_IN_ORDER:
            starts = [e["t"] for e in timeline if e["agent"] == agent and e["event"] == "start"]
            ends   = [e["t"] for e in timeline if e["agent"] == agent and e["event"] == "end"]
            if starts and ends:
                agent_durations[agent].append(ends[-1] - starts[0])

    avg = statistics.mean(times)
    print(f"\n{'─'*60}")
    print(f"  SUMMARY")
    print(f"{'─'*60}")
    print(f"  Avg: {avg:.2f}s  |  Min: {min(times):.2f}s  |  Max: {max(times):.2f}s")
    print(f"\n  Per-agent averages:")
    for agent in AGENTS_IN_ORDER:
        if agent_durations[agent]:
            label = agent.replace("_agent", "").replace("_analyst", "").replace("_", " ").title()
            print(f"    {label:<22} avg {statistics.mean(agent_durations[agent]):.2f}s")


if __name__ == "__main__":
    run_benchmark()