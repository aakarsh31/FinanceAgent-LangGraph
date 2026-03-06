from src.states.financestate import FinanceState,SentimentData
from langchain_core.messages import HumanMessage

class SentimentAgent():

    def __init__(self,llm):
        self.llm = llm

    def analyze(self,state:FinanceState):

        headlines_text = "\n- ".join(state['news_headlines'])

        prompt = f"""
        You are a financial analyst. Analyze the sentiment of these news headlines from {state['ticker']}:
        {headlines_text}

        Return a sentiment score from -1.0 to 1.0, a label (bullish/bearish/neutral), and also your reasoning for the former.
        
        """

        message = [HumanMessage(content=prompt)]
        result = self.llm.with_structured_output(SentimentData).invoke(message)

        return {"sentiment":result}


