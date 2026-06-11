from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os
import logging

logger = logging.getLogger(__name__)

SMART_MODEL = 'gpt-4.1-mini-2025-04-14'  # Supervisor — GPT-4.1 mini
FAST_MODEL = 'gpt-4.1-nano-2025-04-14'   # All other agents — GPT-4.1 nano

class LLMClient():
    def __init__(self):
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")

        self.fast_llm = ChatOpenAI(
            api_key = api_key,
            model = FAST_MODEL,
            temperature=0
        )

        self.smart_llm = ChatOpenAI(
            api_key = api_key,
            model = SMART_MODEL,
            temperature=0
        )

    def get_llm(self,tier: str) -> ChatOpenAI:
        if tier.lower() == 'smart':
            return self.smart_llm
        elif tier.lower() == "fast":
            return self.fast_llm
        else:
            raise ValueError(f"Invalid tier '{tier}'. Valid tiers: 'fast', 'smart'")
