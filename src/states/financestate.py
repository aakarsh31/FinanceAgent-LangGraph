from typing import TypedDict,Optional
from pydantic import BaseModel,Field

# Pydantic models — one per agent output
class FundamentalsData(BaseModel): 
    PE_ratio: Optional[float] = Field(default=None, description="Price to Earnings ratio — stock price divided by earnings per share")

    revenue_growth: Optional[float] = Field(default=None, description="Year-over-year revenue growth as a percentage e.g. 12.5 means 12.5%")

    EPS: Optional[float] = Field(default=None, description="EPS of the stock")

    debt_to_equity: Optional[float] = Field(default=None, description="debt to equity of the stock")

    
class SentimentData(BaseModel):
    # A score (float), a label (str), and reasoning (str)
    sentiment_score:float=Field(description="Sentiment score from -1.0 (very bearish) to 1.0 (very bullish)")

    sentiment_label:str=Field(description="Classification of the sentiment as bearish/bullish/neutral")

    sentiment_reasoning:str=Field(description="Reasoning for the sentiment labeling")


class RiskData(BaseModel):
    volatility: Optional[float] = Field(default=None, description="30-day annualized volatility as a percentage")

    beta: Optional[float] = Field(default=None, description="Beta relative to S&P 500 — 1.0 means moves with market, >1.0 more volatile")

    risk_flag: list[str] = Field(description="Risk flags e.g. ['High volatility', 'Negative EPS', 'High debt load']")

class FinancialReport(BaseModel):
    # The final output — summary, recommendation, key metrics
    summary:str=Field(description="Final summary of the stock using all the metrics")

    recommendations:str=Field(description="Final recommendation on whether to invest in the stock or not")

    key_metrics:list[str]=Field(description="Any highlight metrics of the stock to keep track of")

    confidence: str = Field(description="Confidence level of the recommendation: High / Medium / Low")



# The state — the baton that travels through all agents
class FinanceState(TypedDict):
    #Provided by user
    ticker: str
    asset_class: str
    timeframe: str  # yfinance format: "1mo", "3mo", "6mo", "1y", "2y"
    #Produced by pipeline
    raw_data: dict
    news_headlines: list[str]
    fundamentals: Optional[FundamentalsData]
    sentiment: Optional[SentimentData]
    risk: Optional[RiskData]
    report: Optional[FinancialReport]