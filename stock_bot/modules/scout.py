"""
scout.py — 台股選股雷達管線
四個階段：
  1. Scouting     — TWSE/TPEx 量排 + CnYes/bnext 新聞話題
  2. Tech Filter  — 5MA 趨勢 + 1.1x 量比
  3. Global Check — 美日代理指標昨日表現
  4. Format       — 回傳 Telegram 格式字串
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

from config import TW_SECTOR_STOCKS, TW_STOCK_NAMES
from modules.portfolio import fetch_cnyes_batch, cnyes_news_for_codes

try:
    from google import genai as _genai
    _HAS_GENAI = True
except ImportError:
    _HAS_GENAI = False

# 歷史記錄檔（用於昨日回顧模組）
_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scout_history.json"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# ─────────────────────────────────────────────────────────
#  Gemini helper (lazy singleton, same pattern as summarizer.py)
# ─────────────────────────────────────────────────────────

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
    """
    Gemini 呼叫包裝。
    - 503 UNAVAILABLE → 等待後重試（最多 3 次，間隔遞增）
    - 429 QUOTA EXHAUSTED → 切換備用 model
    - 429 含 retryDelay → 解析等待秒數後重試
    回傳回應文字，失敗則回傳空字串。

    注意：gemini-2.5-flash 預設開啟 thinking 模式，thinking token 會大量消耗配額
    但對批次分類、格式化輸出等任務不需要深度思考，一律設 thinking_budget=0。
    """
    client = _get_gemini()
    if not client:
        return ""

    # 依可用性排列；gemini-2.0-flash 免費配額耗盡時仍保留作備援
    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

    # 關閉 thinking：節省 60~80% token 配額（這些任務不需要推理）
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
                # 只有 2.5-flash 支援 thinking_config；其他 model 不帶此參數
                if _thinking_off and "2.5" in model:
                    kwargs["config"] = _thinking_off
                resp = client.models.generate_content(**kwargs)
                return resp.text or ""
            except Exception as e:
                err     = str(e).lower()
                err_raw = str(e)
                is_unavail = "503" in err or "unavailable" in err
                is_quota   = "429" in err or "quota" in err or "exhausted" in err

                if is_unavail:
                    wait = delay * (attempt + 1)   # 15s → 30s → 45s
                    if attempt < retries - 1:
                        print(f"    Gemini ({model}) 暫時不可用，{wait}s 後重試（{attempt+1}/{retries}）...")
                        time.sleep(wait)
                    else:
                        print(f"    Gemini ({model}) 持續不可用，切換備用 model...")
                        break
                elif is_quota:
                    # 嘗試解析 retryDelay（秒），最多等 90s 後重試同一 model
                    import re as _re
                    retry_m = _re.search(r'"retryDelay":\s*"(\d+)s"', err_raw)
                    wait_s  = int(retry_m.group(1)) if retry_m else 0
                    if wait_s and wait_s <= 90 and attempt < retries - 1:
                        print(f"    Gemini ({model}) 速率限制，等待 {wait_s}s 後重試...")
                        time.sleep(wait_s + 2)
                    else:
                        print(f"    Gemini ({model}) 配額耗盡，切換備用 model...")
                        break
                else:
                    print(f"    Gemini ({model}) 錯誤：{err_raw[:80]}")
                    break
    return ""


# ═══════════════════════════════════════════════════════════
#  Stage 1 — Scouting
# ═══════════════════════════════════════════════════════════

def _twse_top20() -> list[dict]:
    """
    TWSE 官方 API：今日成交量前 20 名上市股。
    回傳 [{"code": "2330", "name": "台積電", "close": 835.0, "volume": 12345678}]
    """
    try:
        today = datetime.now().strftime("%Y%m%d")
        r = requests.get(
            f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX20"
            f"?response=json&date={today}",
            headers=_HEADERS, timeout=10,
        )
        data = r.json()
        rows = data.get("data") or []
        result = []
        for row in rows[:20]:
            # 欄位順序：排名, 代號, 名稱, 成交量, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌, 漲跌幅
            try:
                code   = str(row[1]).strip()
                name   = str(row[2]).strip()
                vol    = int(str(row[3]).replace(",", ""))
                close  = float(str(row[8]).replace(",", ""))
                result.append({"code": code, "name": name, "close": close, "volume": vol})
            except (IndexError, ValueError):
                continue
        return result
    except Exception:
        return []


def _tpex_top20() -> list[dict]:
    """
    TPEx (上櫃) 成交量排行前 20 名。
    """
    try:
        r = requests.get(
            "https://www.tpex.org.tw/web/stock/trading/highlight/ranking.php"
            "?l=zh-tw&o=json",
            headers=_HEADERS, timeout=10,
        )
        data = r.json()
        rows = data.get("aaData") or []
        result = []
        for row in rows[:20]:
            try:
                code  = str(row[0]).strip()
                name  = str(row[1]).strip()
                vol   = int(str(row[2]).replace(",", ""))
                close = float(str(row[8]).replace(",", ""))
                result.append({"code": code, "name": name, "close": close, "volume": vol})
            except (IndexError, ValueError):
                continue
        return result
    except Exception:
        return []


def _yahoo_top_active_fallback() -> list[dict]:
    """
    Yahoo 奇摩股市 most-active-stocks 頁面 HTML fallback。
    當 TWSE/TPEx API 無資料時啟用。
    """
    try:
        r = requests.get(
            "https://tw.stock.yahoo.com/most-active-stocks",
            headers=_HEADERS, timeout=12,
        )
        soup = BeautifulSoup(r.text, "html.parser")
        result = []
        for row in soup.select("table tbody tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            try:
                raw_code = cells[0].get_text(strip=True)
                code = re.search(r"\d{4,5}", raw_code)
                if not code:
                    continue
                code = code.group()
                name  = cells[1].get_text(strip=True)
                close_text = cells[2].get_text(strip=True).replace(",", "")
                close = float(close_text) if close_text else 0.0
                result.append({"code": code, "name": name, "close": close, "volume": 0})
            except (IndexError, ValueError):
                continue
        return result[:40]
    except Exception:
        return []


def _is_etf(code: str) -> bool:
    """ETF 代號通常以 0 開頭（如 00878、00940）。"""
    return code.startswith("0")


def _fetch_all_stock_names() -> dict[str, str]:
    """
    從 TWSE + TPEx openAPI 取得完整的代號→名稱對照表。
    失敗時回傳空 dict（後續 fallback 到 TW_STOCK_NAMES）。
    """
    names: dict[str, str] = {}
    # 上市
    try:
        r = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            headers=_HEADERS, timeout=10,
        )
        for item in r.json():
            code = str(item.get("Code", "")).strip()
            name = str(item.get("Name", "")).strip()
            if code and name:
                names[code] = name
    except Exception:
        pass
    # 上櫃
    try:
        r = requests.get(
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
            headers=_HEADERS, timeout=10,
        )
        for item in r.json():
            code = str(item.get("SecuritiesCompanyCode", "")).strip()
            name = str(item.get("CompanyName", "")).strip()
            if code and name:
                names[code] = name
    except Exception:
        pass
    return names


def _get_volume_candidates() -> list[dict]:
    """合併 TWSE + TPEx 量排，無資料時 fallback 到 Yahoo 奇摩。ETF 一律排除。"""
    listed = _twse_top20()
    otc    = _tpex_top20()
    combined = listed + otc
    if not combined:
        combined = _yahoo_top_active_fallback()
    # 去重 + 過濾 ETF
    seen, out = set(), []
    for s in combined:
        if s["code"] not in seen and not _is_etf(s["code"]):
            seen.add(s["code"])
            out.append(s)
    return out


def _cnyes_hot_codes(all_news: list[dict], min_mentions: int = 2) -> dict[str, str]:
    """
    從 CnYes 批次新聞中統計被標記最多次的台股代號。
    回傳 {code: "鉅亨 x3"}
    """
    counter: Counter = Counter()
    for item in all_news:
        stocks = item.get("stock") or []
        for s in stocks:
            counter[str(s)] += 1
    return {
        code: f"鉅亨 x{cnt}"
        for code, cnt in counter.items()
        if cnt >= min_mentions and re.match(r"^\d{4,5}$", code)  # 只保留台股代號，過濾 US-NVDA 等
    }


def _bnext_codes_via_gemini() -> dict[str, str]:
    """
    爬取數位時代首頁標題，送 Gemini 批次提取台股代號。
    回傳 {code: reason}；Gemini 不可用則回傳空 dict。
    """
    # 1. 爬標題
    titles: list[str] = []
    try:
        r = requests.get("https://www.bnext.com.tw/", headers=_HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup.select("h2, h3, h1"):
            text = tag.get_text(strip=True)
            if text and len(text) > 5:
                titles.append(text)
    except Exception:
        pass
    if not titles:
        return {}

    # 2. Gemini 批次提取
    prompt = (
        "以下是科技財經網站的新聞標題列表。\n"
        "請找出所有明確提及台灣上市/上櫃股票代號（4-5位數字）的標題，"
        "每行輸出格式為：代號|簡短原因（不超過20字）\n"
        "若無任何台股代號則輸出：無\n\n"
        + "\n".join(f"- {t}" for t in titles[:50])
    )
    text = _gemini_call(prompt)
    if not text:
        return {}
    result: dict[str, str] = {}
    for line in text.strip().splitlines():
        m = re.match(r"(\d{4,5})\s*[|｜]\s*(.+)", line.strip())
        if m:
            result[m.group(1)] = f"數位時代：{m.group(2).strip()}"
    return result


# ═══════════════════════════════════════════════════════════
#  Stage 2 — Technical Filter
# ═══════════════════════════════════════════════════════════

def _fetch_ohlcv(code: str) -> tuple[pd.DataFrame | None, str]:
    """
    嘗試 .TW 再 fallback .TWO，回傳 (DataFrame, 使用的後綴)。
    dropna 確保休市/假日時自動退回最後一個有效交易日。
    """
    for suffix in [".TW", ".TWO"]:
        try:
            df = yf.download(
                code + suffix, period="90d",   # 需 60+ 交易日以計算 MA60
                auto_adjust=True, progress=False, multi_level_index=False,
            )
            df = df.dropna(subset=["Close"])   # 去除無收盤價的列（休市日）
            if len(df) >= 10:
                return df, suffix
        except Exception:
            continue
    return None, ""


def _tech_filter(code: str) -> dict | None:
    """
    回傳技術指標 dict；不符合條件回傳 None。
    """
    df, suffix = _fetch_ohlcv(code)
    if df is None:
        return None

    close  = df["Close"].values.flatten()
    volume = df["Volume"].values.flatten()

    if len(close) < 20 or len(volume) < 10:
        return None

    ma5  = close[-5:].mean()
    ma20 = close[-20:].mean()
    ma60 = close[-60:].mean() if len(close) >= 60 else None

    # ① 短線：站上 5MA
    if close[-1] < ma5:
        return None

    # ② 長線保護短線：站上 20MA 或 60MA（至少一個），過濾空頭架構反彈
    above_ma20 = bool(close[-1] >= ma20)
    above_ma60 = bool(close[-1] >= ma60) if ma60 is not None else False
    if not above_ma20 and not above_ma60:
        return None

    vol_avg  = volume[-10:].mean()
    last_vol = volume[-1]

    # ③ 爆量
    if vol_avg == 0 or last_vol < vol_avg * 1.1:
        return None

    last_close = float(close[-1])
    if last_close != last_close:  # nan check
        return None

    # 乖離率 vs 20MA（傳給 Gemini 作為末升段風險判斷依據）
    bias_20ma = (last_close - float(ma20)) / float(ma20) * 100

    # RSI-14
    rsi = None
    if len(close) >= 15:
        deltas   = np.diff(close.astype(float))
        gains    = np.where(deltas > 0, deltas, 0.0)
        losses   = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = gains[-14:].mean()
        avg_loss = losses[-14:].mean()
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs  = avg_gain / avg_loss
            rsi = round(100 - (100 / (1 + rs)), 1)

    pct_5ma = (last_close - float(ma5)) / float(ma5) * 100
    vol_r   = float(last_vol / vol_avg)

    # 篩選條件摘要（顯示於報告卡片）
    reasons = []
    reasons.append(f"價站5MA ({pct_5ma:+.1f}%)")
    if above_ma20:
        reasons.append(f"站上20MA ({bias_20ma:+.1f}%)")
    elif above_ma60:
        reasons.append("站上60MA（未過20MA）")
    reasons.append(f"量比 {vol_r:.1f}x")
    if rsi is not None:
        rsi_tag = "超買" if rsi > 70 else ("超賣" if rsi < 30 else "")
        reasons.append(f"RSI {rsi:.0f}" + (f" ⚠️{rsi_tag}" if rsi_tag else ""))

    return {
        "last_close":     last_close,
        "ma5":            float(ma5),
        "ma20":           float(ma20),
        "ma60":           float(ma60) if ma60 is not None else None,
        "pct_vs_5ma":     pct_5ma,
        "bias_20ma":      bias_20ma,
        "above_ma20":     above_ma20,
        "above_ma60":     above_ma60,
        "vol_ratio":      vol_r,
        "last_vol":       float(last_vol),    # 原始量（股），供週轉率計算
        "suffix":         suffix,
        "rsi":            rsi,
        "filter_reasons": reasons,
    }


# ═══════════════════════════════════════════════════════════
#  Stage 3 — Global Verification (Gemini 動態判斷)
# ═══════════════════════════════════════════════════════════

def _last_trading_date(offset_days: int = 0) -> str:
    """
    回傳最近交易日的 YYYYMMDD 字串（跳過週末）。
    offset_days=1 時往前推一個交易日，依此類推。
    """
    d = datetime.now()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    # 再往前推 offset_days 個交易日
    for _ in range(offset_days):
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _fetch_institutional_all() -> dict[str, dict]:
    """
    TWSE 三大法人今日買賣超（全市場一次抓）。
    若當日資料尚未更新，自動往前找最多 3 個交易日。
    回傳 {code: {foreign, trust, dealer, total}}，單位：張。
    """
    result: dict[str, dict] = {}
    rows = []
    for offset in range(4):  # 0=今日, 1=昨日, 2=前天, 3=大前天
        date_str = _last_trading_date(offset)
        try:
            r = requests.get(
                f"https://www.twse.com.tw/rwd/zh/fund/T86"
                f"?response=json&date={date_str}&selectType=ALLBUT0999",
                headers=_HEADERS, timeout=12,
            )
            rows = r.json().get("data", [])
            if rows:
                print(f"    T86 使用日期：{date_str}（{len(rows)} 筆）")
                break
        except Exception:
            pass
    for row in rows:
        try:
            code = str(row[0]).strip()
            def _int(s):
                v = str(s).replace(",", "").replace("+", "").replace(" ", "")
                return int(v) if v and v != "-" else 0
            # T86 欄位（單位：股，除以 1000 轉換成張）
            # 0:代號 1:名稱
            # 2:外陸資買進 3:外陸資賣出 4:外陸資買賣超
            # 5:外資自營買進 6:外資自營賣出 7:外資自營買賣超
            # 8:投信買進 9:投信賣出 10:投信買賣超
            # 11:自營商買賣超 12:自營買進(自行) 13:自營賣出(自行) 14:自營買賣超(自行)
            # ... 最後一欄：三大法人買賣超
            foreign = _int(row[4])
            trust   = _int(row[10])
            dealer  = _int(row[14]) if len(row) > 14 else 0
            total   = foreign + trust + dealer
            result[code] = {
                "foreign": foreign // 1000,  # 轉張
                "trust":   trust   // 1000,
                "dealer":  dealer  // 1000,
                "total":   total   // 1000,
            }
        except Exception:
            continue
    return result


def _fetch_margin_all() -> dict[str, dict]:
    """
    TWSE 融資融券餘額（全市場）。
    若當日資料尚未更新，自動往前找最多 3 個交易日。
    回傳 {code: {margin_balance, margin_chg, short_balance, short_chg}}，單位：張。

    MI_MARGN tables[1] 欄位順序（實際驗證）：
    0:代號 1:名稱
    融資→ 2:買進 3:賣出 4:現金償還 5:前日餘額 6:今日餘額 7:次一限額
    融券→ 8:買進 9:賣出 10:現券償還 11:前日餘額 12:今日餘額 13:次一限額
    14:資券互抵 15:註記
    """
    result: dict[str, dict] = {}
    rows = []
    for offset in range(4):
        date_str = _last_trading_date(offset)
        try:
            r = requests.get(
                f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
                f"?response=json&date={date_str}&selectType=ALL",
                headers=_HEADERS, timeout=12,
            )
            j = r.json()
            # 資料在 tables[1]（第二張表，第一張是彙總）
            tables = j.get("tables", [])
            if len(tables) >= 2:
                rows = tables[1].get("data", [])
            if rows:
                print(f"    MI_MARGN 使用日期：{date_str}（{len(rows)} 筆）")
                break
        except Exception:
            pass
    for row in rows:
        try:
            code = str(row[0]).strip()
            def _int(s):
                return int(str(s).replace(",", "").replace("+", "").replace(" ", "") or 0)
            margin_today = _int(row[6])
            margin_prev  = _int(row[5])
            short_today  = _int(row[12]) if len(row) > 12 else 0
            short_prev   = _int(row[11]) if len(row) > 11 else 0
            result[code] = {
                "margin_balance": margin_today,
                "margin_chg":     margin_today - margin_prev,
                "short_balance":  short_today,
                "short_chg":      short_today - short_prev,
            }
        except Exception:
            continue
    return result


def _fetch_concept_tags(code: str, suffix: str) -> list[str]:
    """
    從 Yahoo Finance 台灣股票頁面抓「相關概念股」標籤。
    回傳 ["功率半導體", "AI伺服器", "低軌衛星", ...] 最多 6 個。
    """
    url = f"https://tw.stock.yahoo.com/quote/{code}{suffix}"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        tags: list[str] = []
        # 概念股連結格式：/class-quote?category=XXX&categoryLabel=概念股
        for a in soup.find_all("a", href=re.compile(r"/class-quote\?category=")):
            href = a.get("href", "")
            if "categoryLabel=%E6%A6%82%E5%BF%B5%E8%82%A1" in href:  # 概念股 URL encoded
                text = a.get_text(strip=True)
                if text and text not in tags:
                    tags.append(text)
        return tags[:6]
    except Exception:
        return []


_proxy_cache: dict[str, float] = {}  # 每次執行只抓一次


def _proxy_pct(symbol: str) -> float | None:
    """取代理指標最新一個交易日收盤漲跌幅，快取避免重複呼叫。"""
    if symbol in _proxy_cache:
        return _proxy_cache[symbol]
    try:
        df = yf.download(symbol, period="5d", auto_adjust=True,
                         progress=False, multi_level_index=False)
        if len(df) < 2:
            return None
        closes = df["Close"].dropna().values.flatten()
        if len(closes) < 2:
            return None
        pct = (closes[-1] - closes[-2]) / closes[-2] * 100
        _proxy_cache[symbol] = float(pct)
        return float(pct)
    except Exception:
        return None


def _gemini_suggest_proxies(passing: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """
    一次 Gemini 批次呼叫，判斷每支台股的全球供應鏈連動指標。
    回傳 {台股代號: [("NVDA", "AI晶片主要客戶"), ("4062.T", "ABF載板同業")]}
    判斷不出來的回傳空 list。
    """
    if not passing:
        return {}

    lines = []
    for s in passing:
        code   = s["code"]
        name   = s.get("name", "")
        titles = " / ".join(
            n["title"] for n in s.get("news", []) if n.get("title")
        ) or "（無新聞）"
        lines.append(f"{code} {name}：{titles}")

    prompt = (
        "以下是台灣上市櫃股票清單，每行格式為「代號 名稱：近期新聞標題」。\n"
        "請根據每支股票的主要業務和新聞內容，判斷哪 1~2 支美股或日股代號\n"
        "最能作為它的「全球供應鏈連動指標」，並說明關係（5字以內）。\n\n"
        "輸出規則：\n"
        "- 每行格式：台股代號|股票代號:關係,股票代號:關係\n"
        "- 關係說明範例：主要客戶、ABF載板、封裝材料、晶圓代工、同業龍頭\n"
        "- 日股加 .T 後綴（例如 4062.T）\n"
        "- 若真的判斷不出連動關係，該行寫：台股代號|\n"
        "- 不要輸出任何說明文字，只輸出對照表\n\n"
        "股票清單：\n" + "\n".join(lines)
    )

    text = _gemini_call(prompt)
    if not text:
        return {}
    try:
        result: dict[str, list[tuple[str, str]]] = {}
        for line in text.strip().splitlines():
            m = re.match(r"(\d{4,5})\s*[|｜]\s*(.*)", line.strip())
            if not m:
                continue
            tw_code = m.group(1)
            pairs: list[tuple[str, str]] = []
            for item in m.group(2).split(","):
                item = item.strip()
                if not item:
                    continue
                if ":" in item or "：" in item:
                    parts = re.split(r"[:：]", item, 1)
                    sym  = parts[0].strip()
                    rel  = parts[1].strip() if len(parts) > 1 else ""
                else:
                    sym, rel = item, ""
                # Gemini 偶爾會在美股代號前加 "US-"，例如 "US-NVDA" → "NVDA"
                sym = re.sub(r"^US[-\s]", "", sym, flags=re.IGNORECASE).strip()
                if sym:
                    pairs.append((sym, rel))
            result[tw_code] = pairs
        return result
    except Exception:
        return {}


def _build_global_signals(passing: list[dict]) -> dict[str, str]:
    """
    批次取得所有通過篩選股票的全球信號字串。
    回傳 {台股代號: "全球相關股票：NVDA(主要客戶) +3.2%, 4062.T(ABF載板) +1.8%"} 或 ""
    """
    proxy_map = _gemini_suggest_proxies(passing)

    # 收集所有需要查詢的代理指標符號，一次性抓取
    all_symbols: set[str] = set()
    for pairs in proxy_map.values():
        all_symbols.update(sym for sym, _ in pairs)
    for sym in all_symbols:
        _proxy_pct(sym)

    signals: dict[str, str] = {}
    for s in passing:
        code  = s["code"]
        pairs = proxy_map.get(code, [])
        if not pairs:
            signals[code] = ""
            continue

        parts: list[str] = []
        for sym, rel in pairs:
            pct = _proxy_cache.get(sym)
            if pct is None:
                continue
            sign   = "+" if pct >= 0 else ""
            rel_tag = f"({rel})" if rel else ""
            parts.append(f"{sym}{rel_tag} {sign}{pct:.1f}%")

        signals[code] = f"全球相關股票：{', '.join(parts)}" if parts else ""

    return signals


# ═══════════════════════════════════════════════════════════
#  Stage 2.5 — Fundamentals（財報指標）
# ═══════════════════════════════════════════════════════════

def _fetch_financials(code: str, suffix: str) -> dict:
    """
    取得個股近 3 年財報：營收、EPS、本益比區間。
    盡量抓，缺資料欄位留空，不 raise。
    """
    out = {
        "revenue":        [],    # [(label, 億), ...]  年度舊→新，最後一筆是最新季
        "eps":            [],    # [(label, eps), ...]
        "gross_margin":   [],    # [(label, %), ...]  年度 + 最新季
        "inventory_days": [],    # [(label, days), ...]  年度 + 最新季
        "latest_quarter": "",    # 例如 "2026Q1"
        "trailing_pe":       None,
        "forward_pe":        None,
        "pe_52w_high":       None,
        "pe_52w_low":        None,
        "shares_outstanding": None,  # 在外流通股數（股），用於計算週轉率
    }
    try:
        tkr  = yf.Ticker(code + suffix)
        info = tkr.info or {}

        # ── 年度財報 ───────────────────────────────────────
        try:
            stmt = tkr.income_stmt          # DataFrame: rows=metrics, cols=dates
            if stmt is not None and not stmt.empty:
                cols = stmt.columns[:3]     # 最近 3 年（最新在最前）

                # 營收
                for key in ("Total Revenue", "Operating Revenue"):
                    if key in stmt.index:
                        for col in cols:
                            val = stmt.loc[key, col]
                            if pd.notna(val) and val > 0:
                                out["revenue"].append((col.year, val / 1e8))
                        break

                # EPS —— 優先用財報直接欄位，否則用 NetIncome/shares
                for key in ("Basic EPS", "Diluted EPS"):
                    if key in stmt.index:
                        for col in cols:
                            val = stmt.loc[key, col]
                            if pd.notna(val):
                                out["eps"].append((col.year, float(val)))
                        break

                if not out["eps"] and "Net Income" in stmt.index:
                    shares = (info.get("sharesOutstanding")
                              or info.get("impliedSharesOutstanding"))
                    if shares:
                        for col in cols:
                            ni = stmt.loc["Net Income", col]
                            if pd.notna(ni):
                                out["eps"].append((col.year, float(ni) / shares))

                # 毛利率
                gp_key = next((k for k in ("Gross Profit",) if k in stmt.index), None)
                rev_key = next((k for k in ("Total Revenue", "Operating Revenue") if k in stmt.index), None)
                if gp_key and rev_key:
                    for col in cols:
                        gp  = stmt.loc[gp_key,  col]
                        rev = stmt.loc[rev_key, col]
                        if pd.notna(gp) and pd.notna(rev) and rev > 0:
                            out["gross_margin"].append((col.year, round(gp / rev * 100, 1)))

                # 資料舊→新排序
                out["revenue"].sort(key=lambda x: x[0])
                out["eps"].sort(key=lambda x: x[0])
                out["gross_margin"].sort(key=lambda x: x[0])

                # 庫存天數（年度）= 存貨 / COGS × 365
                try:
                    bs = tkr.balance_sheet
                    cogs_key = next((k for k in ("Cost Of Revenue", "Cost Of Goods Sold")
                                     if k in stmt.index), None)
                    inv_key  = next((k for k in ("Inventory",) if bs is not None and k in bs.index), None)
                    if cogs_key and inv_key and bs is not None:
                        bs_cols = bs.columns
                        for col in cols:
                            # 找最近的 balance_sheet 欄位（年度對齊）
                            bs_col = next((c for c in bs_cols if c.year == col.year), None)
                            if bs_col is None:
                                continue
                            inv  = bs.loc[inv_key, bs_col]
                            cogs = stmt.loc[cogs_key, col]
                            if pd.notna(inv) and pd.notna(cogs) and cogs > 0:
                                out["inventory_days"].append(
                                    (col.year, round(float(inv) / float(cogs) * 365, 0))
                                )
                        out["inventory_days"].sort(key=lambda x: x[0])
                except Exception:
                    pass

        except Exception:
            pass

        # ── 最新季度財報 ──────────────────────────────────
        try:
            qstmt = tkr.quarterly_income_stmt
            if qstmt is not None and not qstmt.empty:
                # 最新一季：第一欄
                qcol = qstmt.columns[0]
                qyear = qcol.year
                qmonth = qcol.month
                # 月份 → 季度（以財報公告月對應）
                qnum = (qmonth - 1) // 3 + 1
                qlabel = f"{qyear}Q{qnum}"
                out["latest_quarter"] = qlabel

                # 季度營收
                q_rev = None
                for key in ("Total Revenue", "Operating Revenue"):
                    if key in qstmt.index:
                        val = qstmt.loc[key, qcol]
                        if pd.notna(val) and val > 0:
                            q_rev = val / 1e8
                        break
                if q_rev is not None:
                    out["revenue"].append((qlabel, q_rev))

                # 季度 EPS
                q_eps = None
                for key in ("Basic EPS", "Diluted EPS"):
                    if key in qstmt.index:
                        val = qstmt.loc[key, qcol]
                        if pd.notna(val):
                            q_eps = float(val)
                        break
                if q_eps is None and "Net Income" in qstmt.index:
                    shares = (info.get("sharesOutstanding")
                              or info.get("impliedSharesOutstanding"))
                    ni = qstmt.loc["Net Income", qcol]
                    if shares and pd.notna(ni):
                        q_eps = float(ni) / shares
                if q_eps is not None:
                    out["eps"].append((qlabel, q_eps))

                # 季度毛利率
                gp_key  = next((k for k in ("Gross Profit",) if k in qstmt.index), None)
                rev_key = next((k for k in ("Total Revenue", "Operating Revenue")
                                if k in qstmt.index), None)
                if gp_key and rev_key:
                    gp  = qstmt.loc[gp_key,  qcol]
                    rev = qstmt.loc[rev_key, qcol]
                    if pd.notna(gp) and pd.notna(rev) and rev > 0:
                        out["gross_margin"].append((qlabel, round(float(gp) / float(rev) * 100, 1)))

        except Exception:
            pass

        # ── 本益比 ────────────────────────────────────────
        # 在外流通股數
        out["shares_outstanding"] = (
            info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        )

        price         = info.get("currentPrice") or info.get("regularMarketPrice")
        trailing_eps  = info.get("trailingEps")
        out["forward_pe"] = info.get("forwardPE")

        if trailing_eps and trailing_eps > 0 and price:
            out["trailing_pe"] = round(price / trailing_eps, 1)
        elif info.get("trailingPE"):
            out["trailing_pe"] = round(info["trailingPE"], 1)

        # 52 週 P/E 區間（用 trailing EPS 換算）
        if trailing_eps and trailing_eps > 0:
            hi = info.get("fiftyTwoWeekHigh")
            lo = info.get("fiftyTwoWeekLow")
            if hi:
                out["pe_52w_high"] = round(hi / trailing_eps, 1)
            if lo:
                out["pe_52w_low"]  = round(lo / trailing_eps, 1)

        # ── 合理股價 & 預測股價 ────────────────────────────
        #   合理股價 = 52W P/E 中位數 × trailing EPS
        #   預測股價 = 分析師平均目標價（優先），否則用 52W PE中位 × forward EPS
        t_mean = info.get("targetMeanPrice")
        t_high = info.get("targetHighPrice")
        t_low  = info.get("targetLowPrice")
        out["target_mean"] = round(t_mean, 1) if t_mean else None
        out["target_high"] = round(t_high, 1) if t_high else None
        out["target_low"]  = round(t_low,  1) if t_low  else None

        pe_hi = out.get("pe_52w_high")
        pe_lo = out.get("pe_52w_low")
        fwd_eps = info.get("forwardEps")

        if pe_hi and pe_lo and trailing_eps and trailing_eps > 0:
            pe_mid = (pe_hi + pe_lo) / 2
            out["reasonable_price"] = round(pe_mid * trailing_eps, 1)
        else:
            out["reasonable_price"] = None

        if out["target_mean"]:
            out["predicted_price"] = out["target_mean"]
        elif fwd_eps and fwd_eps > 0 and pe_hi and pe_lo:
            pe_mid = (pe_hi + pe_lo) / 2
            out["predicted_price"] = round(pe_mid * fwd_eps, 1)
        else:
            out["predicted_price"] = None

    except Exception:
        pass

    return out


def _gemini_batch_verdict(passing: list[dict]) -> dict[str, dict]:
    """
    把所有通過篩選的股票資料打包成一個 prompt，讓 Gemini 給出
    買賣評級與一句話原因。
    回傳 {code: {"rating": "買入", "reason": "..."}}
    評級: 積極買入 / 買入 / 觀望 / 減碼 / 避開
    """
    if not passing:
        return {}

    lines = []
    for s in passing:
        code  = s["code"]
        name  = s.get("name", "")
        tech  = s["tech"]
        fin   = s.get("financials", {})
        inst  = s.get("institutional", {})
        mgn   = s.get("margin_data", {})

        # RSI
        rsi = tech.get("rsi")
        rsi_str = f"RSI {rsi:.0f}" if rsi else "RSI N/A"

        # 位階：乖離率 vs 20MA
        bias = tech.get("bias_20ma", 0)
        bias_str = f"乖離20MA {bias:+.1f}%"

        # 週轉率（過高代表當沖過多，隔日沖壓力）
        turnover = s.get("turnover_rate")
        turn_str = f"週轉率 {turnover:.1f}%" if turnover is not None else "週轉率 N/A"

        # 土洋合買
        dual_buy = s.get("dual_buy", False)
        dual_str = "⚡土洋合買（外資+投信同買）" if dual_buy else ""

        # 三大法人
        foreign = inst.get("foreign", 0)
        trust   = inst.get("trust", 0)
        total   = inst.get("total", 0)
        inst_str = f"外資{'+' if foreign >= 0 else ''}{foreign}張 投信{'+' if trust >= 0 else ''}{trust}張 合計{'+' if total >= 0 else ''}{total}張"

        # 融資
        m_chg = mgn.get("margin_chg", 0)
        s_chg = mgn.get("short_chg", 0)
        mgn_str = f"融資增減{'+' if m_chg >= 0 else ''}{m_chg}張 融券增減{'+' if s_chg >= 0 else ''}{s_chg}張"

        # 毛利率趨勢
        gm = fin.get("gross_margin", [])
        gm_str = " → ".join(f"{v:.0f}%" for _, v in gm) if gm else "N/A"

        # 庫存天數
        inv = fin.get("inventory_days", [])
        inv_str = " → ".join(f"{int(v)}天" for _, v in inv) if inv else "N/A"

        # 估值
        trail_pe = fin.get("trailing_pe")
        pe_hi    = fin.get("pe_52w_high")
        pe_lo    = fin.get("pe_52w_low")
        pe_str   = f"P/E {trail_pe:.0f}x (52W {pe_lo:.0f}~{pe_hi:.0f}x)" if trail_pe and pe_hi and pe_lo else "P/E N/A"

        # 全球信號
        global_ = s.get("global_signal", "")

        # 最新新聞標題
        news_titles = " / ".join(n["title"] for n in s.get("news", [])[:2] if n.get("title"))

        lines.append(
            f"{code} {name}：{rsi_str}｜量比{tech['vol_ratio']:.1f}x｜"
            f"{bias_str}｜{turn_str}｜{dual_str}｜"
            f"{inst_str}｜{mgn_str}｜"
            f"毛利率 {gm_str}｜庫存天數 {inv_str}｜"
            f"{pe_str}｜{global_}｜新聞：{news_titles or '無'}"
        )

    prompt = (
        "你是一位專業的台股選股分析師。以下是今日通過技術篩選的台股清單，"
        "每行包含技術面、位階、籌碼面、法人動向、基本面與新聞資訊。\n\n"
        "請根據所有資料，對每支股票給出：\n"
        "① 投資評級（五選一）\n"
        "② 一句話原因（不超過 40 字）\n"
        "③ 當前最重要的投資題材標籤（2～4 個，每個 2～6 字，用逗號分隔）\n\n"
        "評級只能選以下五種之一：積極買入 / 買入 / 觀望 / 減碼 / 避開\n\n"
        "題材標籤範例：先進封裝、AI伺服器、CoWoS需求、2nm量產、HBM記憶體、"
        "ABF載板、COWOS、電動車、低軌衛星、機器人概念、重電基建、高殖利率\n\n"
        "【評分規則（必須遵守）】\n"
        "1. 若乖離20MA > +15% 且量比 > 2x → 末升段噴出風險，最高給「觀望」\n"
        "2. 若週轉率 > 15% → 當沖過多，隔日沖壓力大，降一個評級\n"
        "3. 若「土洋合買」（外資+投信同步買超）→ 基本面支撐，可拉高一個評級\n"
        "4. 若外資+投信合計賣超 < -500張 → 法人出貨警訊，最高給「觀望」\n"
        "5. 融資大增（>+500張）且外資同步賣超 → 散戶接刀風險，降評\n\n"
        "輸出格式（每行一支，不要輸出其他文字）：\n"
        "代號|評級|原因|題材1,題材2,題材3\n\n"
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
        # 嘗試四欄格式（含題材）
        m4 = re.match(r"(\d{4,5})\s*[|｜]\s*(.+?)\s*[|｜]\s*(.+?)\s*[|｜]\s*(.+)", line)
        if m4:
            themes = [t.strip() for t in re.split(r"[,，、]", m4.group(4)) if t.strip()]
            result[m4.group(1)] = {
                "rating": m4.group(2).strip(),
                "reason": m4.group(3).strip(),
                "themes": themes[:4],
            }
            continue
        # fallback：三欄格式（Gemini 忽略了題材欄）
        m3 = re.match(r"(\d{4,5})\s*[|｜]\s*(.+?)\s*[|｜]\s*(.+)", line)
        if m3:
            result[m3.group(1)] = {
                "rating": m3.group(2).strip(),
                "reason": m3.group(3).strip(),
                "themes": [],
            }
    return result


# ═══════════════════════════════════════════════════════════
#  歷史記錄 — 儲存 / 昨日回顧
# ═══════════════════════════════════════════════════════════

def _save_scout_history(passing: list[dict]) -> None:
    """
    將本次通過篩選且有評級的股票儲存到 scout_history.json。
    每日覆寫，保留最近 14 個交易日。
    """
    today = datetime.now().strftime("%Y%m%d")
    try:
        with open(_HISTORY_FILE, encoding="utf-8") as f:
            history: dict = json.load(f)
    except Exception:
        history = {}

    history[today] = [
        {
            "code":   s["code"],
            "name":   s.get("name", ""),
            "rating": s.get("verdict", {}).get("rating", ""),
            "reason": s.get("verdict", {}).get("reason", ""),
            "close":  s["tech"]["last_close"],
            "suffix": s["tech"]["suffix"],
        }
        for s in passing
        if s.get("verdict", {}).get("rating")
    ]

    # 只保留最近 22 個交易日（約 1 個月），供首頁「近期熱門股」統計使用
    for old_date in sorted(history.keys(), reverse=True)[22:]:
        del history[old_date]

    try:
        with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        print(f"  歷史記錄已儲存：{today}（{len(history[today])} 筆）")
    except Exception as e:
        print(f"  歷史記錄儲存失敗：{e}")


def _build_top_picks_html() -> str:
    """
    統計 scout_history.json 裡近期（最多保留 1 個月交易日）被技術篩選選中
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
            code = s.get("code", "")
            if not code:
                continue
            counts[code] = counts.get(code, 0) + 1
            latest[code] = s  # 日期由舊到新遍歷，最後寫入的就是最新一筆

    top5 = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    if not top5:
        return ""

    items_html = ""
    for rank, (code, cnt) in enumerate(top5, 1):
        info = latest.get(code, {})
        name = info.get("name", "")
        items_html += f"""
        <div class="top-item">
          <div class="top-rank">#{rank}</div>
          <div class="top-code">{code} {name}</div>
          <div class="top-count">{cnt}<span class="unit"> 次</span></div>
        </div>"""

    n_days = len(history)
    return f"""
  <div class="top-wrap">
    <div class="top-hdr">
      <h2>🏆 近期熱門股</h2>
      <p>近 {n_days} 個交易日通過技術篩選次數最多的前 5 檔個股</p>
    </div>
    <div class="top-body">{items_html}</div>
  </div>"""


