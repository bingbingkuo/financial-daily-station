"""
macro.py — 總經數據模組
台灣總經：台灣官方來源（台灣銀行、TWSE、主計總處）
美國總經：GDP、CPI、失業率、零售銷售、十年期美債、美元指數（FRED + yfinance）
"""

import re
import csv
import io
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from fredapi import Fred
from config import FRED_API_KEY
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout

_HEADERS = {"User-Agent": "Mozilla/5.0"}

# FRED API 每次呼叫的超時上限（秒）
_FRED_TIMEOUT = 20


def _fred():
    return Fred(api_key=FRED_API_KEY)


def _fred_get(series_id: str, **kwargs) -> object:
    """帶 timeout 的 FRED get_series，超時拋出 TimeoutError"""
    def _inner():
        return _fred().get_series(series_id, **kwargs)
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_inner)
        try:
            return fut.result(timeout=_FRED_TIMEOUT)
        except _FuturesTimeout:
            raise TimeoutError(f"FRED {series_id} 超時（>{_FRED_TIMEOUT}s）")


# ══════════════════════════════════════════════════════════
#  台灣總經（台灣官方來源）
# ══════════════════════════════════════════════════════════

def _get_usd_twd():
    """台幣兌美元即期匯率 — 台灣銀行"""
    try:
        resp = requests.get("https://rate.bot.com.tw/xrt/flcsv/0/day",
                            headers=_HEADERS, timeout=8)
        resp.encoding = "utf-8-sig"
        reader = csv.reader(io.StringIO(resp.text))
        for row in reader:
            if row and row[0].strip() == "USD":
                buy  = float(row[2].strip())   # 即期買入
                sell = float(row[12].strip())  # 即期賣出
                mid  = round((buy + sell) / 2, 4)
                signal = "⚠️ 台幣偏弱" if mid > 32 else ("✅ 台幣偏強" if mid < 30 else "")
                return f"{mid:.3f}（買 {buy} / 賣 {sell}）{signal}"
        return "N/A"
    except Exception as e:
        return f"N/A ({e})"


def _get_taiex():
    """台股加權指數近期收盤 — TWSE"""
    try:
        resp = requests.get(
            "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?response=json",
            headers=_HEADERS, timeout=8)
        rows = resp.json().get("data", [])
        if len(rows) < 2:
            return "N/A"
        # row 格式: [日期, 開盤, 最高, 最低, 收盤]
        latest = rows[-1]
        prev   = rows[-2]
        close     = float(latest[4].replace(",", ""))
        prev_close = float(prev[4].replace(",", ""))
        change = close - prev_close
        pct    = change / prev_close * 100
        arrow  = "▲" if change > 0 else "▼"
        date   = latest[0]  # 民國年/月/日
        return f"{arrow} {close:,.2f} ({change:+.2f}, {pct:+.2f}%)  {date}"
    except Exception as e:
        return f"N/A ({e})"


