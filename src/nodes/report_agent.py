from src.states.financestate import FinanceState,FinancialReport
from langchain_core.messages import HumanMessage
import logging

from src.exceptions import LLMStructuredOutputError

logger = logging.getLogger(__name__)

class ReportAgent():
    def __init__(self,llm):
        self.llm = llm
    

    

    def analyze(self,state:FinanceState):


        ticker = state['ticker']

        consensus = state.get("analyst_consensus") or {}
        recommendation = consensus.get("recommendation") or "unavailable"
        target_price = consensus.get("target_price") or "unavailable"
        num_analysts = consensus.get("num_analysts") or "unavailable"

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

        ANALYST CONSENSUS:
        - Wall Street Recommendation: {recommendation}
        - Average Price Target: {target_price}
        - Number of Analysts: {num_analysts}

        Produce a comprehensive investment report with:
        - summary: narrative analysis combining all the above data
        - recommendations: Buy, Hold, or Sell
        - key_metrics: list the ACTUAL VALUES e.g. ['P/E: 31.2', 'EPS: 6.43', 'Beta: 1.12', 'Volatility: 23.4%','Sentiment: 0.6 (Bullish)']
        - confidence: High, Medium, or Low based on data quality
        Compare your recommendation against Wall Street consensus and populate analyst_agreement.

        """

        message = [HumanMessage(content=prompt)]

        try:
            response = self.llm.with_structured_output(FinancialReport).invoke(message)
        except Exception as e:
            logger.error(f"ReportAgent LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"Report generation failed for {ticker}: {e}")

        return {"report":response}