def _build_review_html() -> str:
    """
    讀取 3 個交易日前被評為「積極買入」或「買入」的股票，查詢今日收盤，計算報酬率。
    回傳 HTML 字串；無資料時回傳空字串。
    """
    try:
        with open(_HISTORY_FILE, encoding="utf-8") as f:
            history: dict = json.load(f)
    except Exception:
        return ""

    target_date = _last_trading_date(3)  # 往前推 3 個交易日
    stocks = history.get(target_date, [])
    tracked = [s for s in stocks if s.get("rating") in ("積極買入", "買入")]

    if not tracked:
        return ""

    date_display = f"{target_date[:4]}/{target_date[4:6]}/{target_date[6:]}"
    rows_html = ""

    for s in tracked:
        code    = s["code"]
        name    = s.get("name", "")
        old_px  = s.get("close", 0)
        suffix  = s.get("suffix", ".TW")
        reason  = s.get("reason", "")

        # 查今日收盤
        cur_px = None
        try:
            df = yf.download(code + suffix, period="5d",
                             auto_adjust=True, progress=False, multi_level_index=False)
            df = df.dropna(subset=["Close"])
            if len(df) >= 1:
                cur_px = float(df["Close"].values.flatten()[-1])
        except Exception:
            pass

        if cur_px and old_px and old_px > 0:
            ret     = (cur_px - old_px) / old_px * 100
            ret_str = f"{'+'if ret>=0 else ''}{ret:.1f}%"
            # 台股：紅漲綠跌
            ret_color = "#c62828" if ret >= 0 else "#2e7d32"
            emoji     = "🔴" if ret >= 0 else "🟢"
        else:
            ret_str   = "N/A"
            ret_color = "#888"
            emoji     = "⚪"

        cur_px_str  = f"${cur_px:,.1f}" if cur_px is not None else "N/A"
        cnyes_url   = f"https://www.cnyes.com/twstock/{code}"

        rows_html += f"""
        <tr>
          <td><a href="{cnyes_url}" style="color:#1a237e;text-decoration:none">
              {code} {name}</a></td>
          <td style="text-align:right;color:#555">${old_px:,.1f}</td>
          <td style="text-align:right;color:#555">{cur_px_str}</td>
          <td style="text-align:right;font-weight:bold;color:{ret_color}">{emoji} {ret_str}</td>
          <td style="color:#666;font-size:12px">{reason}</td>
        </tr>"""

    if not rows_html:
        return ""

    return f"""
  <div class="review-wrap">
    <div class="review-hdr">
      <h2>📋 動能延續追蹤</h2>
      <p>{date_display} 評為「積極買入 / 買入」— 3 個交易日後績效</p>
    </div>
    <div class="review-body">
      <table class="review-table">
        <thead>
          <tr>
            <th>股票</th>
            <th style="text-align:right">買入收盤價</th>
            <th style="text-align:right">今日收盤</th>
            <th style="text-align:right">報酬</th>
            <th>當時 AI 評語</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </div>"""


