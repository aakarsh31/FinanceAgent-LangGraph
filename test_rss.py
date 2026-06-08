from ingestion.rss_client import RSSClient

client = RSSClient()
total = client.fetch_all_feeds()
print(f"Total articles fetched: {total}")
print("Sample headlines:")
for a in client.articles[:5]:
    publisher = a["publisher"]
    headline = a["headline"][:70]
    print(f"  [{publisher}] {headline}")