def _get_tw_cpi():
    """台灣 CPI 年增率 — 主計總處新聞標題"""
    try:
        resp = requests.get("https://www.dgbas.gov.tw/cl.aspx?n=5101&mp=1",
                            headers=_HEADERS, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            t = a.get_text(strip=True)
            if "CPI" in t or "消費者物價" in t:
                # 擷取期間，例如 "115年3月"
                period_m = re.search(r"(\d{3}年\d+月)", t)
                # 擷取漲跌方向與數值，例如 "年增率漲1.20％"
                val_m = re.search(r"年增率([漲跌])([\d.]+)％", t)
                if period_m and val_m:
                    period    = period_m.group(1)
                    direction = val_m.group(1)
                    value     = float(val_m.group(2))
                    if direction == "跌":
                        value = -value
                    arrow  = "🔥" if value > 3 else ("✅" if value < 2 else "⚠️")
                    return f"{arrow} {value:+.2f}% YoY（{period}）"
        return "N/A（找不到最新數據）"
    except Exception as e:
        return f"N/A ({e})"


def _get_tw_unemployment():
    """台灣失業率 — 主計總處新聞標題"""
    try:
        resp = requests.get("https://www.dgbas.gov.tw/cl.aspx?n=5103&mp=1",
                            headers=_HEADERS, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            t = a.get_text(strip=True)
            if "失業率" in t and "%" in t:
                # 擷取期間，例如 "115年2月"
                period_m = re.search(r"(\d{3}年\d+月)", t)
                # 擷取失業率數值，例如 "失業率為3.32%"
                val_m = re.search(r"失業率為([\d.]+)%", t)
                if period_m and val_m:
                    period = period_m.group(1)
                    value  = float(val_m.group(1))
                    return f"{value:.2f}%（{period}）"
        return "N/A（找不到最新數據）"
    except Exception as e:
        return f"N/A ({e})"


def _get_tw_indices_yesterday():
    """昨日美股三大指數收盤 — 台股盤前參考"""
    results = []
    mapping = [("^GSPC", "S&P500"), ("^IXIC", "那斯達克"), ("^DJI", "道瓊")]
    for ticker, name in mapping:
        try:
            hist   = yf.Ticker(ticker).history(period="5d")
            closes = hist["Close"].dropna()
            if len(closes) < 2:
                continue
            latest = closes.iloc[-1]
            prev   = closes.iloc[-2]
            pct    = (latest - prev) / prev * 100
            arrow  = "▲" if pct > 0 else "▼"
            results.append(f"{name} {arrow}{abs(pct):.2f}%")
        except Exception:
            pass
    return "  |  ".join(results) if results else "N/A"


def _get_dxy():
    """美元指數"""
    try:
        hist   = yf.Ticker("DX-Y.NYB").history(period="5d")
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return "N/A"
        latest = closes.iloc[-1]
        prev   = closes.iloc[-2]
        pct    = (latest - prev) / prev * 100
        arrow  = "▲" if pct > 0 else "▼"
        signal = "⚠️ 強勢美元" if latest > 105 else ("✅ 弱美元" if latest < 100 else "")
        return f"{arrow} {latest:.2f} ({pct:+.2f}%) {signal}"
    except Exception as e:
        return f"N/A ({e})"


def get_tw_macro() -> dict:
    return {
        "台股加權指數":       _get_taiex(),
        "台幣兌美元（台銀）":  _get_usd_twd(),
        "台灣 CPI（年增率）":  _get_tw_cpi(),
        "台灣失業率":         _get_tw_unemployment(),
        "昨日美股收盤":       _get_tw_indices_yesterday(),
        "美元指數 DXY":       _get_dxy(),
    }


def format_tw_macro(data: dict) -> str:
    lines = ["📊 【台灣總經環境】"]
    for key, val in data.items():
        lines.append(f"  • {key}：{val}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
#  美國總經
# ══════════════════════════════════════════════════════════

def _get_us_gdp():
    try:
        series = _fred_get("A191RL1Q225SBEA", observation_start="2022-01-01").dropna()
        latest = series.iloc[-1]
        period = series.index[-1]
        q      = (period.month - 1) // 3 + 1
        arrow  = "▲" if latest > 0 else "▼"
        return f"{arrow} {latest:+.1f}% ({period.year} Q{q})"
    except Exception as e:
        return f"N/A ({e})"


def _get_us_cpi():
    try:
        series = _fred_get("CPIAUCSL", observation_start="2022-01-01").dropna()
        yoy    = series.pct_change(12).dropna() * 100
        latest = yoy.iloc[-1]
        period = yoy.index[-1].strftime("%Y/%m")
        arrow  = "🔥" if latest > 3 else ("✅" if latest < 2.5 else "⚠️")
        return f"{arrow} {latest:.1f}% YoY ({period})"
    except Exception as e:
        return f"N/A ({e})"


def _get_us_unemployment():
    try:
        series = _fred_get("UNRATE", observation_start="2022-01-01").dropna()
        latest = series.iloc[-1]
        prev   = series.iloc[-2]
        period = series.index[-1].strftime("%Y/%m")
        change = latest - prev
        arrow  = "▲" if change > 0 else "▼"
        return f"{arrow} {latest:.1f}% ({change:+.1f}% MoM, {period})"
    except Exception as e:
        return f"N/A ({e})"


def _get_us_retail():
    try:
        series = _fred_get("RSXFS", observation_start="2022-01-01").dropna()
        mom    = series.pct_change().dropna() * 100
        latest = mom.iloc[-1]
        period = mom.index[-1].strftime("%Y/%m")
        arrow  = "▲" if latest > 0 else "▼"
        return f"{arrow} {latest:+.1f}% MoM ({period})"
    except Exception as e:
        return f"N/A ({e})"


def _get_tnx():
    try:
        hist   = yf.Ticker("^TNX").history(period="5d")
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return "N/A"
        latest = closes.iloc[-1]
        prev   = closes.iloc[-2]
        change = latest - prev
        arrow  = "▲" if change > 0 else "▼"
        signal = "⚠️ 偏高，壓制成長股" if latest > 4.5 else ("✅ 友善" if latest < 3.8 else "")
        return f"{arrow} {latest:.3f}% ({change:+.3f}) {signal}"
    except Exception as e:
        return f"N/A ({e})"


def get_us_macro() -> dict:
    return {
        "GDP（實質季增年率）":  _get_us_gdp(),
        "CPI（年增率）":        _get_us_cpi(),
        "失業率":               _get_us_unemployment(),
        "零售銷售（月增率）":   _get_us_retail(),
        "十年期美債殖利率":     _get_tnx(),
        "美元指數 DXY":         _get_dxy(),
    }


def format_us_macro(data: dict) -> str:
    lines = ["📊 【美國總經環境】"]
    for key, val in data.items():
        lines.append(f"  • {key}：{val}")
    return "\n".join(lines)
