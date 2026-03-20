from src.states.financestate import FinanceState,FundamentalsData
from langchain_core.messages import HumanMessage

class FundamentalsAgent:

    def __init__(self,llm):
        self.llm = llm
    
    def analyze(self,state:FinanceState):
        info = state["raw_data"]["info"]

        relevant_data = {
            "PE_ratio": info.get("trailingPE"),
            "EPS": info.get("trailingEps"),
            "revenue_growth": info.get("revenueGrowth"),
            "debt_to_equity": info.get("debtToEquity"),
        }

        prompt = f"""
        You are a financial analyst. Analyze these fundamentals for {state["ticker"]}: {relevant_data}

        Return a structured analysis with PE_ratio, EPS, revenue_growth and debt_to_equity.
        If a value is None or missing, return null for that field. 
        Do NOT estimate or invent values — null is more honest than a guess.
        """

        messages = [HumanMessage(content=prompt)]

        result = self.llm.with_structured_output(FundamentalsData).invoke(messages)

        return {"fundamentals":result}