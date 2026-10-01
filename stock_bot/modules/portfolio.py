"""
portfolio.py — 持股分析模組
功能：
  1. 台股新聞：鉅亨網 API → Gemini 閱讀全文摘要重點
  2. 美股新聞：Yahoo Finance RSS → Google Translate 翻譯標題 + 摘要 + 連結
  3. 持倉行程：財報日 / 法說會（yfinance calendar + 季報截止估算）
"""

import yfinance as yf
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, date
from deep_translator import GoogleTranslator
from config import TW_STOCKS, US_STOCKS
from modules.summarizer import fetch_and_summarize

_CNYES_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://www.cnyes.com",
    "Referer": "https://www.cnyes.com/",
}

TW_YAHOO_RSS     = "https://tw.stock.yahoo.com/rss?s={code}.TW"
TW_YAHOO_RSS_OTC = "https://tw.stock.yahoo.com/rss?s={code}.TWO"


# ══════════════════════════════════════════════════════
#  鉅亨網 — 台股新聞 batch
# ══════════════════════════════════════════════════════

def fetch_cnyes_batch(pages: int = 5) -> list[dict]:
    """
    取得鉅亨網台股新聞列表（API 每頁上限 30 篇，分頁抓取）。
    預設抓 5 頁（約 150 篇），全域共用一次即可。
    """
    all_items = []
    for page in range(1, pages + 1):
        try:
            r = requests.get(
                f"https://api.cnyes.com/media/api/v1/newslist/category/tw_stock"
                f"?limit=30&page={page}",
                headers=_CNYES_HEADERS, timeout=10,
            )
            items = r.json().get("items", {}).get("data", [])
            if not items:
                break
            all_items.extend(items)
        except Exception:
            break
    return all_items


def cnyes_news_for_codes(codes: list[str],
                         all_news: list[dict],
                         max_per_code: int = 2) -> dict[str, list]:
    """
    從 batch 新聞中過濾出各股票代號的相關新聞。
    回傳 {code: [news_item, ...]}
    """
    code_set = set(str(c) for c in codes)
    result   = {c: [] for c in codes}
    for item in all_news:
        stocks = item.get("stock") or []
        for s in stocks:
            s = str(s)
            if s in code_set and len(result.get(s, [])) < max_per_code:
                if s in result:
                    result[s].append(item)
    return result


def _format_cnyes_item(item: dict) -> dict:
    """
    鉅亨新聞 dict → 標準 news dict（含 Gemini 摘要重點）
    """
    news_id  = item.get("newsId", 0)
    title    = item.get("title", "")
    fallback = item.get("summary", "")          # 鉅亨內建摘要

    pub_ts = item.get("publishAt", 0)
    try:
        pub_str = datetime.fromtimestamp(pub_ts).strftime("%m/%d %H:%M")
    except Exception:
        pub_str = ""

    link = f"https://news.cnyes.com/news/id/{news_id}" if news_id else ""

    # Gemini 摘要（自動 fallback 到內建摘要）
    key_points = fetch_and_summarize(news_id, title, fallback)

    return {
        "title":     title,
        "summary":   key_points,
        "link":      link,
        "time":      pub_str,
    }


# ── 翻譯工具 ─────────────────────────────────────────

def _translate_to_zh(text: str) -> str:
    if not text:
        return ""
    try:
        translated = GoogleTranslator(source="auto", target="zh-TW").translate(text[:500])
        return translated or text
    except Exception:
        return text


# ══════════════════════════════════════════════════════
#  美股：Yahoo Finance RSS + 翻譯
# ══════════════════════════════════════════════════════

def _fetch_yahoo_rss(symbol: str, max_items: int = 3) -> list[dict]:
    url  = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    news = []
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root    = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            return news

        for item in channel.findall("item")[:max_items]:
            title_en = item.findtext("title", "").strip()
            desc_en  = item.findtext("description", "").strip()
            link     = item.findtext("link", "").strip()
            pub_raw  = item.findtext("pubDate", "")
            try:
                pub_dt  = datetime.strptime(pub_raw, "%a, %d %b %Y %H:%M:%S %z")
                pub_str = pub_dt.strftime("%m/%d %H:%M")
            except Exception:
                pub_str = ""
            if not title_en:
                continue
            news.append({
                "title":   _translate_to_zh(title_en),
                "summary": _translate_to_zh(desc_en[:200]) if desc_en else "",
                "link":    link,
                "time":    pub_str,
            })
    except Exception:
        pass
    return news