def _fetch_market_overview() -> dict:
    """
    取得今日大盤概況：
    - 加權指數：當日收盤、漲跌點、漲跌幅
    - 0050 今年以來（YTD）報酬率
    - 00631L 大盤狀態（強勢區 / 縮量破位 / 帶量跌破），以 20MA 為依據
    回傳 dict，取值失敗的欄位為 None / ""。
    """
    result: dict = {
        "twii_close":  None,   # 加權指數收盤
        "twii_chg":    None,   # 漲跌點
        "twii_pct":    None,   # 漲跌幅 %
        "ytd_0050":    None,   # 0050 YTD %
        "market_status":  "",  # 強勢區 / 縮量破位 / 帶量跌破
        "market_detail":  "",  # 說明文字
        "etf_close":   None,   # 00631L 收盤
        "etf_ma20":    None,   # 00631L MA20
    }

    # 1. 加權指數（^TWII）
    try:
        df = yf.download("^TWII", period="5d", auto_adjust=True,
                         progress=False, multi_level_index=False)
        closes = df["Close"].dropna().values
        if len(closes) >= 2:
            result["twii_close"] = float(closes[-1])
            result["twii_chg"]   = float(closes[-1] - closes[-2])
            result["twii_pct"]   = float((closes[-1] - closes[-2]) / closes[-2] * 100)
    except Exception:
        pass

    # 2. 0050 YTD（從年初第一個交易日至今）
    try:
        year_start = datetime(datetime.now().year, 1, 1).strftime("%Y-%m-%d")
        df = yf.download("0050.TW", start=year_start, auto_adjust=True,
                         progress=False, multi_level_index=False)
        closes = df["Close"].dropna().values
        if len(closes) >= 2:
            result["ytd_0050"] = float((closes[-1] - closes[0]) / closes[0] * 100)
    except Exception:
        pass

    # 3. 00631L 大盤狀態（20MA 生命線）
    try:
        df = yf.download("00631L.TW", period="60d", auto_adjust=True,
                         progress=False, multi_level_index=False)
        closes  = df["Close"].dropna().values
        volumes = df["Volume"].dropna().values
        if len(closes) >= 20:
            curr_p = float(closes[-1])
            ma20   = float(closes[-20:].mean())
            # 量比：今日量 / 近 5 日均量
            vol_ratio = float(volumes[-1] / volumes[-5:].mean()) if len(volumes) >= 5 else 1.0
            result["etf_close"] = curr_p
            result["etf_ma20"]  = ma20
            if curr_p > ma20:
                result["market_status"] = "強勢區"
                result["market_detail"] = f"多頭慣性，00631L 站上 20MA（{ma20:,.1f}）"
            else:
                bias = (curr_p - ma20) / ma20 * 100
                if vol_ratio < 0.8:
                    result["market_status"] = "縮量破位"
                    result["market_detail"] = (
                        f"跌破 20MA（{ma20:,.1f}），量比 {vol_ratio:.1f}x 萎縮，"
                        f"賣壓尚未出盡（{bias:+.1f}%）"
                    )
                else:
                    result["market_status"] = "帶量跌破"
                    result["market_detail"] = (
                        f"帶量跌破 20MA（{ma20:,.1f}），量比 {vol_ratio:.1f}x，"
                        f"系統性風險警戒（{bias:+.1f}%）"
                    )
    except Exception:
        pass

    return result


