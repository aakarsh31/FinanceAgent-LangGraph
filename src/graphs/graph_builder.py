from langgraph.graph import StateGraph, START, END

from src.llms.llm_client import LLMClient
from src.states.financestate import FinanceState

from src.nodes.data_fetch import DataFetchAgent
from src.nodes.macro_regime_agent import MacroRegimeAgent
from src.nodes.fundamentals_agent import FundamentalsAgent
from src.nodes.sentiment_agent import SentimentAgent
from src.nodes.risk_agent import RiskDataAgent
from src.nodes.bull_analyst import BullAnalyst
from src.nodes.bear_analyst import BearAnalyst
from src.nodes.valuation_analyst import ValuationAnalyst
from src.nodes.onchain_analyst import OnChainAnalyst
from src.nodes.supervisor_agent import SupervisorAgent


def route_by_asset_class(state: FinanceState) -> list[str]:
    asset_class = state["asset_class"]
    if asset_class == "equity":
        return ["fundamentals_agent", "sentiment_agent", "risk_agent"]
    elif asset_class == "crypto":
        return ["onchain_analyst", "sentiment_agent", "risk_agent"]
    else:
        raise ValueError(
            f"Invalid asset_class '{asset_class}' in state. "
            f"Expected 'equity' or 'crypto'."
        )


class GraphBuilder:

    def __init__(self):
        self.llm_client = LLMClient()
        self.fast_llm = self.llm_client.get_llm("fast")
        self.smart_llm = self.llm_client.get_llm("smart")

    def build(self):
        graph = StateGraph(FinanceState)

        data_fetch = DataFetchAgent()
        macro = MacroRegimeAgent(self.fast_llm)
        fundamentals = FundamentalsAgent(self.fast_llm)
        sentiment = SentimentAgent(self.fast_llm)
        risk = RiskDataAgent(self.fast_llm)
        bull = BullAnalyst(self.fast_llm)
        bear = BearAnalyst(self.fast_llm)
        valuation = ValuationAnalyst(self.fast_llm)
        onchain = OnChainAnalyst(self.fast_llm)
        supervisor = SupervisorAgent(self.smart_llm)

        graph.add_node("data_fetch", data_fetch.fetch)
        graph.add_node("macro_regime_agent", macro.analyze)
        graph.add_node("fundamentals_agent", fundamentals.analyze)
        graph.add_node("sentiment_agent", sentiment.analyze)
        graph.add_node("risk_agent", risk.analyze)
        graph.add_node("bull_analyst", bull.analyze)
        graph.add_node("bear_analyst", bear.analyze)
        graph.add_node("valuation_analyst", valuation.analyze)
        graph.add_node("onchain_analyst", onchain.analyze)
        graph.add_node("supervisor_agent", supervisor.analyze)

        graph.add_edge(START, "data_fetch")
        graph.add_edge("data_fetch", "macro_regime_agent")
        graph.add_conditional_edges("macro_regime_agent", route_by_asset_class)

        # equity wave 1 → wave 2
        graph.add_edge("fundamentals_agent", "bull_analyst")
        graph.add_edge("fundamentals_agent", "bear_analyst")
        graph.add_edge("fundamentals_agent", "valuation_analyst")
        graph.add_edge("sentiment_agent", "bull_analyst")
        graph.add_edge("sentiment_agent", "bear_analyst")
        graph.add_edge("sentiment_agent", "valuation_analyst")
        graph.add_edge("risk_agent", "bull_analyst")
        graph.add_edge("risk_agent", "bear_analyst")
        graph.add_edge("risk_agent", "valuation_analyst")

        # wave 2 → supervisor
        graph.add_edge("bull_analyst", "supervisor_agent")
        graph.add_edge("bear_analyst", "supervisor_agent")
        graph.add_edge("valuation_analyst", "supervisor_agent")

        # crypto → supervisor
        graph.add_edge("onchain_analyst", "supervisor_agent")

        graph.add_edge("supervisor_agent", END)

        return graph

    def setup_graph(self, checkpointer=None, hitl: bool = True):
        graph = self.build()
        return graph.compile(
            checkpointer=checkpointer,
            interrupt_before=["supervisor_agent"] if hitl else [],
        )