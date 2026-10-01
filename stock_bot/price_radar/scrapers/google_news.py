"""
Google News RSS 爬取模組
"""
import feedparser
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from price_radar.config import MAX_ARTICLES_PER_KEYWORD, ARTICLE_LOOKBACK_DAYS


def _parse_pub_date(entry) -> datetime | None:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return None


def fetch(keyword: str) -> list[dict]:
    encoded = quote(keyword)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    feed = feedparser.parse(url)

    cutoff = datetime.now(timezone.utc) - timedelta(days=ARTICLE_LOOKBACK_DAYS)
    results = []

    for entry in feed.entries[:MAX_ARTICLES_PER_KEYWORD]:
        pub = _parse_pub_date(entry)
        if pub and pub < cutoff:
            continue
        results.append({
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "published": pub.isoformat() if pub else None,
            "source": entry.get("source", {}).get("title", "Google News"),
            "summary": entry.get("summary", ""),
        })

    return results


def fetch_en(keyword: str) -> list[dict]:
    encoded = quote(keyword)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(url)

    cutoff = datetime.now(timezone.utc) - timedelta(days=ARTICLE_LOOKBACK_DAYS)
    results = []

    for entry in feed.entries[:MAX_ARTICLES_PER_KEYWORD]:
        pub = _parse_pub_date(entry)
        if pub and pub < cutoff:
            continue
        results.append({
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "published": pub.isoformat() if pub else None,
            "source": entry.get("source", {}).get("title", "Google News"),
            "summary": entry.get("summary", ""),
        })

    return results
