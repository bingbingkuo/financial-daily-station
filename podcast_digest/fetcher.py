import feedparser
import json
import re
import urllib.request
import tempfile
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path


def parse_pub_date(entry) -> datetime | None:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    return None


def get_audio_url(entry) -> str:
    """從 RSS entry 取得音頻檔案 URL"""
    # 優先從 enclosures 取
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            if "audio" in enc.get("type", "") or enc.get("url", "").endswith((".mp3", ".m4a", ".ogg")):
                return enc.url
    # fallback: 從 links 找
    if hasattr(entry, "links"):
        for link in entry.links:
            if "audio" in link.get("type", "") or link.get("href", "").endswith((".mp3", ".m4a")):
                return link.href
    return ""


def download_audio(url: str, max_mb: int = 150) -> str | None:
    """下載音頻到暫存檔，回傳路徑；超過大小限制則回傳 None"""
    if not url:
        return None
    suffix = ".mp3" if ".mp3" in url else ".m4a"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            # 先檢查 Content-Length
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > max_mb * 1024 * 1024:
                print(f"    ⚠ 音頻檔案過大 ({int(content_length)//1024//1024}MB > {max_mb}MB)，跳過下載")
                return None
            # 分塊寫入，最多下載 max_mb MB
            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > max_mb * 1024 * 1024:
                    print(f"    ⚠ 下載超過 {max_mb}MB，截斷（Gemini 仍可處理部分音頻）")
                    tmp.write(chunk)
                    break
                tmp.write(chunk)
        tmp.close()
        size_mb = os.path.getsize(tmp.name) / 1024 / 1024
        print(f"    ✓ 下載完成 ({size_mb:.1f}MB): {tmp.name}")
        return tmp.name
    except Exception as e:
        print(f"    ✗ 下載失敗: {e}")
        tmp.close()
        os.unlink(tmp.name)
        return None


def parse_entry(entry, podcast_name: str) -> dict:
    """把單一 RSS entry 轉成 episode dict"""
    pub_date = parse_pub_date(entry)
    duration = getattr(entry, "itunes_duration", "")
    description = ""
    if hasattr(entry, "summary"):
        description = re.sub(r"<[^>]+>", "", entry.summary).strip()
    elif hasattr(entry, "description"):
        description = re.sub(r"<[^>]+>", "", entry.description).strip()
    return {
        "podcast": podcast_name,
        "title": entry.title,
        "description": description[:500],
        "pub_date": pub_date.isoformat() if pub_date else "",
        "duration": duration,
        "link": entry.link if hasattr(entry, "link") else "",
        "audio_url": get_audio_url(entry),
    }


def fetch_recent_episodes(rss_url: str, podcast_name: str,
                          days: int = 0, count: int = 0) -> list[dict]:
    """
    抓 episodes：
    - count > 0：取最新 N 集（不管日期）
    - days > 0：取最近 N 天
    - 兩者都沒設：只取最新 1 集
    """
    feed = feedparser.parse(rss_url)
    entries = feed.entries

    if count > 0:
        entries = entries[:count]
    elif days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        entries = [e for e in entries
                   if (parse_pub_date(e) or datetime.now(timezone.utc)) >= cutoff]
    else:
        entries = entries[:1]

    return [parse_entry(e, podcast_name) for e in entries]


def fetch_all_podcasts(config_path: str = "config.json") -> list[dict]:
    """從 config.json 抓所有 podcast"""
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    count = config.get("fetch_episodes", 0)
    days  = config.get("fetch_days", 0)
    all_episodes = []

    for pod in config["podcasts"]:
        print(f"  抓取: {pod['name']} ...")
        try:
            episodes = fetch_recent_episodes(pod["rss"], pod["name"],
                                             days=days, count=count)
            all_episodes.extend(episodes)
            print(f"    → 找到 {len(episodes)} 集")
        except Exception as e:
            print(f"    ✗ 失敗: {e}")

    return all_episodes


if __name__ == "__main__":
    episodes = fetch_all_podcasts()
    for ep in episodes:
        print(f"\n[{ep['podcast']}] {ep['title']}")
        print(f"  發布: {ep['pub_date']}")
        print(f"  描述: {ep['description'][:100]}...")
