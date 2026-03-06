import uvicorn
from fastapi import FastAPI, Request
from src.graphs.graph_builder import GraphBuilder
from src.llms.groqllm import GroqLLM

import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()


os.environ['LANGSMITH_API_KEY'] = os.getenv("LANGCHAIN_API_KEY")

@app.post("/analyze")
async def analyze_stock(request:Request):
    data=await request.json()
    ticker = data.get("ticker","")
    timeframe = data.get("timeframe","")
    asset_class = data.get("asset_class","equity")

    groqllm = GroqLLM()
    llm = groqllm.get_llm() #load llama model

    graph_builder = GraphBuilder(llm)
    
    graph = graph_builder.setup_graph()
    state = graph.invoke({"ticker":ticker,"timeframe":timeframe,"asset_class":asset_class})
        
    
    return {
    "ticker": ticker,
    "report": state["report"]
}

if __name__ == "__main__":
    uvicorn.run("app:app",host="0.0.0.0",port=8000,reload=True)