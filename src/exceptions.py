class FinanceAgentError(Exception):
    """Base exception for all FinanceAgent pipeline errors"""

class TickerNotFoundError(FinanceAgentError):
    """Raised when ticker doesn't exist, is invalid, or returns no data from yfinance"""

class EmptyDataError(FinanceAgentError):
    """Raised when yfinance returns empty history or missing key fields"""

class DataFetchRateLimitError(FinanceAgentError):
    """Raised when yfinance is being rate limited after too many requests"""

class LLMStructuredOutputError(FinanceAgentError):
    """Raised when LLM returns output that fails Pydantic schema validation"""

