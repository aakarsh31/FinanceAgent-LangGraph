from langgraph.graph import StateGraph, START, END
from src.llms.llm_client import LLMClient
from src.states.financestate import FinanceState
from src.nodes.data_fetch import DataFetchAgent
from src.nodes.fundamentals_agent import FundamentalsAgent
from src.nodes.sentiment_agent import SentimentAgent
from src.nodes.risk_agent import RiskDataAgent
from src.nodes.report_agent import ReportAgent
import os

def route_by_asset_class(state: FinanceState) -> list[str]:
    if state['asset_class'] == "equity":
        return ["fundamentals_agent","sentiment_agent","risk_agent"]

    elif state['asset_class'] == "crypto":
        return ["sentiment_agent","risk_agent"]
    
    elif state['asset_class'] == "macro":
        return ["fundamentals_agent","sentiment_agent","risk_agent"]
    else:
        raise ValueError(f"Invalid asset class '{state['asset_class']}'. Valid: 'equity', 'crypto', 'macro'")
        
class GraphBuilder:
    def __init__(self):
        self.llm_client = LLMClient()
        self.fast_llm = self.llm_client.get_llm("fast")
        self.smart_llm = self.llm_client.get_llm("smart")
        self.graph = StateGraph(FinanceState) 


    def build_sequential_graph(self):
        #Initiate agents

        data_fetch = DataFetchAgent()
        fundamentals = FundamentalsAgent(self.fast_llm)
        sentiment = SentimentAgent(self.fast_llm)
        risk = RiskDataAgent(self.fast_llm)
        report = ReportAgent(self.smart_llm)

        # register nodes
        self.graph.add_node("data_fetch", data_fetch.fetch)
        self.graph.add_node("fundamentals_agent",fundamentals.analyze)
        self.graph.add_node("sentiment_agent", sentiment.analyze)
        self.graph.add_node("risk_agent", risk.analyze)
        self.graph.add_node("report_agent", report.analyze)

        self.graph.add_edge(START, "data_fetch")
        self.graph.add_edge("data_fetch", "fundamentals_agent")
        self.graph.add_edge("fundamentals_agent", "sentiment_agent")
        self.graph.add_edge("sentiment_agent", "risk_agent")
        self.graph.add_edge("risk_agent", "report_agent")

        # Report → End
        self.graph.add_edge("report_agent", END)

        return self.graph
    
    def build_parallel_graph(self):
    #instantiate all agents
        data_fetch = DataFetchAgent()
        fundamentals = FundamentalsAgent(self.fast_llm)
        sentiment = SentimentAgent(self.fast_llm)
        risk = RiskDataAgent(self.fast_llm)
        report = ReportAgent(self.smart_llm)

        #register nodes
        self.graph.add_node("data_fetch", data_fetch.fetch)
        self.graph.add_node("fundamentals_agent", fundamentals.analyze)
        self.graph.add_node("sentiment_agent", sentiment.analyze)
        self.graph.add_node("risk_agent", risk.analyze)
        self.graph.add_node("report_agent", report.analyze)

        #wire edges
        self.graph.add_edge(START, "data_fetch")

        #DataFetch feeds three agents simultaneously
        self.graph.add_conditional_edges(
            "data_fetch",           # source node
            route_by_asset_class,   # router function
        )

        #all three feed into report
        self.graph.add_edge("fundamentals_agent", "report_agent")
        self.graph.add_edge("sentiment_agent", "report_agent")
        self.graph.add_edge("risk_agent", "report_agent")

        # Report → End
        self.graph.add_edge("report_agent", END)

        return self.graph
    
    def setup_graph(self, mode="parallel", checkpointer=None):
        if mode == "parallel":
            self.build_parallel_graph()
        else:
            self.build_sequential_graph()
        return self.graph.compile(checkpointer=checkpointer)