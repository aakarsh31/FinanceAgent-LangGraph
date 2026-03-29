import uvicorn
from fastapi import FastAPI, Request, HTTPException
from src.graphs.graph_builder import GraphBuilder
from src.llms.groqllm import GroqLLM
from src.exceptions import FinanceAgentError

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import os
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s:%(funcName)s:%(lineno)d %(message)s",
    force=True
)

app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/")
async def serve_frontend():
    return FileResponse("frontend/index.html")

VALID_TIMEFRAMES = ["1mo", "3mo", "6mo", "1y", "2y"]
VALID_ASSET_CLASSES = ["equity", "crypto", "macro"]

os.environ['LANGSMITH_API_KEY'] = os.getenv("LANGCHAIN_API_KEY")

@app.post("/analyze")
async def analyze_stock(request:Request):
    data=await request.json()

    ticker = data.get("ticker","")
    if not ticker:
        logger.error("No ticker provided in request")
        raise HTTPException(status_code=422, detail= "Ticker cannot be empty")
    
    timeframe = data.get("timeframe","")
    if timeframe not in VALID_TIMEFRAMES:
        logger.error(f"Invalid timeframe '{timeframe}'")
        raise HTTPException(status_code=422,detail=f'Invalid Timeframe : {timeframe}')
    
    asset_class = data.get("asset_class","equity")
    if asset_class not in VALID_ASSET_CLASSES:
        logger.error(f"Invalid asset class '{asset_class}'")
        raise HTTPException(status_code=422,detail=f"Invalid asset class : {asset_class}")

    groqllm = GroqLLM()
    llm = groqllm.get_llm()

    graph_builder = GraphBuilder(llm)
    graph = graph_builder.setup_graph()

    try:
        state = graph.invoke({
            "ticker": ticker,
            "timeframe": timeframe,
            "asset_class": asset_class
        })
    except FinanceAgentError as e:
        logger.error(f"Pipeline failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal pipeline error")
        
    logger.info(f"Successfully analyzed {ticker}")
    return {
    "ticker": ticker,
    "report": state["report"]
}

if __name__ == "__main__":
    uvicorn.run("app:app",host="0.0.0.0",port=8000,reload=True)