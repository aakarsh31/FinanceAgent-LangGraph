from src.states.financestate import FinanceState
import yfinance as yf

class DataFetchAgent:

    def fetch(self,state:FinanceState):
        #Read inputs from state
        ticker = state["ticker"]
        timeframe = state["timeframe"]


        #yfinance tracker object
        try:
            stock = yf.Ticker(ticker)
            #extract info
            info = stock.info
            history = stock.history(period=timeframe).to_dict()
            news = stock.news
        except Exception as e:
            raise ValueError(f"Failed to fetch data for {ticker}:{e}")
        
        
        

        #Extract headlines as a list of strings
        # with each new item as a dict with key as "Title"
        headlines = [article['content']['title'] for article in news if article.get("content") and article["content"].get("title")]

        #return state fields
        return{
            "raw_data":{
                "info":info,
                "history":history,
            },
            "news_headlines":headlines
        }