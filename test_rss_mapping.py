from ingestion.rss_client import RSSClient

client = RSSClient()
client.fetch_all_feeds()

# Small sample name map
ticker_name_map = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation",
    "GOOGL": "Alphabet Inc.",
    "AMZN": "Amazon.com Inc.",
    "META": "Meta Platforms Inc.",
    "TSLA": "Tesla Inc.",
    "PFE": "Pfizer Inc.",
    "JPM": "JPMorgan Chase",
}

result = client.map_to_tickers(ticker_name_map)

print(f"Tickers with matched articles: {len(result)}")
for ticker, articles in result.items():
    print(f"  {ticker}: {len(articles)} articles")
    for a in articles[:2]:
        print(f"    - {a['headline'][:70]}")
