"""
scraper.py — 用 Playwright 登入 X 並搜尋小作文關鍵字
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).parent

# ── 搜尋查詢字串 ──────────────────────────────────────────────
TW_QUERY = (
    "(台股 OR 股票 OR 概念股) "
    "(傳聞 OR 據傳 OR 小道消息 OR 供應鏈爆料 OR 小作文 OR "
    "轉虧為盈 OR 獨家供應 OR 看好上看 OR 法人圈 OR 預估EPS OR "
    "漲價 OR 缺料 OR 急單 OR 驗證通過 OR 擴產 OR 主力線)"
    " min_faves:5"
)

US_QUERY = (
    "(stock OR stocks OR macro) "
    "(rumor OR rumors OR leaked OR \"inside scoop\" OR \"channel check\" OR "
    "\"exclusive supplier\" OR buyout OR \"whisper number\" OR \"supply chain\" OR "
    "\"acquisition\" OR \"massive order\")"
    " min_faves:10"
)

PORTFOLIO_PATH = Path(__file__).parent.parent / "portfolio.json"


def _load_portfolio_tickers() -> list[str]:
    """從 portfolio.json 取出所有股票代號"""
    try:
        data = json.loads(PORTFOLIO_PATH.read_text())
        tickers = list(data.get("tw", {}).keys()) + list(data.get("us", {}).keys())
        return tickers
    except Exception:
        return []


def _build_ticker_queries(tickers: list[str]) -> list[tuple[str, str]]:
    """為持倉中的個股產生專屬搜尋字串，回傳 (label, query) 清單"""
    tw_tickers = []
    us_tickers = []
    try:
        data = json.loads(PORTFOLIO_PATH.read_text())
        tw_tickers = list(data.get("tw", {}).keys())
        us_tickers = list(data.get("us", {}).keys())
    except Exception:
        pass

    queries = []
    if tw_tickers:
        cashtags = " OR ".join(f"${t}" for t in tw_tickers)
        q = (
            f"({cashtags}) "
            "(傳聞 OR 供應鏈 OR 獨家 OR 小作文 OR 產能 OR 漲價 OR 急單 OR 預估EPS)"
            " min_faves:3"
        )
        queries.append(("台股持倉個股", q))

    if us_tickers:
        cashtags = " OR ".join(f"${t}" for t in us_tickers)
        q = (
            f"({cashtags}) "
            "(leaked OR rumor OR \"supply chain\" OR \"exclusive supplier\" OR buyout)"
            " min_faves:5"
        )
        queries.append(("美股持倉個股", q))

    return queries


async def _load_cookies(context):
    """從 x_cookies.json 注入 cookies"""
    cookies_path = BASE_DIR / "x_cookies.json"
    if not cookies_path.exists():
        raise FileNotFoundError(f"找不到 {cookies_path}")
    cookies = json.loads(cookies_path.read_text())
    await context.add_cookies(cookies)
    print("🍪 已注入 cookies")


async def _search_posts(page, query: str, max_posts: int = 20) -> list[dict]:
    """搜尋 X 並回傳貼文列表"""
    import urllib.parse
    encoded = urllib.parse.quote(query)
    url = f"https://x.com/search?q={encoded}&src=typed_query&f=live"

    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)

    posts = []
    seen_ids = set()

    # 滾動幾次抓更多
    for _ in range(3):
        articles = await page.query_selector_all('article[data-testid="tweet"]')
        for article in articles:
            try:
                # 取得推文連結（含 tweet_id）
                link_el = await article.query_selector('a[href*="/status/"]')
                if not link_el:
                    continue
                href = await link_el.get_attribute("href")
                tweet_id = re.search(r"/status/(\d+)", href or "")
                if not tweet_id:
                    continue
                tid = tweet_id.group(1)
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)

                # 取得文字內容
                text_el = await article.query_selector('[data-testid="tweetText"]')
                text = await text_el.inner_text() if text_el else ""

                # 作者
                user_el = await article.query_selector('[data-testid="User-Name"]')
                author = await user_el.inner_text() if user_el else ""
                author = author.split("\n")[0].strip()

                # 時間
                time_el = await article.query_selector("time")
                post_time = await time_el.get_attribute("datetime") if time_el else ""

                posts.append({
                    "id": tid,
                    "author": author,
                    "text": text.strip(),
                    "url": f"https://x.com{href}",
                    "time": post_time,
                })

                if len(posts) >= max_posts:
                    break
            except Exception:
                continue

        if len(posts) >= max_posts:
            break

        await page.evaluate("window.scrollBy(0, 1500)")
        await page.wait_for_timeout(2000)

    return posts


async def fetch_all_posts() -> dict[str, list[dict]]:
    """
    登入 X 後執行所有搜尋，回傳 { label: [posts] }
    """
    ticker_queries = _build_ticker_queries(_load_portfolio_tickers())
    all_queries = [
        ("台股大盤傳聞", TW_QUERY),
        ("美股大盤傳聞", US_QUERY),
    ] + ticker_queries

    results = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        print("🍪 載入 cookies...")
        await _load_cookies(context)
        page = await context.new_page()

        # 進入首頁確認登入狀態
        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        if "login" in page.url or "i/flow" in page.url:
            print("❌ Cookies 已過期，請重新提供")
            await browser.close()
            return {}

        print(f"✅ 登入成功，開始搜尋 {len(all_queries)} 組關鍵字...")

        for label, query in all_queries:
            print(f"  🔍 搜尋：{label}")
            try:
                posts = await _search_posts(page, query, max_posts=15)
                results[label] = posts
                print(f"     找到 {len(posts)} 則")
            except Exception as e:
                print(f"     ⚠️ 搜尋失敗：{e}")
                results[label] = []
            await page.wait_for_timeout(2000)

        await browser.close()

    return results


if __name__ == "__main__":
    posts = asyncio.run(fetch_all_posts())
    for label, items in posts.items():
        print(f"\n=== {label} ({len(items)} 則) ===")
        for p in items[:3]:
            print(f"  [{p['author']}] {p['text'][:80]}...")