# ══════════════════════════════════════════════════════
#  台股：Yahoo Finance 個股 RSS（精準個股新聞）
# ══════════════════════════════════════════════════════

def _fetch_tw_stock_rss(code: str, name: str, max_items: int = 3) -> list[dict]:
    """
    從 Yahoo Finance 台股 RSS 取得個股專屬新聞。
    先試 .TW（上市），無結果再試 .TWO（上櫃）。
    只保留標題含股名或代碼的公司相關文章（過濾大盤通用文）。
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    for suffix in [".TW", ".TWO"]:
        try:
            url  = f"https://tw.stock.yahoo.com/rss?s={code}{suffix}"
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code != 200:
                continue
            root  = ET.fromstring(resp.text)
            items = []
            for it in root.findall(".//item"):
                title   = it.findtext("title", "").strip()
                link    = it.findtext("link", "").strip()
                pub_raw = it.findtext("pubDate", "")
                if not title:
                    continue
                # 過濾與個股無關的大盤通用文
                if name not in title and code not in title:
                    continue
                try:
                    from email.utils import parsedate_to_datetime
                    pub_str = parsedate_to_datetime(pub_raw).strftime("%m/%d %H:%M")
                except Exception:
                    pub_str = ""
                items.append({"title": title, "link": link, "time": pub_str, "summary": ""})
                if len(items) >= max_items:
                    break
            if items:
                return items
        except Exception:
            continue
    return []


# ══════════════════════════════════════════════════════
#  台股：完整資訊（個股 RSS + cnyes 補充）
# ══════════════════════════════════════════════════════

def get_all_tw_portfolio(cnyes_batch: list[dict] | None = None) -> list[dict]:
    """
    取得所有台股持倉資訊。
    主要新聞來源：Yahoo Finance 個股 RSS（公司專屬、精確）。
    cnyes_batch 備用：若 RSS 無結果，從 cnyes batch 補充
    （要求標題含股名，避免大盤通用文混入）。
    """
    from config import TW_STOCKS as _TW

    if cnyes_batch is None:
        cnyes_batch = fetch_cnyes_batch()

    # cnyes batch 按股名過濾（只取標題含股名的文章）
    codes    = list(_TW.keys())
    code_name = dict(_TW)
    news_map = cnyes_news_for_codes(codes, cnyes_batch, max_per_code=2)

    result = []
    for code, name in _TW.items():
        # 主要來源：Yahoo Finance 個股 RSS
        news = _fetch_tw_stock_rss(code, name, max_items=3)

        # 備用：cnyes batch（嚴格過濾，標題須含股名）
        if not news:
            raw = [it for it in news_map.get(code, [])
                   if name in it.get("title", "")]
            news = [_format_cnyes_item(it) for it in raw[:2]]

        result.append({"code": code, "name": name, "news": news})
    return result


# ══════════════════════════════════════════════════════
#  美股：完整資訊
# ══════════════════════════════════════════════════════

def get_us_stock_info(symbol: str, name: str) -> dict:
    return {"symbol": symbol, "name": name, "news": _fetch_yahoo_rss(symbol, max_items=3)}


def get_all_us_portfolio() -> list[dict]:
    return [get_us_stock_info(sym, name) for sym, name in US_STOCKS.items()]


# ══════════════════════════════════════════════════════
#  持倉行程：財報日 / 法說會
# ══════════════════════════════════════════════════════

_TW_DEADLINES = [
    (( 1,  1), ( 3, 31), "Q4財報截止"),
    (( 4,  1), ( 5, 15), "Q1財報截止"),
    (( 5, 16), ( 8, 14), "Q2財報截止"),
    (( 8, 15), (11, 14), "Q3財報截止"),
]

def _tw_deadlines_list(year: int) -> list:
    """回傳指定年份所有季報截止日 list，格式 [(date, label), ...]"""
    dl = []
    for (ms, ds), (me, de), label in _TW_DEADLINES:
        dl.append((date(year, me, de), label))
    dl.append((date(year + 1, 3, 31), "Q4財報截止"))
    return dl


def _next_tw_deadline_after(ref: date) -> tuple:
    """回傳 ref 日期之後（含當日）第一個季報截止日與標籤"""
    for d, label in _tw_deadlines_list(ref.year):
        if d >= ref:
            return d, label
    return _tw_deadlines_list(ref.year + 1)[0]


def _yf_earnings_dates(ticker_str: str) -> list:
    """從 yfinance calendar 取所有財報日（date list），失敗回傳空 list"""
    try:
        cal   = yf.Ticker(ticker_str).calendar
        if not isinstance(cal, dict):
            return []
        raw = cal.get("Earnings Date") or []
        result = []
        for d in raw:
            result.append(d.date() if hasattr(d, "date") else d)
        return result
    except Exception:
        return []


def get_portfolio_schedule(tw_stocks: dict, us_stocks: dict) -> list[dict]:
    today  = date.today()
    events = []

    # ── 台股 ──
    for code, name in tw_stocks.items():
        dates = (_yf_earnings_dates(f"{code}.TW") or
                 _yf_earnings_dates(f"{code}.TWO"))

        # 找最近未來的財報日
        future = [d for d in dates if d >= today]
        # 找最近已過的財報日（90 天內 = 本季剛公告）
        recent_past = [d for d in dates if (today - d).days <= 90 and d < today]

        if future:
            # yfinance 有明確未來財報日 → 直接用
            events.append({"date": future[0], "name": name, "code": code,
                           "label": "財報／法說會", "market": "tw"})
        elif recent_past:
            # 最近已公告過 → 跳過本季截止，顯示下一季
            last_reported = max(recent_past)
            # 下一季截止日 = 剛公告日之後的第一個截止日
            next_d, next_label = _next_tw_deadline_after(last_reported + __import__('datetime').timedelta(days=1))
            events.append({"date": next_d, "name": name, "code": code,
                           "label": f"{next_label}（估）", "market": "tw"})
        else:
            # 無資料 → 以今天為基準找下一個截止日
            d, label = _next_tw_deadline_after(today)
            events.append({"date": d, "name": name, "code": code,
                           "label": f"{label}（估）", "market": "tw"})

    # ── 美股 ──
    for symbol, name in us_stocks.items():
        dates  = _yf_earnings_dates(symbol)
        future = [d for d in dates if d >= today]
        if future:
            events.append({"date": future[0], "name": name, "code": symbol,
                           "label": "財報日", "market": "us"})

    events.sort(key=lambda x: x["date"])
    return events


def format_schedule(events: list[dict]) -> str:
    lines = ["📅 【持倉近期行程】"]
    if not events:
        lines.append("  （近期無已公告事件）")
        return "\n".join(lines)
    for e in events:
        d        = e["date"]
        date_str = f"{d.month}/{d.day}"
        icon     = "💬" if "法說" in e["label"] else ("💰" if e["market"] == "us" else "📋")
        flag     = "🇹🇼" if e["market"] == "tw" else "🇺🇸"
        lines.append(f"  {icon} {date_str}  {flag} {e['name']} ({e['code']})  {e['label']}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════
#  台股注意 / 處置警示
# ══════════════════════════════════════════════════════

def get_tw_flagged_stocks(tw_stocks: dict) -> list[dict]:
    """
    查詢持倉台股中有無被列為注意／處置的股票。
    回傳 list of {code, name, type("attention"|"disposition"), period, detail, reason}
    處置優先排前面，注意排後面。
    """
    headers = {"User-Agent": "Mozilla/5.0", "accept": "application/json"}
    base    = "https://openapi.twse.com.tw/v1"
    today   = date.today()
    codes   = set(tw_stocks.keys())
    result  = {}   # code → dict（處置會蓋掉注意）

    # 1. 當日注意股票
    try:
        r = requests.get(f"{base}/announcement/notice", headers=headers, timeout=8)
        for item in r.json():
            code = item.get("Code", "").strip()
            if code in codes:
                result[code] = {
                    "code":   code,
                    "name":   tw_stocks[code],
                    "type":   "attention",
                    "period": item.get("Date", ""),
                    "detail": item.get("TradingInfoForAttention", ""),
                    "reason": "",
                }
    except Exception:
        pass

    # 2. 近期達注意標準（notetrans）—— 尚未入注意名單但接近
    try:
        r = requests.get(f"{base}/announcement/notetrans", headers=headers, timeout=8)
        for item in r.json():
            code = item.get("Code", "").strip()
            if code in codes and code not in result:
                result[code] = {
                    "code":   code,
                    "name":   tw_stocks[code],
                    "type":   "attention",
                    "period": item.get("RecentlyMetAttentionSecuritiesCriteria", ""),
                    "detail": "",
                    "reason": "",
                }
    except Exception:
        pass

    # 3. 處置股票（過濾有效期，蓋掉注意）
    try:
        r    = requests.get(f"{base}/announcement/punish", headers=headers, timeout=8)
        seen = set()
        for item in r.json():
            code   = item.get("Code", "").strip()
            period = item.get("DispositionPeriod", "")
            if code not in codes or not period:
                continue
            try:
                end_str   = period.split("～")[-1].strip()   # e.g. "115/04/30"
                y, m, dv  = end_str.split("/")
                end_date  = date(int(y) + 1911, int(m), int(dv))
                if end_date >= today and code not in seen:
                    seen.add(code)
                    result[code] = {
                        "code":   code,
                        "name":   tw_stocks[code],
                        "type":   "disposition",
                        "period": period,
                        "detail": item.get("DispositionMeasures", ""),
                        "reason": item.get("ReasonsOfDisposition", ""),
                    }
            except Exception:
                pass
    except Exception:
        pass

    items = list(result.values())
    items.sort(key=lambda x: 0 if x["type"] == "disposition" else 1)
    return items


def format_tw_flags(flagged: list) -> str:
    if not flagged:
        return ""
    lines = ["🚨 【持倉注意 / 處置警示】"]
    for item in flagged:
        if item["type"] == "disposition":
            lines.append(f"  🔴 {item['name']} ({item['code']})  {item['detail']}")
            lines.append(f"     處置期間：{item['period']}")
            if item.get("reason"):
                lines.append(f"     原因：{item['reason']}")
        else:
            lines.append(f"  ⚠️  {item['name']} ({item['code']})  注意股票")
            if item.get("period"):
                lines.append(f"     {item['period']}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════
#  格式化
# ══════════════════════════════════════════════════════

def format_tw_portfolio(tw_data: list) -> str:
    lines = ["📁 【台股持倉動態】（鉅亨網）"]
    for s in tw_data:
        lines.append(f"\n  ◆ {s['name']} ({s['code']})")
        if s["news"]:
            for n in s["news"]:
                time_str = f"[{n['time']}] " if n.get("time") else ""
                lines.append(f"    📰 {time_str}{n['title']}")
                if n.get("summary"):
                    for line in n["summary"].splitlines():
                        if line.strip():
                            lines.append(f"       {line.strip()}")
                if n.get("link"):
                    lines.append(f"       🔗 {n['link']}")
        else:
            lines.append("    📰 暫無相關新聞")
    return "\n".join(lines)


def format_us_portfolio(us_data: list) -> str:
    lines = ["📁 【美股持倉動態】"]
    for s in us_data:
        lines.append(f"\n  ◆ {s['name']} ({s['symbol']})")
        if s["news"]:
            for n in s["news"]:
                time_str = f"[{n['time']}] " if n.get("time") else ""
                lines.append(f"    📰 {time_str}{n['title']}")
                if n.get("summary"):
                    lines.append(f"       📌 {n['summary']}")
                if n.get("link"):
                    lines.append(f"       🔗 {n['link']}")
        else:
            lines.append("    📰 暫無最新新聞")
    return "\n".join(lines)
