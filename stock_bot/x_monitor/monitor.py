"""
monitor.py — X 小作文監控主程式
  - 每次執行抓一輪 X 貼文
  - 過濾已處理過的（dedup）
  - 用 verifier 評分，score >= 7 才推播
  - 推播到 Telegram
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# 讓 import 找得到上層 modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from x_monitor.scraper import fetch_all_posts
from x_monitor.verifier import verify_post
from modules.notifier import send_telegram

SEEN_PATH = Path(__file__).parent / "seen_posts.json"
SCORE_THRESHOLD = 5  # 只推播 score >= 5 的貼文


def _load_seen() -> set:
    if SEEN_PATH.exists():
        return set(json.loads(SEEN_PATH.read_text()))
    return set()


def _save_seen(seen: set):
    SEEN_PATH.write_text(json.dumps(list(seen), ensure_ascii=False, indent=2))


def _format_telegram(post: dict, label: str) -> str:
    level_emoji = {"高": "🔴", "中": "🟡", "低": "⚪"}.get(post.get("level", ""), "❓")
    stocks = "、".join(post.get("stocks", [])) or "不明"

    return (
        f"📡 <b>X 股市傳聞偵測</b>｜{label}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{level_emoji} <b>可信度：{post.get('level')} ({post.get('score')}/10)</b>\n"
        f"📌 涉及股票：{stocks}\n\n"
        f"💬 <b>原文（{post.get('author', '')}）：</b>\n"
        f"{post.get('text', '')[:300]}\n\n"
        f"🔍 <b>評估理由：</b>\n{post.get('reason', '')}\n\n"
        f"🔗 {post.get('url', '')}\n"
        f"⏰ {post.get('time', '')[:16]}"
    )


async def run():
    print(f"\n{'='*50}")
    print(f"🚀 X 小作文監控啟動 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    seen = _load_seen()

    # 抓取所有貼文
    all_posts = await fetch_all_posts()

    if not all_posts:
        print("⚠️ 未抓到任何貼文（可能登入失敗）")
        return

    new_count = 0
    notified_count = 0

    for label, posts in all_posts.items():
        for post in posts:
            tid = post.get("id", "")
            if not tid or tid in seen:
                continue

            seen.add(tid)
            new_count += 1

            if not post.get("text", "").strip():
                continue

            import time
            print(f"\n🔎 驗證：{post['text'][:60]}...")
            time.sleep(13)  # Gemini 免費層每分鐘 5 次
            result = verify_post(post)
            score = result.get("score", 0)
            level = result.get("level", "")

            print(f"   → 可信度：{level} ({score}/10) — {result.get('reason', '')[:50]}")

            if score >= SCORE_THRESHOLD:
                msg = _format_telegram(result, label)
                send_telegram(msg)
                notified_count += 1
                print(f"   ✅ 已推播")

    _save_seen(seen)

    print(f"\n{'='*50}")
    print(f"✅ 完成｜新貼文：{new_count} 則｜推播：{notified_count} 則")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    asyncio.run(run())
