"""
功率元件漲價情報雷達 — 主流程
執行方式：python -m price_radar.main
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from price_radar import db, extractor, notifier
from price_radar.scrapers import google_news
from price_radar.config import (
    KEYWORDS_TW, KEYWORDS_EN, MIN_CONFIDENCE
)


def collect_articles() -> list[dict]:
    articles = []
    seen_urls = set()

    print("📡 抓取中文新聞...")
    for kw in KEYWORDS_TW:
        items = google_news.fetch(kw)
        print(f"  [{kw}] → {len(items)} 篇")
        for item in items:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                articles.append(item)
        time.sleep(0.3)

    print("📡 抓取英文新聞...")
    for kw in KEYWORDS_EN:
        items = google_news.fetch_en(kw)
        print(f"  [{kw}] → {len(items)} 篇")
        for item in items:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                articles.append(item)
        time.sleep(0.3)

    return articles


def run(dry_run: bool = False):
    print("=" * 50)
    print("⚡ 功率元件漲價雷達啟動")
    print("=" * 50)

    db.init_db()

    articles = collect_articles()
    print(f"\n✅ 共收集 {len(articles)} 篇（去重後）")

    new_signals = []
    skipped = 0

    print("\n🤖 Gemini 分析中...")
    for i, article in enumerate(articles):
        url = article.get("url", "")
        if db.is_processed(url):
            skipped += 1
            continue

        print(f"  [{i+1}/{len(articles)}] {article['title'][:60]}...")
        signal = extractor.extract(article)
        if not dry_run:
            db.mark_processed(url)

        if signal is None:
            continue

        print(f"    → 相關！{signal.get('company')} / {signal.get('product')} / 可信度 {signal.get('confidence'):.0%}")

        if dry_run:
            new_signals.append(signal)
            continue

        is_new = db.save_signal(signal)
        if is_new:
            new_signals.append(signal)

        time.sleep(13)  # 免費版每分鐘 5 次限制

    print(f"\n📊 分析完成：{len(new_signals)} 則新情報，{skipped} 篇已處理跳過")

    if not new_signals:
        print("今日無新漲價情報。")
        return

    # 推播
    high_conf = [s for s in new_signals if s.get("confidence", 0) >= MIN_CONFIDENCE]
    print(f"📬 推播 {len(high_conf)} 則（可信度 ≥ {MIN_CONFIDENCE:.0%}）")

    if high_conf:
        if len(high_conf) == 1:
            msg = notifier.format_signal(high_conf[0])
        else:
            msg = notifier.format_summary(high_conf)

        if not dry_run:
            notifier.send(msg)
        else:
            print("\n[dry_run] 預覽推播訊息：")
            print(msg)


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    run(dry_run=dry)
