import time
import statistics
from src.graphs.graph_builder import GraphBuilder

def run_benchmark(graph, runs=5):
    times = []
    for i in range(runs):
        start = time.time()
        graph.invoke({
            "ticker": "AAPL",
            "timeframe": "3mo",
            "asset_class": "equity"
        })
        end = time.time()
        elapsed = end - start
        times.append(elapsed)
        print(f"  Run {i+1}: {elapsed:.2f}s")
    return times

if __name__ == "__main__": 

    print("🔄 Benchmarking SEQUENTIAL graph...")
    graph_builder = GraphBuilder()
    sequential_graph = graph_builder.setup_graph()
    seq_times = run_benchmark(sequential_graph)
    
    seq_avg = statistics.mean(seq_times)
    print(f"\n📊 Sequential Average: {seq_avg:.2f}s")
    print(f"   Min: {min(seq_times):.2f}s")
    print(f"   Max: {max(seq_times):.2f}s")

    print("\n⚡ Benchmarking PARALLEL graph...")
    graph_builder2 = GraphBuilder()
    parallel_graph = graph_builder2.setup_graph(mode="parallel")
    par_times = run_benchmark(parallel_graph)

    par_avg = statistics.mean(par_times)
    print(f"\n📊 Parallel Average: {par_avg:.2f}s")
    print(f"   Min: {min(par_times):.2f}s")
    print(f"   Max: {max(par_times):.2f}s")

    improvement = ((seq_avg - par_avg) / seq_avg) * 100
    print(f"\n🚀 Latency Improvement: {improvement:.1f}%")
    print(f"   Sequential: {seq_avg:.2f}s → Parallel: {par_avg:.2f}s")