"""
us_scout.py — 美股主題基本面選股雷達
四個板塊：
  1. 大盤概況    — 費半/納指/道瓊/VIX/10Y 殖利率 + YTD
  2. 熱門主題    — Gemini 從財經新聞識別 3~5 個結構性催化劑主題
  3. 主題選股    — 基本面加速篩選（營收/EPS/毛利率）→ Gemini 長期評級
  4. 動能追蹤    — 3 個交易日後績效回顧
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

try:
    from google import genai as _genai
    _HAS_GENAI = True
except ImportError:
    _HAS_GENAI = False

# yfinance SQLite 隔離（避免多執行緒 disk I/O error）
try:
    if hasattr(yf, "set_tz_cache_location"):
        _existing = os.environ.get("YF_CACHE_DIR")
        if not _existing:
            _tmp = tempfile.mkdtemp(prefix="yf_cache_")
            os.environ["YF_CACHE_DIR"] = _tmp
            yf.set_tz_cache_location(_tmp)
except Exception:
    pass

_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "us_scout_history.json"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MAX_STOCKS = 30

# ── 成長補充池（不在S&P500/NDX100但值得每日追蹤）─────────────────────
_GROWTH_SUPPLEMENT = [
    # AI 半導體
    "NVDA", "SMCI", "MRVL", "ARM", "MU", "AMAT", "LRCX", "KLAC", "ENTG",
    # AI 軟體 / 平台
    "PLTR", "APP", "DDOG", "NET", "CRWD", "SNOW", "MDB", "ZS", "PANW",
    # 太空 / 國防科技
    "RKLB", "ASTS", "AXON", "IONQ", "RGTI",
    # 消費成長
    "CAVA", "ONON", "DUOL", "CELH", "HIMS", "HOOD",
    # 加密 / 金融科技
    "COIN", "MSTR",
    # 電力 / 能源轉型
    "VST", "CEG", "NRG", "GEV",
    # 生技 / 醫療
    "LLY", "ISRG", "DXCM", "REGN", "VRTX", "MRNA",
    # 半導體龍頭
    "TSM", "ASML", "QCOM", "AVGO",
    # 大型科技（永遠相關）
    "META", "GOOGL", "MSFT", "AMZN", "AAPL", "TSLA",
    # 其他成長
    "SOUN", "UBER", "SPOT", "SHOP",
]


# ═══════════════════════════════════════════════════════════
#  Gemini helper
# ═══════════════════════════════════════════════════════════

_gemini_client = None


def _get_gemini():
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    if not _HAS_GENAI:
        return None
    try:
        from config import GEMINI_API_KEY
        if not GEMINI_API_KEY:
            return None
        _gemini_client = _genai.Client(api_key=GEMINI_API_KEY)
        return _gemini_client
    except Exception:
        return None


def _gemini_call(prompt: str, retries: int = 3, delay: int = 15) -> str:
    client = _get_gemini()
    if not client:
        return ""

    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

    try:
        from google.genai import types as _gtypes
        _thinking_off = _gtypes.GenerateContentConfig(
            thinking_config=_gtypes.ThinkingConfig(thinking_budget=0)
        )
    except Exception:
        _thinking_off = None

    for model in models:
        for attempt in range(retries):
            try:
                kwargs: dict = {"model": model, "contents": prompt}
                if _thinking_off and "2.5" in model:
                    kwargs["config"] = _thinking_off
                resp = client.models.generate_content(**kwargs)
                return resp.text or ""
            except Exception as e:
                err = str(e).lower()
                err_raw = str(e)
                is_unavail = "503" in err or "unavailable" in err
                is_quota = "429" in err or "quota" in err or "exhausted" in err
                if is_unavail:
                    wait = delay * (attempt + 1)
                    if attempt < retries - 1:
                        print(f"    Gemini ({model}) 暫時不可用，{wait}s 後重試…")
                        time.sleep(wait)
                    else:
                        break
                elif is_quota:
                    m2 = re.search(r'"retryDelay":\s*"(\d+)s"', err_raw)
                    wait_s = int(m2.group(1)) if m2 else 0
                    if wait_s and wait_s <= 90 and attempt < retries - 1:
                        time.sleep(wait_s + 2)
                    else:
                        break
                else:
                    print(f"    Gemini ({model}) 錯誤：{err_raw[:80]}")
                    break
    return ""


# ═══════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════

def _last_us_trading_date(n_days_back: int) -> str:
    d = datetime.now().date()
    count = 0
    while count < n_days_back:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d.strftime("%Y%m%d")


# ═══════════════════════════════════════════════════════════
#  Section 1 — US Market Overview
# ═══════════════════════════════════════════════════════════

def _fetch_us_market_overview() -> dict:
    result: dict = {
        "sox": {"close": None, "chg_pct": None, "ytd": None},
        "ndx": {"close": None, "chg_pct": None, "ytd": None},
        "dji": {"close": None, "chg_pct": None, "ytd": None},
        "vix": {"close": None, "chg_pct": None},
        "tnx": {"close": None},
        "tyx": {"close": None},
        "spy_ytd": None,
    }
    sym_key_map = {
        "^SOX": "sox", "^IXIC": "ndx", "^DJI": "dji",
        "^VIX": "vix", "^TNX": "tnx", "^TYX": "tyx",
    }
    all_syms = list(sym_key_map.keys()) + ["SPY"]
    try:
        daily = yf.download(all_syms, period="5d", auto_adjust=True, progress=False)
        close_d = daily["Close"].dropna(how="all")
        for sym, key in sym_key_map.items():
            if sym not in close_d.columns:
                continue
            vals = close_d[sym].dropna().values
            if len(vals) < 2:
                continue
            result[key]["close"] = float(vals[-1])
            if key not in ("tnx", "tyx"):
                result[key]["chg_pct"] = float((vals[-1] - vals[-2]) / vals[-2] * 100)
    except Exception as e:
        print(f"  大盤日線下載失敗：{e}")

    try:
        year_start = f"{datetime.now().year}-01-01"
        ytd_sym_key = {"^SOX": "sox", "^IXIC": "ndx", "^DJI": "dji", "SPY": "spy"}
        ytd_data = yf.download(list(ytd_sym_key.keys()), start=year_start,
                               auto_adjust=True, progress=False)
        close_y = ytd_data["Close"].dropna(how="all")
        for sym, key in ytd_sym_key.items():
            if sym not in close_y.columns:
                continue
            vals = close_y[sym].dropna().values
            if len(vals) < 2:
                continue
            ytd = float((vals[-1] - vals[0]) / vals[0] * 100)
            if key == "spy":
                result["spy_ytd"] = ytd
            elif key in result:
                result[key]["ytd"] = ytd
    except Exception as e:
        print(f"  YTD 下載失敗：{e}")

    return result


# ═══════════════════════════════════════════════════════════
#  Section 2 — Hot Themes
# ═══════════════════════════════════════════════════════════

def _fetch_news_articles() -> list[tuple[str, str, str]]:
    """多來源 RSS 抓取，回傳 [(來源, 標題, URL), ...]"""
    articles: list[tuple[str, str, str]] = []

    def _rss(rss_url: str, label: str, max_n: int = 10) -> None:
        try:
            r = requests.get(rss_url, headers=_HEADERS, timeout=10)
            for item_xml in re.findall(r"<item>(.*?)</item>", r.text, re.DOTALL):
                t_m = re.search(
                    r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                    item_xml, re.DOTALL,
                )
                l_m = re.search(r"<link>\s*(https?://[^\s<]+)\s*</link>", item_xml)
                if not t_m:
                    continue
                t = t_m.group(1).strip()
                url = l_m.group(1).strip() if l_m else ""
                if t and 10 < len(t) < 150:
                    articles.append((label, t, url))
                    if sum(1 for a in articles if a[0] == label) >= max_n:
                        break
        except Exception:
            pass

    _rss("https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135", "CNBC")
    _rss("https://finance.yahoo.com/rss/topfinstories", "Yahoo Finance")
    _rss("https://feeds.marketwatch.com/marketwatch/topstories/", "MarketWatch")
    _rss("https://feeds.reuters.com/reuters/businessNews", "Reuters")

    try:
        r = requests.get("https://finviz.com/news.ashx", headers=_HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        count = 0
        for tag in soup.select("tr.nn-tab-row a, a.nn-tab-link"):
            t = tag.get_text(strip=True)
            if not t or not (8 < len(t) < 150):
                continue
            href = tag.get("href", "")
            articles.append(("Finviz", t, href if href.startswith("http") else ""))
            count += 1
            if count >= 8:
                break
    except Exception:
        pass

    return articles[:60]


def _gemini_identify_themes(articles: list[tuple[str, str, str]]) -> list[dict]:
    """
    Gemini 分析新聞 → 3~5 個結構性投資主題
    每主題含：名稱、催化劑、預計持續時間、相關股票、主要風險
    """
    if not articles:
        return []

    numbered = "\n".join(f"{i+1}. [{a[0]}] {a[1]}" for i, a in enumerate(articles))
    prompt = (
        "你是一位專注長期基本面的美股投資分析師。\n"
        "請從以下今日財經新聞中，識別 3~5 個目前最具投資價值的產業主題。\n\n"
        "選擇標準：\n"
        "- 有「結構性多年期催化劑」（非短線炒作）\n"
        "- 例如：HBM記憶體短缺→供需缺口→定價權→EPS爆發\n"
        "- 同時包含大型股和成長小型股\n\n"
        "每行一個主題，格式（用 | 分隔，不輸出其他文字）：\n"
        "主題名稱（12字內）"
        "|催化劑說明（70字內，含具體數字或事件）"
        "|預期持續時間（如 12-24個月）"
        "|相關美股代號（8~12個，大小型股混合，逗號分隔）"
        "|主要投資風險（30字內）\n\n"
        f"新聞標題：\n{numbered}"
    )

    text = _gemini_call(prompt, retries=5, delay=20)
    if not text:
        return []

    themes = []
    for line in text.strip().splitlines():
        line = line.strip().lstrip("•·▪*-– 0123456789.")
        if "|" not in line and "｜" not in line:
            continue
        cols = re.split(r"[|｜]", line)
        if len(cols) < 4:
            continue
        raw_tickers = cols[3].strip()
        tickers = [
            t.strip().upper()
            for t in re.split(r"[,，、\s]+", raw_tickers)
            if re.match(r"^[A-Z][A-Z0-9\-]{0,5}$", t.strip().upper())
        ]
        themes.append({
            "theme":    cols[0].strip(),
            "catalyst": cols[1].strip(),
            "horizon":  cols[2].strip() if len(cols) > 2 else "未知",
            "tickers":  tickers[:12],
            "risks":    cols[4].strip() if len(cols) > 4 else "",
        })

    return themes[:5]


# ═══════════════════════════════════════════════════════════
#  Section 3 — Fundamental Screening
# ═══════════════════════════════════════════════════════════

def _fetch_stock_fundamentals(ticker: str) -> dict | None:
    """
    抓取單支股票基本面。
    核心指標：季度 YoY 營收/EPS 成長率 + 加速度 + 毛利率趨勢。
    """
    out: dict = {
        "ticker": ticker,
        "name": ticker, "sector": "", "industry": "",
        "market_cap": None, "market_cap_too_small": False,
        "price": None,
        # 季度成長指標
        "rev_yoy": None, "rev_yoy_prev": None, "rev_accelerating": False,
        "eps_yoy": None, "eps_yoy_prev": None, "eps_accelerating": False,
        "eps_turning_positive": False,
        "gm_now": None, "gm_yr_ago": None, "gm_expanding": None,
        # 最新季資料
        "latest_quarter": "", "latest_q_rev": None, "latest_q_eps": None,
        # 年度資料
        "revenue": [], "eps": [],
        # 分析師
        "target_mean": None, "analyst_upside": None,
        "num_analysts": None, "recommendation": None,
        # 估值
        "trailing_pe": None, "forward_pe": None,
        "pe_52w_high": None, "pe_52w_low": None,
        "reasonable_price": None, "predicted_price": None,
        # 空單
        "short_percent": None, "short_ratio": None,
        # 盤前/盤後
        "premarket_price": None, "premarket_change_pct": None,
        "postmarket_price": None, "postmarket_change_pct": None,
        # 篩選結果
        "filter_reasons": [], "matched_themes": [], "verdict": {},
    }

    try:
        tkr = yf.Ticker(ticker)
        info = tkr.info or {}

        mc = info.get("marketCap") or 0
        out["market_cap"] = mc
        out["market_cap_too_small"] = 0 < mc < 300_000_000
        out["name"] = info.get("longName") or info.get("shortName") or ticker
        out["sector"] = info.get("sector", "")
        out["industry"] = info.get("industry", "")

        cp = info.get("currentPrice") or info.get("regularMarketPrice")
        out["price"] = cp

        # 盤前 / 盤後
        pre_px  = info.get("preMarketPrice")
        pre_chg = info.get("preMarketChangePercent")
        post_px  = info.get("postMarketPrice")
        post_chg = info.get("postMarketChangePercent")
        if pre_px:
            out["premarket_price"]      = round(pre_px, 2)
            out["premarket_change_pct"] = round(float(pre_chg), 2) if pre_chg else None
        if post_px:
            out["postmarket_price"]      = round(post_px, 2)
            out["postmarket_change_pct"] = round(float(post_chg), 2) if post_chg else None

        out["short_percent"]  = info.get("shortPercentOfFloat")
        out["short_ratio"]    = info.get("shortRatio")
        out["target_mean"]    = info.get("targetMeanPrice")
        out["num_analysts"]   = info.get("numberOfAnalystOpinions")
        out["recommendation"] = info.get("recommendationKey", "")
        out["trailing_pe"]    = info.get("trailingPE")
        out["forward_pe"]     = info.get("forwardPE")

        if out["target_mean"] and cp and cp > 0:
            out["analyst_upside"] = round(
                (out["target_mean"] - cp) / cp * 100, 1
            )

        trailing_eps = info.get("trailingEps")
        if trailing_eps and trailing_eps > 0:
            hi52 = info.get("fiftyTwoWeekHigh")
            lo52 = info.get("fiftyTwoWeekLow")
            if hi52: out["pe_52w_high"] = round(hi52 / trailing_eps, 1)
            if lo52: out["pe_52w_low"]  = round(lo52 / trailing_eps, 1)

        pe_hi   = out["pe_52w_high"]
        pe_lo   = out["pe_52w_low"]
        fwd_eps = info.get("forwardEps")
        if pe_hi and pe_lo and trailing_eps and trailing_eps > 0:
            out["reasonable_price"] = round(((pe_hi + pe_lo) / 2) * trailing_eps, 1)
        if out["target_mean"]:
            out["predicted_price"] = out["target_mean"]
        elif fwd_eps and fwd_eps > 0 and pe_hi and pe_lo:
            out["predicted_price"] = round(((pe_hi + pe_lo) / 2) * fwd_eps, 1)

        # 年度財報
        try:
            stmt = tkr.income_stmt
            if stmt is not None and not stmt.empty:
                cols3 = stmt.columns[:3]
                for key in ("Total Revenue", "Operating Revenue"):
                    if key in stmt.index:
                        out["revenue"] = [
                            (col.year, round(float(stmt.loc[key, col]) / 1e9, 2))
                            for col in cols3
                            if pd.notna(stmt.loc[key, col]) and stmt.loc[key, col] > 0
                        ]
                        break
                for key in ("Basic EPS", "Diluted EPS"):
                    if key in stmt.index:
                        out["eps"] = [
                            (col.year, round(float(stmt.loc[key, col]), 2))
                            for col in cols3
                            if pd.notna(stmt.loc[key, col])
                        ]
                        break
        except Exception:
            pass

        # 季度財報（成長 + 加速度）
        try:
            qstmt = tkr.quarterly_income_stmt
            if qstmt is not None and not qstmt.empty:
                nc = len(qstmt.columns)
                q0 = qstmt.columns[0]
                q1 = qstmt.columns[1] if nc > 1 else None
                q4 = qstmt.columns[4] if nc > 4 else None
                q5 = qstmt.columns[5] if nc > 5 else None

                out["latest_quarter"] = f"{q0.year}Q{(q0.month - 1) // 3 + 1}"

                rev_key = next(
                    (k for k in ("Total Revenue", "Operating Revenue") if k in qstmt.index), None
                )
                eps_key = next(
                    (k for k in ("Basic EPS", "Diluted EPS") if k in qstmt.index), None
                )

                if rev_key:
                    r0 = qstmt.loc[rev_key, q0]
                    if pd.notna(r0) and r0 > 0:
                        out["latest_q_rev"] = round(float(r0) / 1e9, 2)
                    if q4 is not None:
                        r4 = qstmt.loc[rev_key, q4]
                        if pd.notna(r0) and pd.notna(r4) and r4 != 0:
                            out["rev_yoy"] = round(float(r0 - r4) / abs(float(r4)) * 100, 1)
                    if q1 is not None and q5 is not None:
                        r1 = qstmt.loc[rev_key, q1]
                        r5 = qstmt.loc[rev_key, q5]
                        if pd.notna(r1) and pd.notna(r5) and r5 != 0:
                            out["rev_yoy_prev"] = round(float(r1 - r5) / abs(float(r5)) * 100, 1)
                    if out["rev_yoy"] is not None and out["rev_yoy_prev"] is not None:
                        out["rev_accelerating"] = out["rev_yoy"] > out["rev_yoy_prev"]

                if eps_key:
                    e0 = qstmt.loc[eps_key, q0]
                    if pd.notna(e0):
                        out["latest_q_eps"] = round(float(e0), 2)
                    if q4 is not None:
                        e4 = qstmt.loc[eps_key, q4]
                        if pd.notna(e0) and pd.notna(e4) and e4 != 0:
                            out["eps_yoy"] = round(float(e0 - e4) / abs(float(e4)) * 100, 1)
                        if pd.notna(e0) and pd.notna(e4):
                            out["eps_turning_positive"] = float(e0) > 0 and float(e4) < 0
                    if q1 is not None and q5 is not None:
                        e1 = qstmt.loc[eps_key, q1]
                        e5 = qstmt.loc[eps_key, q5]
                        if pd.notna(e1) and pd.notna(e5) and e5 != 0:
                            out["eps_yoy_prev"] = round(float(e1 - e5) / abs(float(e5)) * 100, 1)
                    if out["eps_yoy"] is not None and out["eps_yoy_prev"] is not None:
                        out["eps_accelerating"] = out["eps_yoy"] > out["eps_yoy_prev"]

                # 毛利率趨勢
                gp_key = "Gross Profit"
                if gp_key in qstmt.index and rev_key:
                    gp0 = qstmt.loc[gp_key, q0]
                    r0v  = qstmt.loc[rev_key, q0]
                    if pd.notna(gp0) and pd.notna(r0v) and r0v > 0:
                        out["gm_now"] = round(float(gp0) / float(r0v) * 100, 1)
                    if q4 is not None:
                        gp4 = qstmt.loc[gp_key, q4]
                        r4v  = qstmt.loc[rev_key, q4]
                        if pd.notna(gp4) and pd.notna(r4v) and r4v > 0:
                            out["gm_yr_ago"] = round(float(gp4) / float(r4v) * 100, 1)
                    if out["gm_now"] is not None and out["gm_yr_ago"] is not None:
                        out["gm_expanding"] = out["gm_now"] >= out["gm_yr_ago"] - 1.0
        except Exception:
            pass

    except Exception as e:
        print(f"    {ticker} 基本面失敗：{e}")
        return None

    return out


def _apply_fundamental_filter(stocks: list[dict]) -> list[dict]:
    """
    任一條件成立即通過：
    A. 強成長：營收 YoY > 10% 且 (EPS 為正 or EPS 加速)
    B. 虧轉盈：eps_turning_positive
    C. 分析師強烈看好：upside > 20% 且營收 > 5%
    D. 雙加速：營收 + EPS 同步加速且營收 > 5%
    """
    passed = []
    for s in stocks:
        mc = s.get("market_cap") or 0
        if 0 < mc < 300_000_000:
            continue

        rev_yoy = s.get("rev_yoy")
        eps_q   = s.get("latest_q_eps")
        eps_acc = s.get("eps_accelerating", False)
        rev_acc = s.get("rev_accelerating", False)
        turning = s.get("eps_turning_positive", False)
        upside  = s.get("analyst_upside") or 0

        reasons = []

        if rev_yoy is not None and rev_yoy > 10:
            if (eps_q is not None and eps_q > 0) or eps_acc:
                reasons.append(f"A: 營收 YoY {rev_yoy:+.0f}% + EPS {'加速' if eps_acc else '正'}")

        if turning:
            reasons.append("B: 虧損轉盈利")

        if upside > 20 and rev_yoy is not None and rev_yoy > 5:
            reasons.append(f"C: 分析師看漲 {upside:.0f}%")

        if rev_acc and eps_acc and rev_yoy is not None and rev_yoy > 5:
            reasons.append("D: 營收+EPS雙加速")

        if reasons:
            s["filter_reasons"] = reasons
            passed.append(s)

    return passed


def _gemini_longterm_verdict(
    stocks: list[dict],
    themes: list[dict],
    tnx: float | None,
    vix: float | None,
) -> dict[str, dict]:
    """長期評級：積極布局 / 值得追蹤 / 等待回調 / 暫時觀望"""
    if not stocks:
        return {}

    theme_ctx = "\n".join(
        f"- {t['theme']}：{t['catalyst']}（預計 {t['horizon']}）"
        for t in themes
    ) or "（主題識別失敗）"

    macro_lines = []
    if tnx:
        macro_lines.append(f"10Y殖利率 {tnx:.2f}%（{'偏高，成長股承壓' if tnx > 4.5 else '溫和'}）")
    if vix:
        macro_lines.append(f"VIX {vix:.1f}（{'高恐慌' if vix > 25 else '正常'}）")
    macro_ctx = " | ".join(macro_lines) or "宏觀環境正常"

    lines = []
    for s in stocks:
        rev_s  = f"{s['rev_yoy']:+.0f}%{'↑' if s.get('rev_accelerating') else '↓'}" if s.get("rev_yoy") is not None else "N/A"
        eps_s  = f"{s['eps_yoy']:+.0f}%{'↑' if s.get('eps_accelerating') else ''}" if s.get("eps_yoy") is not None else "N/A"
        turn_s = " 🔄虧轉盈" if s.get("eps_turning_positive") else ""
        up_s   = f"目標+{s['analyst_upside']:.0f}%" if s.get("analyst_upside") else "無目標"
        gm_s   = f"毛利率{s['gm_now']:.0f}%({'↑' if s.get('gm_expanding') else '↓'})" if s.get("gm_now") else ""
        fpe_s  = f"前瞻PE {s['forward_pe']:.1f}x" if s.get("forward_pe") else ""

        lines.append(
            f"{s['ticker']} {s.get('name', s['ticker'])}（{s.get('sector', '')}）："
            f"營收YoY {rev_s} | EPS YoY {eps_s}{turn_s} | {gm_s} | {up_s} | {fpe_s}"
        )

    prompt = (
        "你是一位專注 6~18 個月持有期的美股基本面投資分析師。\n\n"
        f"【今日熱門投資主題】\n{theme_ctx}\n\n"
        f"【宏觀環境】{macro_ctx}\n\n"
        "【評級定義】\n"
        "- 積極布局：催化劑明確、基本面加速、現在是好進場點\n"
        "- 值得追蹤：方向正確，等下季財報確認或更好買點\n"
        "- 等待回調：基本面好但股價已充分反映，等回調再建倉\n"
        "- 暫時觀望：主題有潛力但時機未到或有重大不確定性\n\n"
        "格式（每行一支，不輸出其他文字）：\n"
        "代號|評級|投資邏輯（≤40字）|主題標籤1,標籤2|最大風險（≤20字）\n\n"
        "股票清單：\n" + "\n".join(lines)
    )

    text = _gemini_call(prompt)
    if not text:
        return {}

    result: dict[str, dict] = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        cols = re.split(r"[|｜]", line)
        if len(cols) < 3:
            continue
        m = re.match(r"^([A-Z][A-Z0-9\-]{0,8})", cols[0].strip())
        if not m:
            continue
        tk = m.group(1)
        tags = (
            [t.strip() for t in re.split(r"[,，、]", cols[3]) if t.strip()]
            if len(cols) > 3 else []
        )
        result[tk] = {
            "rating": cols[1].strip(),
            "reason": cols[2].strip(),
            "themes": tags[:4],
            "risk":   cols[4].strip() if len(cols) > 4 else "",
        }

    return result


def _build_longterm_radar(tnx: float | None, vix: float | None) -> tuple[list[dict], list[dict]]:
    """完整管線，回傳 (themes, stocks)"""
    print("  抓取財經新聞…")
    articles = _fetch_news_articles()
    print(f"  取得 {len(articles)} 則標題")

    print("  Gemini 識別投資主題…")
    themes = _gemini_identify_themes(articles)
    print(f"  識別出 {len(themes)} 個主題")
    for t in themes:
        print(f"    📌 {t['theme']}：{t['tickers']}")

    theme_tickers: set[str] = set()
    for t in themes:
        theme_tickers.update(t.get("tickers", []))

    screen_tickers = list(theme_tickers | set(_GROWTH_SUPPLEMENT))
    print(f"  篩選池：{len(screen_tickers)} 支（主題 {len(theme_tickers)} + 成長池）")

    # 驗證代號
    print("  驗證股票代號…")
    try:
        val = yf.download(screen_tickers, period="2d", auto_adjust=True, progress=False)
        valid_close = val["Close"].dropna(how="all")
        valid_tickers = [t for t in screen_tickers
                         if t in valid_close.columns and not valid_close[t].dropna().empty]
    except Exception as e:
        print(f"  驗證失敗（使用原始清單）：{e}")
        valid_tickers = screen_tickers

    print(f"  有效：{len(valid_tickers)} 支")

    # 抓基本面（5執行緒）
    print(f"  抓取 {len(valid_tickers)} 支基本面（5執行緒）…")
    all_funds: list[dict] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_map = {executor.submit(_fetch_stock_fundamentals, t): t
                      for t in valid_tickers}
        done = 0
        for future in as_completed(future_map):
            r = future.result()
            if r:
                all_funds.append(r)
            done += 1
            if done % 20 == 0:
                print(f"    進度 {done}/{len(valid_tickers)}")

    print(f"  取得基本面：{len(all_funds)} 支")

    # 篩選
    passed = _apply_fundamental_filter(all_funds)
    print(f"  通過篩選：{len(passed)} 支")

    if not passed:
        return themes, []

    # 標記所屬主題
    for s in passed:
        s["matched_themes"] = [
            t["theme"] for t in themes if s["ticker"] in t.get("tickers", [])
        ]

    # Gemini 評級
    print(f"  Gemini 長期評級 {len(passed)} 支…")
    verdicts = _gemini_longterm_verdict(passed, themes, tnx, vix)
    for s in passed:
        s["verdict"] = verdicts.get(s["ticker"], {})

    rating_order = {"積極布局": 0, "值得追蹤": 1, "等待回調": 2, "暫時觀望": 3}
    passed.sort(key=lambda x: (
        rating_order.get(x.get("verdict", {}).get("rating", ""), 4),
        -(x.get("analyst_upside") or 0),
        -(x.get("rev_yoy") or 0),
    ))

    return themes, passed[:MAX_STOCKS]


# ═══════════════════════════════════════════════════════════
#  Section 4 — Momentum Tracking
# ═══════════════════════════════════════════════════════════

def _save_us_scout_history(stocks: list[dict]) -> None:
    today = datetime.now().strftime("%Y%m%d")
    try:
        with open(_HISTORY_FILE, encoding="utf-8") as f:
            history: dict = json.load(f)
    except Exception:
        history = {}

    history[today] = [
        {
            "ticker": s["ticker"],
            "name":   s.get("name", s["ticker"]),
            "rating": s.get("verdict", {}).get("rating", ""),
            "reason": s.get("verdict", {}).get("reason", ""),
            "price":  s.get("price"),
        }
        for s in stocks
        if s.get("verdict", {}).get("rating")
    ]

    # 只保留最近 22 個交易日（約 1 個月），供首頁「近期熱門股」統計使用
    for old_date in sorted(history.keys(), reverse=True)[22:]:
        del history[old_date]

    try:
        with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        print(f"  歷史記錄儲存：{today}（{len(history[today])} 筆）")
    except Exception as e:
        print(f"  歷史記錄儲存失敗：{e}")


def _build_top_picks_html() -> str:
    """
    統計 us_scout_history.json 裡近期（最多保留 1 個月交易日）被選中
    次數最多的前 5 檔股票，回傳 HTML 字串；無資料時回傳空字串。
    """
    try:
        with open(_HISTORY_FILE, encoding="utf-8") as f:
            history: dict = json.load(f)
    except Exception:
        return ""

    if not history:
        return ""

    counts: dict[str, int] = {}
    latest: dict[str, dict] = {}
    for date_str in sorted(history.keys()):
        for s in history[date_str]:
            ticker = s.get("ticker", "")
            if not ticker:
                continue
            counts[ticker] = counts.get(ticker, 0) + 1
            latest[ticker] = s  # 日期由舊到新遍歷，最後寫入的就是最新一筆

    top5 = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    if not top5:
        return ""

    items_html = ""
    for rank, (ticker, cnt) in enumerate(top5, 1):
        info = latest.get(ticker, {})
        name = info.get("name", "")
        items_html += f"""
        <div class="top-item">
          <div class="top-rank">#{rank}</div>
          <div class="top-code">{ticker}</div>
          <div class="top-name">{name}</div>
          <div class="top-count">{cnt}<span class="unit"> 次</span></div>
        </div>"""

    n_days = len(history)
    return f"""
  <div class="top-wrap">
    <div class="top-hdr">
      <h2>🏆 近期熱門股</h2>
      <p>近 {n_days} 個交易日通過選股雷達次數最多的前 5 檔個股</p>
    </div>
    <div class="top-body">{items_html}</div>
  </div>"""


def _build_us_review_html() -> str:
    try:
        with open(_HISTORY_FILE, encoding="utf-8") as f:
            history: dict = json.load(f)
    except Exception:
        return ""

    target_date = _last_us_trading_date(3)
    stocks  = history.get(target_date, [])
    tracked = [s for s in stocks if s.get("rating") in ("積極布局", "值得追蹤")]
    if not tracked:
        return ""

    date_display = f"{target_date[:4]}/{target_date[4:6]}/{target_date[6:]}"
    rows_html = ""
    for s in tracked:
        ticker = s["ticker"]
        old_px = s.get("price") or 0
        cur_px = None
        try:
            df = yf.download(ticker, period="5d", auto_adjust=True,
                             progress=False, multi_level_index=False)
            df = df.dropna(subset=["Close"])
            if len(df) >= 1:
                cur_px = float(df["Close"].values.flatten()[-1])
        except Exception:
            pass

        if cur_px and old_px and old_px > 0:
            ret       = (cur_px - old_px) / old_px * 100
            ret_str   = f"{'+'if ret>=0 else ''}{ret:.1f}%"
            ret_color = "#2e7d32" if ret >= 0 else "#c62828"
            emoji     = "🟢" if ret >= 0 else "🔴"
        else:
            ret_str, ret_color, emoji = "N/A", "#888", "⚪"

        yf_url = f"https://finance.yahoo.com/quote/{ticker}"
        rows_html += f"""
        <tr>
          <td><a href="{yf_url}" style="color:#1a237e;text-decoration:none">
              {ticker} {s.get('name', ticker)}</a></td>
          <td style="text-align:right;color:#555">{"$"+f"{old_px:,.2f}" if old_px else "N/A"}</td>
          <td style="text-align:right;color:#555">{"$"+f"{cur_px:,.2f}" if cur_px else "N/A"}</td>
          <td style="text-align:right;font-weight:bold;color:{ret_color}">{emoji} {ret_str}</td>
          <td style="color:#666;font-size:12px">{s.get('reason','')}</td>
        </tr>"""

    if not rows_html:
        return ""

    return f"""
  <div class="review-wrap">
    <div class="review-hdr">
      <h2>📋 動能延續追蹤</h2>
      <p>{date_display} 評為「積極布局 / 值得追蹤」— 3 個交易日後績效</p>
    </div>
    <div class="review-body">
      <table class="review-table">
        <thead><tr>
          <th>股票</th><th style="text-align:right">推薦時</th>
          <th style="text-align:right">今日</th><th style="text-align:right">報酬</th>
          <th>當時評語</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </div>"""


# ═══════════════════════════════════════════════════════════
#  HTML Formatting
# ═══════════════════════════════════════════════════════════

def _format_longterm_report_html(
    themes: list[dict],
    radar: list[dict],
    market_overview: dict | None,
    review_html: str = "",
) -> str:
    today = datetime.now().strftime("%Y/%m/%d")
    n = len(radar)

    CSS = """
    body{font-family:'Helvetica Neue',Arial,sans-serif;background:#f4f4f4;margin:0;padding:20px}
    .wrap{max-width:720px;margin:0 auto;background:#fff;border-radius:8px;
          box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden}
    .hdr{padding:18px 24px 14px;border-bottom:1px solid #eee}
    .hdr h2{margin:0;font-size:20px;color:#1a237e}
    .hdr p{margin:4px 0 0;font-size:13px;color:#666}
    .card{border-left:4px solid #3949ab;margin:16px;padding:12px 16px;
          background:#fafafa;border-radius:4px}
    .card h3{margin:0 0 6px;font-size:16px;color:#1a237e}
    .row{font-size:13px;margin:3px 0;color:#333}
    table.fin{border-collapse:collapse;font-size:13px;margin:6px 0;width:100%}
    table.fin td{padding:3px 12px 3px 0;color:#444;vertical-align:top}
    table.fin td:first-child{color:#666;white-space:nowrap;font-weight:500;width:110px}
    .growth-box{background:#e8f5e9;border-radius:4px;padding:8px 12px;font-size:14px;
                font-weight:bold;margin:6px 0;color:#1b5e20;line-height:1.6}
    .gm-box{background:#e8f5e9;border-radius:4px;padding:6px 10px;font-size:13px;margin:4px 0}
    .pe-bar{background:#e3f2fd;border-radius:4px;padding:6px 10px;font-size:13px;margin:4px 0}
    .price-box{background:#fff3e0;border-radius:4px;padding:6px 10px;font-size:13px;margin:4px 0}
    .analyst-box{background:#f3e5f5;border-radius:4px;padding:6px 10px;font-size:13px;margin:4px 0}
    .risk-box{background:#fff8e1;border-radius:4px;padding:6px 10px;font-size:13px;
              margin:4px 0;color:#bf360c}
    .verdict-box{border-radius:6px;padding:8px 12px;font-size:14px;font-weight:bold;margin:8px 0}
    .v-aggressive{background:#1b5e20;color:#fff}
    .v-watch{background:#1565c0;color:#fff}
    .v-dip{background:#e65100;color:#fff}
    .v-wait{background:#37474f;color:#fff}
    .footer{text-align:center;font-size:11px;color:#aaa;padding:14px;border-top:1px solid #eee}
    /* Top picks */
    .top-wrap{max-width:720px;margin:0 auto 16px;background:#fff;border-radius:8px;
              box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden}
    .top-hdr{padding:14px 20px 12px;border-bottom:1px solid #eee}
    .top-hdr h2{margin:0;font-size:18px;color:#6a1b9a}
    .top-hdr p{margin:3px 0 0;font-size:12px;color:#666}
    .top-body{display:flex;flex-wrap:wrap}
    .top-item{flex:1 1 20%;padding:14px 16px;border-right:1px solid #f0f0f0;
              min-width:120px;box-sizing:border-box;text-align:center}
    .top-item:last-child{border-right:none}
    .top-rank{font-size:11px;color:#888;font-weight:bold;margin-bottom:4px}
    .top-code{font-size:14px;font-weight:bold;color:#1a237e}
    .top-name{font-size:11px;color:#666;margin:2px 0 6px;white-space:nowrap;
              overflow:hidden;text-overflow:ellipsis}
    .top-count{font-size:20px;font-weight:bold;color:#c62828}
    .top-count .unit{font-size:11px;color:#888;font-weight:normal;margin-left:2px}
    /* Market */
    .mkt-wrap{max-width:720px;margin:0 auto 16px;background:#fff;border-radius:8px;
              box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden}
    .mkt-hdr{padding:14px 20px 12px;border-bottom:1px solid #eee}
    .mkt-hdr h2{margin:0;font-size:18px;color:#37474f}
    .mkt-hdr p{margin:3px 0 0;font-size:12px;color:#666}
    .mkt-body{display:flex;flex-wrap:wrap;padding:0}
    .mkt-item{flex:1 1 20%;padding:12px 16px;border-right:1px solid #f0f0f0;
              border-bottom:1px solid #f0f0f0;min-width:130px;box-sizing:border-box}
    .mkt-item-label{font-size:10px;color:#888;text-transform:uppercase;
                    letter-spacing:.5px;margin-bottom:4px}
    .mkt-item-val{font-size:19px;font-weight:bold;margin-bottom:2px}
    .mkt-item-sub{font-size:12px;color:#666}
    .mkt-up{color:#2e7d32} .mkt-dn{color:#c62828} .mkt-neu{color:#555}
    /* Themes */
    .theme-wrap{max-width:720px;margin:0 auto 16px;background:#fff;border-radius:8px;
                box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden}
    .theme-hdr{padding:14px 20px 12px;border-bottom:1px solid #eee}
    .theme-hdr h2{margin:0;font-size:18px;color:#4527a0}
    .theme-hdr p{margin:3px 0 0;font-size:12px;color:#666}
    .theme-card{padding:14px 20px;border-bottom:1px solid #ede7f6;background:#faf8ff}
    .theme-card:last-child{border-bottom:none}
    .theme-name{font-size:15px;font-weight:bold;color:#4527a0;margin-bottom:5px}
    .theme-catalyst{font-size:13px;color:#333;line-height:1.65;margin-bottom:4px}
    .theme-meta{font-size:12px;color:#888;margin-bottom:6px}
    .theme-risk{background:#fff8e1;border-radius:4px;padding:2px 8px;font-size:12px;
                color:#e65100;display:inline-block}
    /* Review */
    .review-wrap{max-width:720px;margin:16px auto 0;background:#fff;border-radius:8px;
                 box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden}
    .review-hdr{padding:14px 20px 12px;border-bottom:1px solid #eee}
    .review-hdr h2{margin:0;font-size:18px;color:#2e7d32}
    .review-hdr p{margin:3px 0 0;font-size:12px;color:#666}
    .review-body{padding:12px 16px}
    .review-table{width:100%;border-collapse:collapse;font-size:13px}
    .review-table th{background:#eceff1;color:#37474f;padding:7px 10px;
                     text-align:left;font-weight:600;white-space:nowrap}
    .review-table td{padding:7px 10px;border-bottom:1px solid #f0f0f0;vertical-align:top}
    .review-table tr:last-child td{border-bottom:none}
    .small-cap-warn{background:#fff9c4;border-radius:4px;padding:2px 7px;
                    font-size:11px;color:#f57f17;margin-left:5px}
    """

    # ── 大盤概況 ─────────────────────────────────────────────
    mo = market_overview or {}

    def _idx_block(key: str, label: str) -> str:
        d     = mo.get(key, {})
        close = d.get("close")
        chg   = d.get("chg_pct")
        ytd   = d.get("ytd")
        if close is None:
            return (f'<div class="mkt-item"><div class="mkt-item-label">{label}</div>'
                    f'<div class="mkt-item-val mkt-neu">—</div></div>')
        cls   = "mkt-up" if (chg or 0) >= 0 else "mkt-dn"
        arrow = "▲" if (chg or 0) >= 0 else "▼"
        sign  = "+" if (chg or 0) >= 0 else ""
        cs    = f"{close:,.0f}" if close > 999 else f"{close:,.2f}"
        ytd_h = ""
        if ytd is not None:
            yc = "mkt-up" if ytd >= 0 else "mkt-dn"
            ys = "+" if ytd >= 0 else ""
            ytd_h = f'<div class="mkt-item-sub {yc}">YTD {ys}{ytd:.1f}%</div>'
        return (f'<div class="mkt-item"><div class="mkt-item-label">{label}</div>'
                f'<div class="mkt-item-val {cls}">{cs}</div>'
                f'<div class="mkt-item-sub {cls}">{arrow} {sign}{chg:.2f}%</div>{ytd_h}</div>')

    vix_d   = mo.get("vix", {})
    vix_val = vix_d.get("close")
    vix_chg = vix_d.get("chg_pct")
    if vix_val is not None:
        vcls  = "mkt-dn" if vix_val >= 25 else "mkt-neu"
        varr  = "▲" if (vix_chg or 0) >= 0 else "▼"
        vsign = "+" if (vix_chg or 0) >= 0 else ""
        vix_block = (
            f'<div class="mkt-item"><div class="mkt-item-label">VIX 恐慌指數</div>'
            f'<div class="mkt-item-val {vcls}">{vix_val:.1f}</div>'
            + (f'<div class="mkt-item-sub {vcls}">{varr} {vsign}{vix_chg:.2f}%</div>'
               if vix_chg is not None else "")
            + '</div>'
        )
    else:
        vix_block = ('<div class="mkt-item"><div class="mkt-item-label">VIX</div>'
                     '<div class="mkt-item-val mkt-neu">—</div></div>')

    tnx_val = mo.get("tnx", {}).get("close")
    if tnx_val is not None:
        tcls  = "mkt-dn" if tnx_val > 4.5 else "mkt-neu"
        tnote = "⚠️ 利率偏高" if tnx_val > 4.5 else "利率溫和"
        tnx_block = (
            f'<div class="mkt-item"><div class="mkt-item-label">10Y 美債殖利率</div>'
            f'<div class="mkt-item-val {tcls}">{tnx_val:.2f}%</div>'
            f'<div class="mkt-item-sub">{tnote}</div></div>'
        )
    else:
        tnx_block = ('<div class="mkt-item"><div class="mkt-item-label">10Y 殖利率</div>'
                     '<div class="mkt-item-val mkt-neu">—</div></div>')

    tyx_val = mo.get("tyx", {}).get("close")
    if tyx_val is not None:
        ycls  = "mkt-dn" if tyx_val > 4.5 else "mkt-neu"
        ynote = "⚠️ 利率偏高" if tyx_val > 4.5 else "利率溫和"
        tyx_block = (
            f'<div class="mkt-item"><div class="mkt-item-label">30Y 美債殖利率</div>'
            f'<div class="mkt-item-val {ycls}">{tyx_val:.2f}%</div>'
            f'<div class="mkt-item-sub">{ynote}</div></div>'
        )
    else:
        tyx_block = ('<div class="mkt-item"><div class="mkt-item-label">30Y 殖利率</div>'
                     '<div class="mkt-item-val mkt-neu">—</div></div>')

    market_html = f"""
  <div class="mkt-wrap">
    <div class="mkt-hdr">
      <h2>📊 今日大盤概況</h2>
      <p>{today}　資料來源：yfinance（^SOX / ^IXIC / ^DJI / ^VIX / ^TNX / ^TYX）</p>
    </div>
    <div class="mkt-body">
      {_idx_block("sox","費城半導體 ^SOX")}
      {_idx_block("ndx","納斯達克 ^IXIC")}
      {_idx_block("dji","道瓊工業 ^DJI")}
      {vix_block}{tnx_block}{tyx_block}
    </div>
  </div>"""

    # ── 熱門主題 ─────────────────────────────────────────────
    theme_cards = ""
    for t in themes:
        ticker_chips = " ".join(
            f'<a href="https://finance.yahoo.com/quote/{tk}" target="_blank" '
            f'style="display:inline-block;background:#ede7f6;color:#4527a0;'
            f'border-radius:10px;padding:1px 8px;font-size:12px;'
            f'margin:2px;text-decoration:none">{tk}</a>'
            for tk in t.get("tickers", [])
        )
        risk_html = (
            f'<span class="theme-risk">⚠️ 風險：{t["risks"]}</span>'
            if t.get("risks") else ""
        )
        theme_cards += f"""
    <div class="theme-card">
      <div class="theme-name">📌 {t['theme']}</div>
      <div class="theme-catalyst">{t['catalyst']}</div>
      <div class="theme-meta">預計持續：{t.get('horizon','未知')}</div>
      <div>{ticker_chips}</div>
      <div style="margin-top:6px">{risk_html}</div>
    </div>"""

    if not theme_cards:
        theme_cards = '<div class="theme-card" style="color:#aaa">⏳ 今日主題識別失敗</div>'

    theme_html = f"""
  <div class="theme-wrap">
    <div class="theme-hdr">
      <h2>🔍 今日熱門投資主題</h2>
      <p>{today}　資料來源：CNBC / Yahoo Finance / MarketWatch / Reuters / Finviz → Gemini AI 識別</p>
    </div>
    {theme_cards}
  </div>"""

    # ── 股票卡片 ──────────────────────────────────────────────
    blocks = []
    for s in radar:
        ticker  = s["ticker"]
        name    = s.get("name", ticker)
        sector  = s.get("sector", "")
        price   = s.get("price")
        mc      = s.get("market_cap") or 0
        mc_str  = f"${mc/1e9:.1f}B" if mc >= 1e9 else (f"${mc/1e6:.0f}M" if mc > 0 else "—")
        too_sm  = s.get("market_cap_too_small", False)
        verdict = s.get("verdict") or {}

        # 盤前/盤後 badge
        pre_px  = s.get("premarket_price")
        pre_chg = s.get("premarket_change_pct")
        post_px  = s.get("postmarket_price")
        post_chg = s.get("postmarket_change_pct")
        if pre_px and pre_chg is not None:
            ext_label, ext_px, ext_chg = "盤前", pre_px, pre_chg
        elif post_px and post_chg is not None:
            ext_label, ext_px, ext_chg = "盤後", post_px, post_chg
        else:
            ext_label = ext_px = ext_chg = None

        if ext_chg is not None:
            ec  = "#2e7d32" if ext_chg >= 0 else "#c62828"
            ebg = "#e8f5e9" if ext_chg >= 0 else "#ffebee"
            ea  = "▲" if ext_chg >= 0 else "▼"
            es  = "+" if ext_chg >= 0 else ""
            ext_badge = (
                f'&nbsp;<span style="background:{ebg};color:{ec};padding:1px 8px;'
                f'border-radius:10px;font-size:12px;font-weight:bold">'
                f'{ext_label} {ea} {es}{ext_chg:.2f}%'
                f'&nbsp;<span style="font-weight:normal">${ext_px:,.2f}</span></span>'
            )
        else:
            ext_badge = ""

        yf_url    = f"https://finance.yahoo.com/quote/{ticker}"
        name_link = f'<a href="{yf_url}" style="color:#1a237e;text-decoration:none">{name}</a>'
        sm_warn   = '<span class="small-cap-warn">⚠️ &lt;$300M</span>' if too_sm else ""
        price_str = f'昨收 ${price:,.2f}' if price else ""

        h = (f'<div class="card">'
             f'<h3>▶ {ticker} {name_link}{sm_warn}'
             f'&nbsp;&nbsp;<span style="font-weight:normal;font-size:13px">{price_str}</span>'
             f'{ext_badge}'
             f'&nbsp;<span style="font-weight:normal;font-size:11px;color:#aaa">'
             f'{sector} · {mc_str}</span></h3>')

        # 評級橫幅
        rating = verdict.get("rating", "")
        reason = verdict.get("reason", "")
        risk   = verdict.get("risk", "")
        css_map = {
            "積極布局": "v-aggressive", "值得追蹤": "v-watch",
            "等待回調": "v-dip",       "暫時觀望": "v-wait",
        }
        icon_map = {"積極布局": "🚀", "值得追蹤": "🔎", "等待回調": "⏳", "暫時觀望": "👁"}
        if rating:
            vcls = css_map.get(rating, "v-wait")
            icon = icon_map.get(rating, "")
            h += (f'<div class="verdict-box {vcls}">{icon} {rating}'
                  f'&nbsp;&nbsp;<span style="font-weight:normal;font-size:13px">{reason}</span></div>')
        else:
            h += ('<div class="verdict-box v-wait" style="background:#bdbdbd">'
                  '⏳ AI 評級暫時無法取得</div>')

        # 所屬主題 + AI 標籤
        matched = s.get("matched_themes", [])
        ai_tags = verdict.get("themes", [])
        tag_html = ""
        for tg in (matched + [t for t in ai_tags if t not in matched])[:6]:
            tag_html += (
                f'<span style="background:#ede7f6;color:#4527a0;padding:2px 9px;'
                f'border-radius:10px;font-size:12px;margin-right:4px;'
                f'display:inline-block;margin-bottom:3px">🔖 {tg}</span>'
            )
        if sector:
            tag_html += (
                f'<span style="background:#e3f2fd;color:#0d47a1;padding:2px 9px;'
                f'border-radius:10px;font-size:12px;margin-right:4px;'
                f'display:inline-block;margin-bottom:3px">{sector}</span>'
            )
        if tag_html:
            h += f'<div class="row" style="margin:5px 0">{tag_html}</div>'

        # ── 核心：成長指標 ────────────────────────────────────
        rev_yoy    = s.get("rev_yoy")
        rev_acc    = s.get("rev_accelerating", False)
        eps_yoy    = s.get("eps_yoy")
        eps_acc    = s.get("eps_accelerating", False)
        turning    = s.get("eps_turning_positive", False)

        growth_parts = []
        if rev_yoy is not None:
            arrow = "↑加速" if rev_acc else ("↓減速" if rev_yoy is not None else "")
            color = "#1b5e20" if rev_yoy > 0 else "#c62828"
            growth_parts.append(
                f'營收 YoY <span style="color:{color}">{rev_yoy:+.0f}%</span>'
                f'<span style="font-size:12px;color:#666">{arrow}</span>'
            )
        if eps_yoy is not None:
            arrow = "↑加速" if eps_acc else ""
            turn  = " 🔄虧轉盈" if turning else ""
            color = "#1b5e20" if eps_yoy > 0 else "#c62828"
            growth_parts.append(
                f'EPS YoY <span style="color:{color}">{eps_yoy:+.0f}%</span>'
                f'<span style="font-size:12px;color:#666">{arrow}{turn}</span>'
            )
        elif turning:
            growth_parts.append('EPS <span style="color:#1b5e20">🔄 虧損轉盈利</span>')

        filter_reasons = s.get("filter_reasons", [])
        reason_str = "  |  ".join(filter_reasons) if filter_reasons else ""

        if growth_parts:
            h += (f'<div class="growth-box">'
                  f'📈 {"　　".join(growth_parts)}'
                  + (f'<div style="font-size:11px;font-weight:normal;color:#388e3c;margin-top:3px">'
                     f'篩選依據：{reason_str}</div>' if reason_str else "")
                  + '</div>')

        # ── 財報表格 ─────────────────────────────────────────
        rev_list   = s.get("revenue", [])
        eps_list   = s.get("eps", [])
        latest_q   = s.get("latest_quarter", "")
        lq_rev     = s.get("latest_q_rev")
        lq_eps     = s.get("latest_q_eps")

        if rev_list or eps_list or lq_rev is not None or lq_eps is not None:
            h += '<table class="fin">'
            if rev_list:
                cells = "".join(
                    f'<td><b>${v:.2f}B</b>'
                    f'<span style="color:#aaa;font-size:11px"> ({lbl})</span></td>'
                    for lbl, v in rev_list
                )
                h += f'<tr><td>營收（年）</td>{cells}</tr>'
            if eps_list:
                cells = "".join(
                    f'<td><b style="color:{"#2e7d32" if v>=0 else "#c62828"}">'
                    f'{"+" if v>=0 else ""}${v:.2f}</b>'
                    f'<span style="color:#aaa;font-size:11px"> ({lbl})</span></td>'
                    for lbl, v in eps_list
                )
                h += f'<tr><td>EPS（年）</td>{cells}</tr>'
            if latest_q and (lq_rev is not None or lq_eps is not None):
                q_parts = []
                if lq_rev is not None:
                    q_parts.append(f'營收 <b>${lq_rev:.2f}B</b>')
                if lq_eps is not None:
                    ec2 = "#2e7d32" if lq_eps >= 0 else "#c62828"
                    q_parts.append(
                        f'EPS <b style="color:{ec2}">{"+" if lq_eps >= 0 else ""}${lq_eps:.2f}</b>'
                    )
                h += (f'<tr style="background:#fffde7">'
                      f'<td style="color:#f57f17;font-weight:bold">最新季 ({latest_q})</td>'
                      f'<td colspan="3">{"&nbsp;｜&nbsp;".join(q_parts)}</td></tr>')
            h += '</table>'

        # 毛利率趨勢
        gm_now    = s.get("gm_now")
        gm_yr_ago = s.get("gm_yr_ago")
        gm_exp    = s.get("gm_expanding")
        if gm_now is not None:
            gm_diff = round(gm_now - gm_yr_ago, 1) if gm_yr_ago is not None else None
            gm_icon = "↑ 擴張" if gm_exp else "↓ 收縮"
            gm_color = "#2e7d32" if gm_exp else "#c62828"
            yr_ago_s = f"（去年同期 {gm_yr_ago:.1f}%）" if gm_yr_ago is not None else ""
            diff_s   = (f'<b style="color:{gm_color}"> {gm_icon} {gm_diff:+.1f}%</b>'
                        if gm_diff is not None else "")
            h += (f'<div class="gm-box">▪ 毛利率 <b>{gm_now:.1f}%</b>'
                  f'{yr_ago_s}{diff_s}</div>')

        # 分析師
        t_mean = s.get("target_mean")
        upside = s.get("analyst_upside")
        n_anal = s.get("num_analysts") or 0
        rec_raw = s.get("recommendation", "")
        rec     = rec_raw.replace("_", " ").title() if rec_raw else ""
        if t_mean or rec:
            up_color = "#2e7d32" if (upside or 0) >= 0 else "#c62828"
            up_str   = (f'&nbsp;|&nbsp;距目標 <b style="color:{up_color}">'
                        f'{"+" if (upside or 0)>=0 else ""}{upside:.0f}%</b>'
                        if upside is not None else "")
            t_str = f'目標 <b>${t_mean:.2f}</b>{up_str}' if t_mean else ""
            r_str = (f'&nbsp;|&nbsp;共識 <b>{rec}</b>（{n_anal} 位）' if rec else "")
            h += f'<div class="analyst-box">🎯 {t_str}{r_str}</div>'

        # 最大風險（Gemini 給出）
        if risk:
            h += f'<div class="risk-box">⚠️ 最大風險：{risk}</div>'

        # 估值
        t_pe  = s.get("trailing_pe")
        f_pe  = s.get("forward_pe")
        pe_hi = s.get("pe_52w_high")
        pe_lo = s.get("pe_52w_low")
        pe_parts = []
        if t_pe:
            pe_parts.append(f"目前 <b>{t_pe:.1f}x</b>")
        if pe_hi and pe_lo:
            pe_parts.append(f"52W {pe_lo:.0f}x ~ {pe_hi:.0f}x")
        if f_pe and f_pe > 0:
            pe_parts.append(f"前瞻 <b>{f_pe:.1f}x</b>")
        if pe_parts:
            h += (f'<div class="pe-bar">▪ 本益比：'
                  f'{"&nbsp;|&nbsp;".join(pe_parts)}</div>')

        # 合理 / 預測股價
        r_price = s.get("reasonable_price")
        p_price = s.get("predicted_price")
        pp_parts = []
        if r_price:
            pp_parts.append(f"合理股價 <b>${r_price:,.1f}</b>（52W PE中位 × EPS）")
        if p_price:
            src = "分析師目標" if t_mean else "52W PE中位 × 前瞻EPS"
            pp_parts.append(f"預測股價 <b>${p_price:,.1f}</b>（{src}）")
        if pp_parts:
            h += f'<div class="price-box">▪ {"&nbsp;|&nbsp;".join(pp_parts)}</div>'

        # 空單
        sp = (s.get("short_percent") or 0) * 100
        sr = s.get("short_ratio")
        if sp > 0:
            flag  = " ⚠️ 高空單" if sp > 10 else ""
            dr_s  = f"&nbsp;|&nbsp;Days to Cover {sr:.1f}" if sr else ""
            h += (f'<div style="background:#fce4ec;border-radius:4px;padding:5px 10px;'
                  f'font-size:13px;margin:4px 0">🔻 空單 <b>{sp:.1f}%</b>{dr_s}{flag}</div>')

        h += "</div>"
        blocks.append(h)

    empty_msg = (
        '<div style="padding:20px;color:#888">'
        '今日無符合條件的股票（市場可能休市或尚未有足夠基本面加速動能）</div>'
    )

    top_html = _build_top_picks_html()

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{CSS}</style></head>
<body>
  {top_html}
  {market_html}
  {theme_html}
  <div class="wrap">
    <div class="hdr">
      <h2>🔭 主題基本面選股雷達</h2>
      <p>{today}　共 {n} 支通過篩選（最多 {MAX_STOCKS} 支）
        &nbsp;|&nbsp; 條件：營收YoY>10% + EPS正/加速，或虧轉盈，或分析師看漲>20%
      </p>
    </div>
    {"".join(blocks) if blocks else empty_msg}
    <div class="footer">
      本報告為資訊彙整，非投資建議。長期持有請自行評估風險。<br>
      資料來源：yfinance / CNBC / Yahoo Finance / MarketWatch / Reuters / Finviz / Gemini AI
    </div>
  </div>
  {review_html}
</body></html>"""


# ═══════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════

def run_us_scout() -> str:
    """完整四板塊管線，回傳 HTML 字串。"""
    print("\n── 板塊一：大盤概況 ──")
    market_overview = _fetch_us_market_overview()
    tnx = market_overview.get("tnx", {}).get("close")
    vix = market_overview.get("vix", {}).get("close")
    if tnx and vix:
        print(f"  TNX={tnx:.2f}%  VIX={vix:.1f}")

    print("\n── 板塊二＋三：熱門主題選股 ──")
    themes, radar = _build_longterm_radar(tnx, vix)
    print(f"  最終選出 {len(radar)} 支股票")

    print("\n── 板塊四：動能追蹤 ──")
    _save_us_scout_history(radar)
    review_html = _build_us_review_html()

    return _format_longterm_report_html(themes, radar, market_overview, review_html)
