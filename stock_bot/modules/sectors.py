"""
sectors.py — 族群漲跌分析模組
美股：SPDR 族群 ETF 找前三大，並列出各族群當日前三支領頭股票
台股：TWSE 類股指數或代表股加權，並列出各族群當日前三支領頭股票
"""

import yfinance as yf
import requests
import pandas as pd
from config import (
    US_SECTOR_ETFS, US_SECTOR_THEMES, US_SECTOR_LEADERS,
    TW_SECTOR_STOCKS, TW_STOCK_NAMES,
)


# ══════════════════════════════════════════════════════════
#  通用：計算個股當日漲跌幅
# ══════════════════════════════════════════════════════════

def _calc_pct(ticker: str) -> float | None:
    """回傳單一股票當日漲跌幅（%），失敗回傳 None"""
    try:
        hist   = yf.Ticker(ticker).history(period="2d")
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None
        return float((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2] * 100)
    except Exception:
        return None


def _batch_pct(tickers: list[str]) -> dict[str, float]:
    """批次下載多支股票漲跌幅，回傳 {ticker: pct}"""
    result = {}
    try:
        data   = yf.download(tickers, period="2d", auto_adjust=True, progress=False)
        closes = data["Close"]
        for t in tickers:
            col = closes[t].dropna() if t in closes.columns else pd.Series(dtype=float)
            if len(col) >= 2:
                result[t] = float((col.iloc[-1] - col.iloc[-2]) / col.iloc[-2] * 100)
    except Exception:
        for t in tickers:
            pct = _calc_pct(t)
            if pct is not None:
                result[t] = pct
    return result


def _top_stocks(candidates: list[tuple], suffix: str = "") -> list[dict]:
    """
    candidates: [(ticker, name), ...]
    回傳前 3 支漲最多的股票 [{"ticker":..,"name":..,"pct":..}]
    """
    tickers = [f"{t}{suffix}" for t, _ in candidates]
    pct_map = _batch_pct(tickers)
    ranked  = []
    for (ticker, name), full in zip(candidates, tickers):
        pct = pct_map.get(full)
        if pct is not None:
            ranked.append({"ticker": ticker, "name": name, "pct": round(pct, 2)})
    ranked.sort(key=lambda x: x["pct"], reverse=True)
    return ranked[:3]


# ══════════════════════════════════════════════════════════
#  美股族群
# ══════════════════════════════════════════════════════════

def get_us_top_sectors(top_n: int = 3) -> list[dict]:
    """前三大美股族群 + 各族群前三支領頭股"""
    etfs   = list(US_SECTOR_ETFS.keys())
    pct_map = _batch_pct(etfs)

    sectors = []
    for etf, name in US_SECTOR_ETFS.items():
        pct = pct_map.get(etf)
        if pct is not None:
            sectors.append({
                "etf":   etf,
                "name":  name,
                "pct":   round(pct, 2),
                "theme": US_SECTOR_THEMES.get(etf, ""),
            })
    sectors.sort(key=lambda x: x["pct"], reverse=True)
    top = sectors[:top_n]

    # 補充各族群領頭股
    for s in top:
        leaders = US_SECTOR_LEADERS.get(s["etf"], [])
        s["top_stocks"] = _top_stocks(leaders) if leaders else []

    return top


# ══════════════════════════════════════════════════════════
#  台股族群
# ══════════════════════════════════════════════════════════

def get_tw_top_sectors(top_n: int = 3) -> list[dict]:
    """前三大台股族群 + 各族群前三支領頭股"""
    # 先嘗試 TWSE 官方類股指數
    try:
        return _tw_sectors_from_twse(top_n)
    except Exception:
        pass
    # 備援：用代表股加權平均估算
    return _tw_sectors_from_stocks(top_n)


def _tw_sectors_from_twse(top_n: int) -> list[dict]:
    url  = "https://www.twse.com.tw/rwd/zh/fund/MI_INDEX?response=json&type=IND"
    resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    j = resp.json()

    rows_raw = []
    for table in j.get("tables", []):
        fields = table.get("fields", [])
        rows   = table.get("data", [])
        if not rows:
            continue
        try:
            name_idx = fields.index("指數名稱")
            chg_idx  = next(i for i, f in enumerate(fields) if "漲跌" in f and "%" in f)
        except (ValueError, StopIteration):
            continue
        for row in rows:
            try:
                name = row[name_idx].replace("\u3000", "").strip()
                pct  = float(row[chg_idx].replace("%","").replace(",","").strip())
                rows_raw.append({"name": name, "pct": pct})
            except Exception:
                continue

    if not rows_raw:
        raise ValueError("No data")

    rows_raw.sort(key=lambda x: x["pct"], reverse=True)
    top = rows_raw[:top_n]

    # TWSE 類股指數不對應到 TW_SECTOR_STOCKS，無法補領頭股，留空
    for s in top:
        s["top_stocks"] = []
    return top


