"""
股票追蹤器：podcast 提到的股票自動加入 watchlist
"""
import json
import requests
from datetime import date
from pathlib import Path

# 台股中文名稱快取（process 層級，避免重複打 API）
_TW_NAME_CACHE: dict = {}

def _load_tw_names() -> dict:
    """從 TWSE + 櫃買中心 API 取得台股代號→中文名稱對照表"""
    global _TW_NAME_CACHE
    if _TW_NAME_CACHE:
        return _TW_NAME_CACHE
    mapping = {}
    sources = [
        "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
    ]
    for url in sources:
        try:
            data = requests.get(url, timeout=10).json()
            for item in data:
                code = item.get("Code") or item.get("SecuritiesCompanyCode") or ""
                name = item.get("Name") or item.get("CompanyName") or ""
                if code and name:
                    mapping[code] = name
        except Exception:
            pass
    _TW_NAME_CACHE = mapping
    return mapping


def _fetch_name(symbol: str, market: str) -> str:
    if market == "TW":
        return _load_tw_names().get(symbol, "")
    return ""

def _normalize_date(raw: str) -> str:
    """把各種格式的日期統一轉成 YYYY-MM-DD"""
    if not raw:
        return date.today().isoformat()
    # 已是 ISO 格式
    if len(raw) >= 10 and raw[4] == '-':
        return raw[:10]
    # RFC 2822：e.g. "Wed, 26 Aug 2026 07:19:53 GMT"
    from email.utils import parsedate
    parsed = parsedate(raw)
    if parsed:
        from datetime import date as _date
        try:
            return _date(parsed[0], parsed[1], parsed[2]).isoformat()
        except Exception:
            pass
    return raw[:10]


WATCHLIST_PATH = Path(__file__).parent / "data" / "watchlist.json"
INDUSTRIES_PATH = Path(__file__).parent / "data" / "industries.json"


def load_watchlist() -> dict:
    if WATCHLIST_PATH.exists():
        with open(WATCHLIST_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_watchlist(data: dict):
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update_watchlist(episodes: list[dict]):
    """從摘要結果更新 watchlist，新股票記錄首次提及資訊"""
    watchlist = load_watchlist()
    today = date.today().isoformat()
    new_count = 0

    for ep in episodes:
        ep_date = _normalize_date(ep.get("pub_date", today))
        ep_title = ep.get("title", "")
        ep_podcast = ep.get("podcast", "")

        stock_views = ep.get("stock_views", {})

        def make_mention(symbol):
            sv = stock_views.get(symbol, {})
            return {
                "date": ep_date,
                "episode": ep_title,
                "podcast": ep_podcast,
                "sentiment": ep.get("sentiment", "neutral"),
                "view": sv.get("view", ""),
                "rating": sv.get("rating", ""),
            }

        for symbol in ep.get("tw_stocks", []):
            key = f"TW:{symbol}"
            if key not in watchlist:
                name = _fetch_name(symbol, "TW")
                watchlist[key] = {
                    "symbol": symbol,
                    "market": "TW",
                    "ticker": f"{symbol}.TW",
                    "name": name,
                    "first_date": ep_date,
                    "first_episode": ep_title,
                    "first_podcast": ep_podcast,
                    "mentions": [],
                }
                new_count += 1
                print(f"    ＋ 新追蹤台股: {symbol} {name}（首次提及於 {ep_date}）")
            # 避免同一天重複記錄
            if not any(m["date"] == ep_date and m["episode"] == ep_title for m in watchlist[key]["mentions"]):
                watchlist[key]["mentions"].append(make_mention(symbol))

        for symbol in ep.get("us_stocks", []):
            key = f"US:{symbol}"
            if key not in watchlist:
                watchlist[key] = {
                    "symbol": symbol,
                    "market": "US",
                    "ticker": symbol,
                    "first_date": ep_date,
                    "first_episode": ep_title,
                    "first_podcast": ep_podcast,
                    "mentions": [],
                }
                new_count += 1
                print(f"    ＋ 新追蹤美股: {symbol}（首次提及於 {ep_date}）")
            if not any(m["date"] == ep_date and m["episode"] == ep_title for m in watchlist[key]["mentions"]):
                watchlist[key]["mentions"].append(make_mention(symbol))

    save_watchlist(watchlist)
    print(f"    watchlist 更新完成（新增 {new_count} 檔，共 {len(watchlist)} 檔）")
    update_industries(episodes)
    return watchlist


def load_industries() -> dict:
    if INDUSTRIES_PATH.exists():
        with open(INDUSTRIES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_industries(data: dict):
    with open(INDUSTRIES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update_industries(episodes: list[dict]):
    """從摘要結果更新產業觀點資料庫"""
    industries = load_industries()
    today = date.today().isoformat()
    new_count = 0

    for ep in episodes:
        ep_date = _normalize_date(ep.get("pub_date", today))
        ep_title = ep.get("title", "")
        ep_podcast = ep.get("podcast", "")
        industry_views = ep.get("industry_views", {})

        for ind_name, iv in industry_views.items():
            if ind_name not in industries:
                industries[ind_name] = {
                    "name": ind_name,
                    "first_date": ep_date,
                    "mentions": [],
                }
                new_count += 1

            mention = {
                "date": ep_date,
                "episode": ep_title,
                "podcast": ep_podcast,
                "view": iv.get("view", ""),
                "outlook": iv.get("outlook", "neutral"),
                "catalysts": iv.get("catalysts", []),
                "risks": iv.get("risks", []),
                "related_stocks": iv.get("related_stocks", []),
            }
            if not any(m["date"] == ep_date and m["episode"] == ep_title for m in industries[ind_name]["mentions"]):
                industries[ind_name]["mentions"].append(mention)

    save_industries(industries)
    print(f"    industries 更新完成（新增 {new_count} 個產業，共 {len(industries)} 個）")
