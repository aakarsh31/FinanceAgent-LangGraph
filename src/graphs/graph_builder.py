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
from src.nodes.technical_analyst import TechnicalAnalyst
from src.nodes.supervisor_agent import SupervisorAgent


def route_by_asset_class(state: FinanceState) -> list[str]:
    asset_class = state["asset_class"]
    if asset_class == "equity":
        return ["fundamentals_agent", "sentiment_agent", "risk_agent", "technical_analyst"]
    elif asset_class == "crypto":
        return ["onchain_analyst", "sentiment_agent", "risk_agent"]
    else:
        raise ValueError(
            f"Invalid asset_class '{asset_class}' in state. "
            f"Expected 'equity' or 'crypto'."
        )


class GraphBuilder:

    def __init__(self, engine=None):
        self.llm_client = LLMClient()
        self.fast_llm = self.llm_client.get_llm("fast")
        self.smart_llm = self.llm_client.get_llm("smart")
        self.engine = engine  # SQLAlchemy engine — None falls back to live API

    def build(self):
        graph = StateGraph(FinanceState)

        data_fetch = DataFetchAgent(engine=self.engine)
        macro = MacroRegimeAgent(self.fast_llm)
        fundamentals = FundamentalsAgent(self.fast_llm)
        sentiment = SentimentAgent(self.fast_llm)
        risk = RiskDataAgent(self.fast_llm)
        bull = BullAnalyst(self.fast_llm)
        bear = BearAnalyst(self.fast_llm)
        valuation = ValuationAnalyst(self.fast_llm)
        onchain = OnChainAnalyst(self.fast_llm)
        technical = TechnicalAnalyst(self.fast_llm)
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
        graph.add_node("technical_analyst", technical.analyze)
        graph.add_node("supervisor_agent", supervisor.analyze)

        # HITL gate — dummy node that exists purely to be interrupted after supervisor
        # The actual trade execution happens in app.py /approve, not in the graph
        graph.add_node("trade_gate", lambda state: state)

        graph.add_edge(START, "data_fetch")
        graph.add_edge("data_fetch", "macro_regime_agent")
        graph.add_conditional_edges("macro_regime_agent", route_by_asset_class)

        # equity wave 1 → wave 2
        # bull/bear wait for all wave-1 agents (they use macro, sentiment, risk, fundamentals, technical)
        # valuation only needs fundamentals — runs parallel to sentiment/risk/technical
        graph.add_edge("fundamentals_agent", "bull_analyst")
        graph.add_edge("fundamentals_agent", "bear_analyst")
        graph.add_edge("fundamentals_agent", "valuation_analyst")
        graph.add_edge("sentiment_agent", "bull_analyst")
        graph.add_edge("sentiment_agent", "bear_analyst")
        graph.add_edge("risk_agent", "bull_analyst")
        graph.add_edge("risk_agent", "bear_analyst")
        graph.add_edge("technical_analyst", "bull_analyst")
        graph.add_edge("technical_analyst", "bear_analyst")

        # wave 2 → supervisor
        graph.add_edge("bull_analyst", "supervisor_agent")
        graph.add_edge("bear_analyst", "supervisor_agent")
        graph.add_edge("valuation_analyst", "supervisor_agent")

        # crypto → supervisor
        graph.add_edge("onchain_analyst", "supervisor_agent")

        graph.add_edge("supervisor_agent", "trade_gate")
        graph.add_edge("trade_gate", END)

        return graph

    def setup_graph(self, checkpointer=None, hitl: bool = True):
        graph = self.build()
        return graph.compile(
            checkpointer=checkpointer,
            interrupt_before=["trade_gate"] if hitl else [],
        )