def _fetch_hot_topics() -> list[dict]:
    """
    從鉅亨網、數位時代、Yahoo財經、工商時報、經濟日報、Digitimes
    抓取今日財經熱門標題，送 Gemini 整理成 4~6 個話題。
    每個話題包含：短標題 + 約 80 字說明 + 最相關原文連結。
    回傳 [{"title": "...", "detail": "...", "url": "..."}, ...]
    """
    # 儲存 (label, title, url) 三元組，url 可為空字串
    articles: list[tuple[str, str, str]] = []

    def _rss_items(rss_url: str, label: str, max_n: int = 8) -> None:
        """從 RSS XML 同時提取 title + link，失敗靜默略過。"""
        try:
            r = requests.get(rss_url, headers=_HEADERS, timeout=10)
            # 逐 <item> 解析，避免跨 item 匹配錯位
            for item_xml in re.findall(r"<item>(.*?)</item>", r.text, re.DOTALL):
                t_m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                                item_xml, re.DOTALL)
                l_m = re.search(r"<link>\s*(https?://[^\s<]+)\s*</link>", item_xml)
                if not t_m:
                    continue
                t = t_m.group(1).strip()
                url = l_m.group(1).strip() if l_m else ""
                if t and 10 < len(t) < 120 and label.lower() not in t.lower():
                    articles.append((label, t, url))
                    if len([a for a in articles if a[0] == label]) >= max_n:
                        break
        except Exception:
            pass

    def _html_items(page_url: str, label: str, selectors: str,
                    max_n: int = 8) -> None:
        """從網頁抓標題，嘗試取父層 <a> href 作為連結。"""
        try:
            r = requests.get(page_url, headers=_HEADERS, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            count = 0
            for tag in soup.select(selectors):
                text = tag.get_text(strip=True)
                if not text or not (8 < len(text) < 100):
                    continue
                # 找最近的祖先 <a>
                a = tag.find_parent("a") or tag.find("a")
                href = a["href"] if a and a.get("href", "").startswith("http") else ""
                articles.append((label, text, href))
                count += 1
                if count >= max_n:
                    break
        except Exception:
            pass

    # 1. 鉅亨網 API（JSON，可直接取 newsId 構造連結）
    try:
        r = requests.get(
            "https://api.cnyes.com/media/api/v1/newslist/category/tw_stock"
            "?limit=20&page=1",
            headers=_HEADERS, timeout=10,
        )
        for item in r.json().get("items", {}).get("data", [])[:15]:
            t = item.get("title", "")
            news_id = item.get("newsId") or item.get("id") or ""
            url = f"https://news.cnyes.com/news/id/{news_id}" if news_id else ""
            if t:
                articles.append(("鉅亨", t, url))
    except Exception:
        pass

    # 2. 數位時代（網頁爬取）
    _html_items("https://www.bnext.com.tw/", "數位時代", "h1,h2,h3")

    # 3. Yahoo 財經 RSS
    _rss_items("https://tw.news.yahoo.com/rss/finance", "Yahoo財經")

    # 4. 工商時報 — Google News RSS 代理
    _rss_items(
        "https://news.google.com/rss/search"
        "?q=%E5%8F%B0%E8%82%A1+site%3Actee.com.tw"
        "&hl=zh-TW&gl=TW&ceid=TW%3Azh-Hant",
        "工商時報",
    )

    # 5. 經濟日報 — Google News RSS 代理
    _rss_items(
        "https://news.google.com/rss/search"
        "?q=%E5%8F%B0%E8%82%A1+site%3Amoney.udn.com"
        "&hl=zh-TW&gl=TW&ceid=TW%3Azh-Hant",
        "經濟日報",
    )

    # 6. Digitimes — Google News RSS 代理
    _rss_items(
        "https://news.google.com/rss/search"
        "?q=%E5%8F%B0%E8%82%A1+site%3Adigitimes.com.tw"
        "&hl=zh-TW&gl=TW&ceid=TW%3Azh-Hant",
        "Digitimes",
    )

    if len(articles) < 3:
        return []

    # 傳給 Gemini 的是「序號. [來源] 標題」，URL 不放進 prompt 節省 token
    articles = articles[:40]
    numbered = "\n".join(
        f"{i+1}. [{a[0]}] {a[1]}" for i, a in enumerate(articles)
    )

    prompt = (
        "以下是今日來自多個財經媒體的新聞標題。\n"
        "請整理出今日台股市場最重要的 4~6 個熱門討論話題。\n\n"
        "每行一個話題，格式（用 | 分隔，不要其他文字）：\n"
        "話題標題（15字以內）|說明（約80字，含背景、相關個股、關鍵數字）|最相關標題的序號\n\n"
        "新聞標題：\n" + numbered
    )
    # 熱門話題是最後一個 Gemini 呼叫，對 503 使用更長等待與更多重試
    text = _gemini_call(prompt, retries=5, delay=20)
    if not text:
        return []
    result: list[dict] = []
    for line in text.strip().splitlines():
        line = line.strip().lstrip("•·▪*-–— 123456789.")
        if not line:
            continue
        if "|" not in line and "｜" not in line:
            continue
        cols = re.split(r"[|｜]", line)
        title  = cols[0].strip() if len(cols) > 0 else ""
        detail = cols[1].strip() if len(cols) > 1 else ""
        idx_str = cols[2].strip() if len(cols) > 2 else ""
        # 從序號取 URL（Gemini 可能回傳 "3" 或 "第3條" 等，取第一個數字）
        url = ""
        idx_m = re.search(r"\d+", idx_str)
        if idx_m:
            idx = int(idx_m.group()) - 1  # 轉為 0-based
            if 0 <= idx < len(articles):
                url = articles[idx][2]    # (label, title, url)[2]
        if title and len(title) <= 30:
            result.append({"title": title, "detail": detail, "url": url})
    return result


def _pe_assessment(fin: dict) -> str:
    """
    根據當前 P/E 在 52 週區間的位置，給出一行估值評語。
    """
    cur  = fin.get("trailing_pe")
    hi   = fin.get("pe_52w_high")
    lo   = fin.get("pe_52w_low")
    fwd  = fin.get("forward_pe")

    if cur is None:
        return ""

    parts = []
    if hi and lo and hi > lo > 0:
        mid = (hi + lo) / 2
        if cur > hi * 0.90:
            parts.append(f"⚠️ 估值偏高（接近52W高點 {hi:.0f}x）")
        elif cur < lo * 1.10:
            parts.append(f"✅ 估值偏低（接近52W低點 {lo:.0f}x）")
        elif cur > mid:
            parts.append(f"📌 估值中高（52W 中位 {mid:.0f}x）")
        else:
            parts.append(f"📌 估值中低（52W 中位 {mid:.0f}x）")

    if fwd and cur and fwd > 0:
        growth_implied = (cur / fwd - 1) * 100
        if growth_implied > 5:
            parts.append(f"前瞻 {fwd:.1f}x（隱含獲利成長 +{growth_implied:.0f}%）")
        else:
            parts.append(f"前瞻 {fwd:.1f}x")

    return "  | ".join(parts) if parts else ""


# ═══════════════════════════════════════════════════════════
#  Stage 4 — Format HTML Report (for email)
# ═══════════════════════════════════════════════════════════

def _fmt_news_html(news_items: list[dict]) -> str:
    """把鉅亨新聞 list 轉成 HTML 片段（超連結標題 + 摘要）。"""
    if not news_items:
        return ""
    parts = []
    for n in news_items:
        title   = n.get("title", "")
        link    = n.get("link", "")
        summary = n.get("summary", "")
        time_   = n.get("time", "")
        time_tag = f'<span style="color:#888;font-size:12px">[{time_}]</span> ' if time_ else ""
        if link:
            title_html = (f'{time_tag}<a href="{link}" '
                          f'style="color:#1a0dab;text-decoration:none">{title}</a>')
        else:
            title_html = f"{time_tag}{title}"
        summary_html = ""
        if summary:
            bullets = [l.strip() for l in summary.splitlines() if l.strip()]
            summary_html = "".join(
                f'<div style="color:#555;font-size:13px;padding-left:12px">• {b}</div>'
                for b in bullets
            )
        parts.append(f'<div style="margin:4px 0">{title_html}{summary_html}</div>')
    return "\n".join(parts)


def _format_report_html(
    passing: list[dict],
    hot_topics: list[dict] | None = None,
    review_html: str = "",
    market_overview: dict | None = None,
) -> str:
    today = datetime.now().strftime("%Y/%m/%d")
    n     = len(passing)

    CSS = """
    body{font-family:'Helvetica Neue',Arial,sans-serif;background:#f4f4f4;margin:0;padding:20px}
    .wrap{max-width:720px;margin:0 auto;background:#fff;border-radius:8px;
          box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden}
    .hdr{padding:18px 24px 14px;border-bottom:1px solid #eee}
    .hdr h2{margin:0;font-size:20px;color:#1a237e}
    .hdr p{margin:4px 0 0;font-size:13px;color:#666}
    .card{border-left:4px solid #3f51b5;margin:16px;padding:12px 16px;
          background:#fafafa;border-radius:4px}
    .card h3{margin:0 0 6px;font-size:16px;color:#1a237e}
    .badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px;
           font-weight:bold;margin-right:4px}
    .up{background:#e8f5e9;color:#2e7d32}
    .dn{background:#ffebee;color:#c62828}
    .row{font-size:13px;margin:3px 0;color:#333}
    .label{color:#666;margin-right:4px}
    table.fin{border-collapse:collapse;font-size:13px;margin:6px 0}
    table.fin td{padding:2px 14px 2px 0;color:#444}
    table.fin td:first-child{color:#666;white-space:nowrap}
    .pe-bar{background:#e3f2fd;border-radius:4px;padding:6px 10px;
            font-size:13px;margin:4px 0}
    .price-box{background:#fff3e0;border-radius:4px;padding:6px 10px;
               font-size:13px;margin:4px 0}
    .verdict{font-size:13px;margin:4px 0}
    .news-section{margin-top:8px;padding-top:6px;border-top:1px solid #eee}
    .inst-box{background:#f3e5f5;border-radius:4px;padding:6px 10px;font-size:13px;margin:4px 0}
    .margin-box{background:#fce4ec;border-radius:4px;padding:6px 10px;font-size:13px;margin:4px 0}
    .gm-box{background:#e8f5e9;border-radius:4px;padding:6px 10px;font-size:13px;margin:4px 0}
    .verdict-box{border-radius:6px;padding:8px 12px;font-size:14px;font-weight:bold;margin:8px 0}
    .v-strong-buy{background:#1b5e20;color:#fff}
    .v-buy{background:#2e7d32;color:#fff}
    .v-hold{background:#e65100;color:#fff}
    .v-reduce{background:#b71c1c;color:#fff}
    .v-avoid{background:#37474f;color:#fff}
    .footer{text-align:center;font-size:11px;color:#aaa;padding:14px;
            border-top:1px solid #eee}
    .top-wrap{max-width:720px;margin:0 auto 16px;background:#fff;
              border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden}
    .top-hdr{padding:14px 20px 12px;border-bottom:1px solid #eee}
    .top-hdr h2{margin:0;font-size:18px;color:#6a1b9a}
    .top-hdr p{margin:3px 0 0;font-size:12px;color:#666}
    .top-body{display:flex;flex-wrap:wrap}
    .top-item{flex:1 1 20%;padding:14px 16px;border-right:1px solid #f0f0f0;
              min-width:120px;box-sizing:border-box;text-align:center}
    .top-item:last-child{border-right:none}
    .top-rank{font-size:11px;color:#888;font-weight:bold;margin-bottom:4px}
    .top-code{font-size:14px;font-weight:bold;color:#1a237e;margin-bottom:6px}
    .top-count{font-size:20px;font-weight:bold;color:#c62828}
    .top-count .unit{font-size:11px;color:#888;font-weight:normal;margin-left:2px}
    .mkt-wrap{max-width:720px;margin:0 auto 16px;background:#fff;
              border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden}
    .mkt-hdr{padding:14px 20px 12px;border-bottom:1px solid #eee}
    .mkt-hdr h2{margin:0;font-size:18px;color:#37474f}
    .mkt-hdr p{margin:3px 0 0;font-size:12px;color:#666}
    .mkt-body{display:flex;flex-wrap:wrap;gap:0;padding:0}
    .mkt-item{flex:1 1 33%;padding:14px 20px;border-right:1px solid #f0f0f0;
              border-bottom:1px solid #f0f0f0;min-width:180px;box-sizing:border-box}
    .mkt-item:last-child{border-right:none}
    .mkt-item-label{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
    .mkt-item-val{font-size:22px;font-weight:bold;margin-bottom:2px}
    .mkt-item-sub{font-size:12px;color:#666}
    .mkt-up{color:#c62828}
    .mkt-dn{color:#2e7d32}
    .mkt-neu{color:#555}
    .status-strong{display:inline-block;background:#e8f5e9;color:#1b5e20;
                   padding:2px 10px;border-radius:10px;font-size:13px;font-weight:bold}
    .status-warn{display:inline-block;background:#fff3e0;color:#e65100;
                 padding:2px 10px;border-radius:10px;font-size:13px;font-weight:bold}
    .status-danger{display:inline-block;background:#ffebee;color:#b71c1c;
                   padding:2px 10px;border-radius:10px;font-size:13px;font-weight:bold}
    .hot-wrap{max-width:720px;margin:0 auto 16px;background:#fff8e1;
              border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden}
    .hot-hdr{padding:14px 20px 12px;border-bottom:1px solid #eee}
    .hot-hdr h2{margin:0;font-size:18px;color:#e65100}
    .hot-hdr p{margin:3px 0 0;font-size:12px;color:#666}
    .hot-body{padding:4px 0}
    .topic-item{padding:12px 20px;border-bottom:1px solid #ffe082}
    .topic-item:last-child{border-bottom:none}
    .topic-title{font-size:14px;font-weight:bold;color:#bf360c;margin-bottom:5px}
    .topic-detail{font-size:13px;color:#555;line-height:1.7}
    .review-wrap{max-width:720px;margin:16px auto 0;background:#fff;
                 border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden}
    .review-hdr{padding:14px 20px 12px;border-bottom:1px solid #eee}
    .review-hdr h2{margin:0;font-size:18px;color:#2e7d32}
    .review-hdr p{margin:3px 0 0;font-size:12px;color:#666}
    .review-body{padding:12px 16px}
    .review-table{width:100%;border-collapse:collapse;font-size:13px}
    .review-table th{background:#eceff1;color:#37474f;padding:7px 10px;
                     text-align:left;font-weight:600;white-space:nowrap}
    .review-table td{padding:7px 10px;border-bottom:1px solid #f0f0f0;vertical-align:top}
    .review-table tr:last-child td{border-bottom:none}
    """

    blocks = []
    for s in passing:
        code    = s["code"]
        name    = s.get("name") or TW_STOCK_NAMES.get(code, "")
        tech    = s["tech"]
        sources = s.get("sources", "")
        global_ = s.get("global_signal", "")
        fin     = s.get("financials", {})
        news    = s.get("news", [])

        close    = tech["last_close"]
        pct      = tech["pct_vs_5ma"]
        vol_r    = tech["vol_ratio"]
        suffix   = tech["suffix"]
        pct_sign = "+" if pct >= 0 else ""
        pct_cls  = "up" if pct >= 0 else "dn"
        pct_str  = f"{pct_sign}{pct:.1f}% vs 5MA"

        rev_list  = fin.get("revenue", [])
        eps_list  = fin.get("eps", [])
        gm_list   = fin.get("gross_margin", [])
        inv_list  = fin.get("inventory_days", [])
        trail_pe  = fin.get("trailing_pe")
        fwd_pe    = fin.get("forward_pe")
        pe_hi     = fin.get("pe_52w_high")
        pe_lo     = fin.get("pe_52w_low")
        r_price   = fin.get("reasonable_price")
        p_price   = fin.get("predicted_price")
        t_mean    = fin.get("target_mean")
        verdict   = _pe_assessment(fin)
        concept_tags = s.get("concept_tags", [])
        ai_themes    = s.get("verdict", {}).get("themes", [])
        inst      = s.get("institutional", {})
        mgn       = s.get("margin_data", {})
        ai_v      = s.get("verdict", {})
        rsi       = tech.get("rsi")

        # 標題列（名稱超連結到鉅亨網）
        cnyes_url = f"https://www.cnyes.com/twstock/{code}"
        name_link = (f'<a href="{cnyes_url}" '
                     f'style="color:#1a237e;text-decoration:none">{name}</a>')
        h = (f'<div class="card">'
             f'<h3>▶ {code} {name_link}'
             f'&nbsp;&nbsp;<span style="font-weight:normal;font-size:14px">${close:,.1f}</span>'
             f'&nbsp;<span class="badge {pct_cls}">{pct_str}</span>'
             f'&nbsp;<span style="font-weight:normal;font-size:11px;color:#aaa">{suffix} · {sources}</span>'
             f'</h3>')

        # Gemini 評級 banner
        if ai_v:
            rating = ai_v.get("rating", "")
            reason = ai_v.get("reason", "")
            css_map = {
                "積極買入": "v-strong-buy", "買入": "v-buy",
                "觀望": "v-hold", "減碼": "v-reduce", "避開": "v-avoid",
            }
            vcls = css_map.get(rating, "v-hold")
            h += (f'<div class="verdict-box {vcls}">'
                  f'{"🚀" if "積極" in rating else "✅" if rating=="買入" else "⚠️" if rating=="觀望" else "🔻"}'
                  f' {rating}　<span style="font-weight:normal;font-size:13px">{reason}</span>'
                  f'</div>')
        else:
            h += ('<div class="verdict-box" style="background:#bdbdbd;color:#fff">'
                  '⏳ AI 評級暫時無法取得（Gemini API 配額已用完，明日自動恢復）'
                  '</div>')

        # 概念股 tag（Yahoo 概念股 綠色）+ AI 題材（深藍色）
        all_tag_html = ""
        for t in concept_tags:
            all_tag_html += (
                f'<span style="background:#e8f5e9;color:#1b5e20;padding:2px 9px;'
                f'border-radius:10px;font-size:12px;margin-right:4px;'
                f'display:inline-block;margin-bottom:3px">{t}</span>'
            )
        for t in ai_themes:
            all_tag_html += (
                f'<span style="background:#e3f2fd;color:#0d47a1;padding:2px 9px;'
                f'border-radius:10px;font-size:12px;margin-right:4px;'
                f'display:inline-block;margin-bottom:3px">🔖 {t}</span>'
            )
        if all_tag_html:
            h += f'<div class="row" style="margin:5px 0">🏷 {all_tag_html}</div>'

        # 全球相關股票
        if global_:
            h += f'<div class="row">🌐 {global_}</div>'

        # 篩選條件摘要
        filter_reasons = tech.get("filter_reasons", [])
        if filter_reasons:
            reason_str = "&nbsp;｜&nbsp;".join(filter_reasons)
            h += (f'<div class="row" style="font-size:11px;color:#6a1b9a;'
                  f'background:#f3e5f5;border-radius:4px;padding:4px 8px;margin:3px 0">'
                  f'🔍 篩選依據：{reason_str}</div>')

        # 量比 + RSI
        rsi_str = ""
        if rsi is not None:
            rsi_color = ("#c62828" if rsi > 70 else "#2e7d32" if rsi < 30 else "#333")
            rsi_str = (f'&nbsp;|&nbsp;RSI '
                       f'<b style="color:{rsi_color}">{rsi:.0f}</b>')
        h += (f'<div class="row">📊 量比 {vol_r:.1f}x{rsi_str}</div>')

        # 三大法人
        if inst:
            foreign = inst.get("foreign", 0)
            trust   = inst.get("trust", 0)
            dealer  = inst.get("dealer", 0)
            total   = inst.get("total", 0)
            def _signed(n):
                return f'<span style="color:{"#c62828" if n < 0 else "#2e7d32"}">{("+" if n >= 0 else "")}{n:,}張</span>'
            h += (f'<div class="inst-box">🏦 三大法人：'
                  f'外資 {_signed(foreign)}&nbsp;｜&nbsp;'
                  f'投信 {_signed(trust)}&nbsp;｜&nbsp;'
                  f'自營 {_signed(dealer)}&nbsp;｜&nbsp;'
                  f'合計 {_signed(total)}</div>')

        # 融資融券
        if mgn:
            m_chg = mgn.get("margin_chg", 0)
            s_chg = mgn.get("short_chg", 0)
            m_bal = mgn.get("margin_balance", 0)
            s_bal = mgn.get("short_balance", 0)
            h += (f'<div class="margin-box">📉 融資：餘額 {m_bal:,}張 '
                  f'增減 {("+" if m_chg >= 0 else "")}{m_chg:,}張&nbsp;｜&nbsp;'
                  f'融券：餘額 {s_bal:,}張 '
                  f'增減 {("+" if s_chg >= 0 else "")}{s_chg:,}張</div>')

        # 新聞
        news_html = _fmt_news_html(news)
        if news_html:
            h += f'<div class="news-section">{news_html}</div>'

        # 財報表格
        latest_q  = fin.get("latest_quarter", "")
        has_fin   = rev_list or eps_list
        if has_fin:
            h += '<table class="fin">'
            if rev_list:
                cells = ""
                for label, v in rev_list:
                    is_q = str(label) == latest_q
                    style = (' style="color:#1565c0;font-weight:bold"'
                             if is_q else "")
                    cells += f"<td{style}>{label}: {v:,.1f}</td>"
                h += f"<tr><td>📈 營收(億)</td>{cells}</tr>"
            if eps_list:
                cells = ""
                for label, v in eps_list:
                    is_q = str(label) == latest_q
                    style = (' style="color:#1565c0;font-weight:bold"'
                             if is_q else "")
                    cells += f"<td{style}>{label}: {v:.2f}</td>"
                h += f"<tr><td>💰 EPS(元)</td>{cells}</tr>"
            if latest_q:
                h += (f'<tr><td colspan="10" style="color:#888;font-size:11px;'
                      f'padding-top:2px">※ <b style="color:#1565c0">{latest_q}</b>'
                      f' 為最新季報，其餘為年度合計</td></tr>')
            h += "</table>"

        # 毛利率趨勢 + 庫存天數
        gm_inv_parts = []
        if gm_list:
            gm_str = " → ".join(
                f'<b style="color:#1565c0">{v:.0f}%</b>' if str(lbl) == latest_q
                else f"{v:.0f}%"
                for lbl, v in gm_list
            )
            gm_inv_parts.append(f"毛利率：{gm_str}")
        if inv_list:
            inv_str = " → ".join(f"{int(v)}天" for _, v in inv_list)
            gm_inv_parts.append(f"庫存天數：{inv_str}")
        if gm_inv_parts:
            h += f'<div class="gm-box">▪ {"　｜　".join(gm_inv_parts)}</div>'

        # 本益比
        pe_parts = []
        if trail_pe:
            pe_parts.append(f"目前 <b>{trail_pe:.1f}x</b>")
        if pe_hi and pe_lo:
            pe_parts.append(f"52W {pe_lo:.0f}x ~ {pe_hi:.0f}x")
        if fwd_pe and fwd_pe > 0:
            pe_parts.append(f"前瞻 <b>{fwd_pe:.1f}x</b>")
        if pe_parts:
            h += f'<div class="pe-bar">▪ 本益比：{"&nbsp;&nbsp;|&nbsp;&nbsp;".join(pe_parts)}</div>'

        # 合理 / 預測股價
        price_parts = []
        if r_price:
            price_parts.append(f"合理股價 <b>${r_price:,.0f}</b>（52W P/E中位 × EPS）")
        if p_price:
            src = "分析師目標" if t_mean else "52W P/E中位 × 前瞻EPS"
            price_parts.append(f"預測股價 <b>${p_price:,.0f}</b>（{src}）")
        if price_parts:
            h += f'<div class="price-box">▪ {"&nbsp;&nbsp;|&nbsp;&nbsp;".join(price_parts)}</div>'

        if verdict:
            h += f'<div class="verdict">▪ 估值：{verdict}</div>'

        h += "</div>"
        blocks.append(h)

    # 熱門討論話題區塊（獨立在台股選股雷達之前）
    if hot_topics:
        topic_parts = []
        for t in hot_topics:
            if not t.get("title"):
                continue
            url = t.get("url", "")
            link_html = (
                f' <a href="{url}" target="_blank" '
                f'style="display:inline-block;margin-top:5px;font-size:12px;'
                f'color:#e65100;text-decoration:none;border:1px solid #e65100;'
                f'border-radius:4px;padding:1px 7px">📰 原文連結</a>'
                if url else ""
            )
            topic_parts.append(
                f'<div class="topic-item">'
                f'<div class="topic-title">• {t["title"]}</div>'
                f'<div class="topic-detail">{t["detail"]}{link_html}</div>'
                f'</div>'
            )
        items_html = "".join(topic_parts)
    else:
        # 佔位符：Gemini 無法使用時顯示
        items_html = (
            '<div class="topic-item">'
            '<div class="topic-title">• 話題一：市場動態分析</div>'
            '<div class="topic-detail" style="color:#aaa">⏳ Gemini API 今日配額已用完，此區塊明日自動恢復。'
            '正式運作後將顯示來自鉅亨網、工商時報、經濟日報、Digitimes、數位時代、Yahoo財經的熱門討論話題。</div>'
            '</div>'
        )
    # ── 大盤概況區塊 ─────────────────────────────────────────
    mo = market_overview or {}
    twii_close = mo.get("twii_close")
    twii_chg   = mo.get("twii_chg")
    twii_pct   = mo.get("twii_pct")
    ytd_0050   = mo.get("ytd_0050")
    mkt_status = mo.get("market_status", "")
    mkt_detail = mo.get("market_detail", "")

    # 加權指數欄
    if twii_close is not None and twii_pct is not None:
        idx_cls = "mkt-up" if twii_chg >= 0 else "mkt-dn"
        idx_sign = "+" if twii_chg >= 0 else ""
        idx_arrow = "▲" if twii_chg >= 0 else "▼"
        twii_html = (
            f'<div class="mkt-item-val {idx_cls}">{twii_close:,.0f}</div>'
            f'<div class="mkt-item-sub {idx_cls}">'
            f'{idx_arrow} {idx_sign}{twii_chg:,.0f}　({idx_sign}{twii_pct:.2f}%)</div>'
        )
    else:
        twii_html = '<div class="mkt-item-val mkt-neu">—</div><div class="mkt-item-sub">無資料</div>'

    # 0050 YTD 欄
    if ytd_0050 is not None:
        ytd_cls   = "mkt-up" if ytd_0050 >= 0 else "mkt-dn"
        ytd_sign  = "+" if ytd_0050 >= 0 else ""
        ytd_arrow = "▲" if ytd_0050 >= 0 else "▼"
        ytd_html  = (
            f'<div class="mkt-item-val {ytd_cls}">{ytd_arrow} {ytd_sign}{ytd_0050:.2f}%</div>'
            f'<div class="mkt-item-sub">元大台灣 50 今年以來</div>'
        )
    else:
        ytd_html = '<div class="mkt-item-val mkt-neu">—</div><div class="mkt-item-sub">無資料</div>'

    # 00631L 大盤狀態欄
    if mkt_status:
        badge_cls = {"強勢區": "status-strong", "縮量破位": "status-warn",
                     "帶量跌破": "status-danger"}.get(mkt_status, "status-warn")
        status_html = (
            f'<div class="mkt-item-val" style="font-size:16px;margin-bottom:6px">'
            f'<span class="{badge_cls}">{mkt_status}</span></div>'
            f'<div class="mkt-item-sub">{mkt_detail}</div>'
        )
    else:
        status_html = '<div class="mkt-item-val mkt-neu">—</div><div class="mkt-item-sub">無資料</div>'

    market_html = f"""
  <div class="mkt-wrap">
    <div class="mkt-hdr">
      <h2>📊 今日大盤概況</h2>
      <p>{today}　資料來源：yfinance（^TWII / 0050.TW / 00631L.TW）</p>
    </div>
    <div class="mkt-body">
      <div class="mkt-item">
        <div class="mkt-item-label">加權指數</div>
        {twii_html}
      </div>
      <div class="mkt-item">
        <div class="mkt-item-label">0050 YTD</div>
        {ytd_html}
      </div>
      <div class="mkt-item">
        <div class="mkt-item-label">大盤狀態（00631L 20MA）</div>
        {status_html}
      </div>
    </div>
  </div>"""

    hot_html = f"""
  <div class="hot-wrap">
    <div class="hot-hdr">
      <h2>🔥 今日市場熱門討論話題</h2>
      <p>{today}　資料來源：鉅亨網 / 工商時報 / 經濟日報 / Digitimes / 數位時代 / Yahoo財經</p>
    </div>
    <div class="hot-body">{items_html}</div>
  </div>"""

    top_html = _build_top_picks_html()

    body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{CSS}</style></head>
<body>
  {top_html}
  {market_html}
  {hot_html}
  <div class="wrap">
    <div class="hdr">
      <h2>🔭 台股選股雷達</h2>
      <p>{today}　共 {n} 支通過技術篩選</p>
    </div>
    {"".join(blocks)}
    <div class="footer">本報告為資訊彙整，非投資建議。資料來源：TWSE / TPEx / 鉅亨網 / yfinance</div>
  </div>
  {review_html}
</body></html>"""
    return body


# ═══════════════════════════════════════════════════════════
#  Main entry: run_scout()
# ═══════════════════════════════════════════════════════════

def run_scout() -> str:
    """
    執行完整四階段管線，回傳 Telegram 格式字串。
    """
    print("[Scout] Stage 1: 收集標的...")

    # 預先取得完整名稱對照表（TWSE + TPEx openAPI）
    full_names = _fetch_all_stock_names()
    # 合併 config 內建名稱（作為 fallback）
    full_names = {**TW_STOCK_NAMES, **full_names}

    def _name(code: str, fallback: str = "") -> str:
        return full_names.get(code) or fallback or ""

    # 1a. 成交量排行
    vol_stocks = _get_volume_candidates()
    vol_map    = {s["code"]: s for s in vol_stocks}
    print(f"  量排：{len(vol_stocks)} 支")

    # 1b. CnYes 新聞話題
    cnyes_batch = fetch_cnyes_batch(pages=5)
    cnyes_codes = _cnyes_hot_codes(cnyes_batch, min_mentions=2)
    print(f"  CnYes 熱點：{len(cnyes_codes)} 支")

    # 1c. bnext 新聞話題
    bnext_codes = _bnext_codes_via_gemini()
    print(f"  數位時代：{len(bnext_codes)} 支")

    # 1d. 合併去重，附帶來源標記（ETF 全部過濾）
    all_codes: dict[str, dict] = {}
    for s in vol_stocks:
        c = s["code"]
        all_codes[c] = {"code": c, "name": _name(c, s["name"]), "sources": "量", "news_reason": ""}
    for c, reason in cnyes_codes.items():
        if _is_etf(c):
            continue
        if c in all_codes:
            all_codes[c]["sources"] += "+鉅亨"
        else:
            all_codes[c] = {"code": c, "name": _name(c), "sources": "鉅亨", "news_reason": ""}
        if not all_codes[c]["news_reason"]:
            all_codes[c]["news_reason"] = reason
    for c, reason in bnext_codes.items():
        if _is_etf(c):
            continue
        if c in all_codes:
            all_codes[c]["sources"] += "+數位時代"
        else:
            all_codes[c] = {"code": c, "name": _name(c), "sources": "數位時代", "news_reason": ""}
        if not all_codes[c]["news_reason"]:
            all_codes[c]["news_reason"] = reason

    print(f"  合計候選：{len(all_codes)} 支")

    # Stage 2: 技術面篩選
    print("[Scout] Stage 2: 技術面篩選...")
    passing: list[dict] = []
    for info in all_codes.values():
        code = info["code"]
        tech = _tech_filter(code)
        if tech is None:
            continue
        info["tech"] = tech
        passing.append(info)

    print(f"  通過技術篩選：{len(passing)} 支")

    # Stage 2.5: 財報指標 + 個股新聞
    print("[Scout] Stage 2.5: 抓取財報指標 + 新聞...")
    passing_codes = [s["code"] for s in passing]
    news_map = cnyes_news_for_codes(passing_codes, cnyes_batch, max_per_code=2)

    for i, info in enumerate(passing, 1):
        code   = info["code"]
        suffix = info["tech"]["suffix"]
        print(f"  [{i}/{len(passing)}] {code} {info.get('name','')}")
        info["financials"]    = _fetch_financials(code, suffix)
        info["concept_tags"]  = _fetch_concept_tags(code, suffix)

        # 新聞：直接用 CnYes 內建 summary（不額外呼叫 Gemini）
        raw_news = news_map.get(code, [])
        news_items = []
        for item in raw_news:
            news_id = item.get("newsId", 0)
            title   = item.get("title", "")
            summary = item.get("summary") or ""   # API 偶爾回傳 None，需轉為空字串
            pub_ts  = item.get("publishAt", 0)
            try:
                time_str = datetime.fromtimestamp(pub_ts).strftime("%m/%d %H:%M")
            except Exception:
                time_str = ""
            link = f"https://news.cnyes.com/news/id/{news_id}" if news_id else ""
            # 把摘要整理成 bullet points（段落拆分）
            bullets = [l.strip() for l in summary.split("。") if len(l.strip()) > 5][:3]
            summary_fmt = "。\n".join(bullets) + "。" if bullets else summary[:80]
            news_items.append({
                "title":   title,
                "summary": summary_fmt,
                "link":    link,
                "time":    time_str,
            })
        info["news"] = news_items

    # Stage 2.6: 三大法人 + 融資融券
    print("[Scout] Stage 2.6: 三大法人 + 融資融券...")
    inst_all   = _fetch_institutional_all()
    margin_all = _fetch_margin_all()
    for info in passing:
        code = info["code"]
        info["institutional"] = inst_all.get(code, {})
        info["margin_data"]   = margin_all.get(code, {})

    # Stage 2.7: 週轉率 + 土洋合買標籤（需要 financials + institutional 都完成後才能算）
    print("[Scout] Stage 2.7: 計算週轉率 + 土洋合買標籤...")
    for info in passing:
        tech = info["tech"]
        fin  = info.get("financials", {})
        inst = info.get("institutional", {})

        # 週轉率 = 當日成交量（股）/ 在外流通股數（股） × 100
        shares   = fin.get("shares_outstanding")
        last_vol = tech.get("last_vol", 0)
        if shares and shares > 0 and last_vol > 0:
            info["turnover_rate"] = round(float(last_vol) / float(shares) * 100, 2)
        else:
            info["turnover_rate"] = None

        # 土洋合買：外資與投信同日買超
        info["dual_buy"] = (inst.get("foreign", 0) > 0 and inst.get("trust", 0) > 0)

    # Stage 3: 全球對焦（Gemini 批次判斷供應鏈連動指標）
    print("[Scout] Stage 3: Gemini 判斷供應鏈連動...")
    signals = _build_global_signals(passing)
    for info in passing:
        info["global_signal"] = signals.get(info["code"], "")

    # Stage 3.5: Gemini 綜合評級
    print("[Scout] Stage 3.5: Gemini 綜合買賣評級...")
    verdicts = _gemini_batch_verdict(passing)
    for info in passing:
        info["verdict"] = verdicts.get(info["code"], {})

    # 依買入評級排序（積極買入 > 買入 > 觀望 > 減碼 > 避開 > 無評級）
    _RATING_ORDER = {"積極買入": 0, "買入": 1, "觀望": 2, "減碼": 3, "避開": 4}
    passing.sort(key=lambda s: _RATING_ORDER.get(
        s.get("verdict", {}).get("rating", ""), 5
    ))
    print(f"  排序完成，頂部評級：{passing[0].get('verdict', {}).get('rating', '無') if passing else '-'}")

    # 儲存歷史記錄（供昨日回顧使用）
    _save_scout_history(passing)

    # Stage 3.6: 大盤概況
    print("[Scout] Stage 3.6: 取得今日大盤概況（加權指數 / 0050 YTD / 00631L）...")
    market_overview = _fetch_market_overview()
    status_label = market_overview.get("market_status") or "無資料"
    twii_close   = market_overview.get("twii_close")
    print(f"  加權指數：{twii_close:,.0f}" if twii_close else "  加權指數：無資料")
    print(f"  大盤狀態：{status_label}")

    # Stage 3.7: 抓取熱門討論話題
    print("[Scout] Stage 3.7: 抓取今日市場熱門話題...")
    hot_topics = _fetch_hot_topics()
    print(f"  熱門話題：{'已取得 ' + str(len(hot_topics)) + ' 則' if hot_topics else '無資料'}")

    # 昨日回顧（3 個交易日前積極買入 → 今日追蹤績效）
    print("[Scout] 昨日回顧：查詢 3 個交易日前積極買入的表現...")
    review_html = _build_review_html()
    print(f"  昨日回顧：{'有資料' if review_html else '尚無歷史記錄（首次執行）'}")

    # Stage 4: 格式化 HTML
    print("[Scout] Stage 4: 格式化 HTML 報告...")
    return _format_report_html(
        passing,
        hot_topics=hot_topics,
        review_html=review_html,
        market_overview=market_overview,
    )
