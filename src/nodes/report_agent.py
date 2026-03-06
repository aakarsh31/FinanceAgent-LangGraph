from src.states.financestate import FinanceState,FinancialReport
from langchain_core.messages import HumanMessage

class ReportAgent():
    def __init__(self,llm):
        self.llm = llm

    def analyze(self,state:FinanceState):
        
        prompt = f"""
        You are a financial analyst producing a final investment report.

        Ticker: {state['ticker']}

        FUNDAMENTALS:
        - P/E Ratio: {state['fundamentals'].PE_ratio}
        - EPS: {state['fundamentals'].EPS}
        - Revenue Growth: {state['fundamentals'].revenue_growth}%
        - Debt to Equity: {state['fundamentals'].debt_to_equity}

        SENTIMENT:
        - Score: {state['sentiment'].sentiment_score}
        - Label: {state['sentiment'].sentiment_label}
        - Reasoning: {state['sentiment'].sentiment_reasoning}

        RISK:
        - Volatility: {state['risk'].volatility:.2f}%
        - Beta: {state['risk'].beta}
        - Risk Flags: {state['risk'].risk_flag}

        Produce a comprehensive investment report with a summary, 
        recommendation (Buy/Hold/Sell), key metrics to watch, 
        and your confidence level.


        """

        message = [HumanMessage(content=prompt)]

        response = self.llm.with_structured_output(FinancialReport).invoke(message)

        return {"report":response}