def _tw_pct(code: str, pct_map: dict) -> float | None:
    """先試上市（.TW），無資料再試上櫃（.TWO）"""
    return pct_map.get(f"{code}.TW") if pct_map.get(f"{code}.TW") is not None \
        else pct_map.get(f"{code}.TWO")


def _tw_sectors_from_stocks(top_n: int) -> list[dict]:
    """用代表股漲跌加權估算各族群，並取各族群前三領頭股"""
    # 收集所有需要的台股代號，同時下載 .TW（上市）與 .TWO（上櫃）
    all_codes = set()
    for codes in TW_SECTOR_STOCKS.values():
        all_codes.update(codes)

    tickers = [f"{c}.TW" for c in all_codes] + [f"{c}.TWO" for c in all_codes]
    pct_map = _batch_pct(tickers)

    sectors = []
    for sector, codes in TW_SECTOR_STOCKS.items():
        pcts = []
        stock_perfs = []
        for code in codes:
            pct = _tw_pct(code, pct_map)
            if pct is not None:
                pcts.append(pct)
                name = TW_STOCK_NAMES.get(code, code)
                stock_perfs.append({"ticker": code, "name": name, "pct": round(pct, 2)})

        if not pcts:
            continue

        avg = round(sum(pcts) / len(pcts), 2)
        stock_perfs.sort(key=lambda x: x["pct"], reverse=True)

        sectors.append({
            "name":       sector,
            "pct":        avg,
            "top_stocks": stock_perfs[:3],
        })

    sectors.sort(key=lambda x: x["pct"], reverse=True)
    return sectors[:top_n]


# ══════════════════════════════════════════════════════════
#  格式化
# ══════════════════════════════════════════════════════════

def _pct_str(pct: float) -> str:
    return f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"


def format_us_sectors(sectors: list) -> str:
    lines = ["🏆 【美股前三大強勢族群】"]
    for i, s in enumerate(sectors, 1):
        lines.append(f"\n  {i}. {s['name']} ({s['etf']})  {_pct_str(s['pct'])}")
        if s.get("theme"):
            lines.append(f"     📌 題材：{s['theme']}")
        if s.get("top_stocks"):
            lines.append("     🔝 領頭股：")
            for st in s["top_stocks"]:
                lines.append(f"        • {st['name']} ({st['ticker']})  {_pct_str(st['pct'])}")
    return "\n".join(lines)


def enrich_tw_sectors_news(sectors: list, cnyes_batch: list) -> list:
    """
    將鉅亨新聞注入到已計算好的台股族群資料中。
    每個族群取領頭股的相關新聞，最多 1 篇（避免太長）。
    """
    from modules.portfolio import cnyes_news_for_codes, _format_cnyes_item

    # 收集所有族群中的股票代號
    all_codes = []
    for s in sectors:
        all_codes += [st["ticker"] for st in s.get("top_stocks", [])]

    news_map = cnyes_news_for_codes(all_codes, cnyes_batch, max_per_code=1)

    for s in sectors:
        sector_news = []
        for st in s.get("top_stocks", []):
            items = news_map.get(st["ticker"], [])
            if items:
                sector_news.append(_format_cnyes_item(items[0]))
                break  # 每族群只取第一篇
        s["news"] = sector_news
    return sectors


def format_tw_sectors(sectors: list) -> str:
    lines = ["🏆 【台股前三大強勢族群】"]
    for i, s in enumerate(sectors, 1):
        lines.append(f"\n  {i}. {s['name']}  {_pct_str(s['pct'])}")
        if s.get("top_stocks"):
            lines.append("     🔝 領頭股：")
            for st in s["top_stocks"]:
                lines.append(f"        • {st['name']} ({st['ticker']})  {_pct_str(st['pct'])}")
        # 族群相關新聞（來自鉅亨）
        for n in s.get("news", []):
            time_str = f"[{n['time']}] " if n.get("time") else ""
            lines.append(f"     📰 {time_str}{n['title']}")
            if n.get("summary"):
                for line in n["summary"].splitlines():
                    if line.strip():
                        lines.append(f"        {line.strip()}")
            if n.get("link"):
                lines.append(f"        🔗 {n['link']}")
    return "\n".join(lines)
