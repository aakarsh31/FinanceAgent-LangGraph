from src.states.financestate import FinanceState,RiskData
from langchain_core.messages import HumanMessage
import pandas as pd

class RiskDataAgent():
    def __init__(self,llm):
        self.llm = llm

    def analyze(self,state:FinanceState):
        history_df = pd.DataFrame(state['raw_data']['history'])

        # daily returns = percentage change day over day
        daily_returns = history_df['Close'].pct_change()

        # annualized volatility = std of daily returns × √252
        # 252 = trading days in a year
        volatility = daily_returns.std() * (252**0.5) * 100

        info = state['raw_data']['info']
        beta = info.get("beta")

        prompt = f"""
        You are a financial Analyst, Analyze the risk_flags for {state['ticker']} using the following metrics:
        Beta : {beta} and \n Annualized Volatility: {volatility:.2f}%.

        Return the metrics along with the Risk Flags u found associated with them
        """

        message = [HumanMessage(content=prompt)]

        response = self.llm.with_structured_output(RiskData).invoke(message)

        return {"risk": response}
