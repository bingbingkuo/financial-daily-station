"""
股票代號驗證器：過濾掉非真實股票的代號
- 台股：比對 TWSE/TPEx 掛牌清單
- 美股：白名單 + yfinance 動態確認
"""
import urllib.request
import json
import time
from pathlib import Path
import yfinance as yf

CACHE_PATH = Path(__file__).parent / "data" / "tw_stocks_cache.json"

# 明確排除的非股票大寫縮寫
US_BLACKLIST = {
    # 技術規格 / 產品型號
    "AI", "VPN", "CPU", "GPU", "RAM", "SSD", "USB", "PCB", "LED", "LCD",
    "GTX", "RX", "DDR", "NVMe", "PCIe", "HDMI", "OLED", "AMOLED",
    "LTE", "HBM", "CoWoS", "HPC", "TPU", "NPU", "MCU", "IC", "IP", "IoT",
    "AR", "VR", "XR", "MR", "OS", "API", "SDK", "UI", "UX", "IPO",
    # 組織 / 機構
    "FED", "ECB", "BOJ", "IMF", "WTO", "OPEC", "NATO", "UN", "EU", "WB",
    "FDIC", "SEC", "CFTC", "OCC",
    # 常見縮寫
    "CEO", "CFO", "COO", "CTO", "CSO", "VP", "MD", "LLC", "LTD", "INC",
    "ETF", "IPO", "ESG", "PE", "VC", "LBO", "ROE", "ROA", "EPS", "PBR",
    "EBITDA", "FCF", "YOY", "QOQ", "GDP", "CPI", "PPI", "PMI",
    "US", "USD", "EUR", "JPY", "TWD", "CNY", "GBP",
    # 其他
    "OK", "NO", "IT", "IN", "OR", "AT", "BE", "BY", "IF",
}

def load_tw_stock_list() -> set:
    """載入台股清單（上市+上櫃，快取 7 天）"""
    if CACHE_PATH.exists():
        mtime = CACHE_PATH.stat().st_mtime
        if time.time() - mtime < 86400 * 7:
            data = json.load(open(CACHE_PATH))
            return set(data)

    codes = set()
    sources = [
        ("上市", "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"),
        ("上市ETF", "https://openapi.twse.com.tw/v1/ETF/domestic"),
    ]
    for name, url in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                for item in json.loads(r.read()):
                    code = item.get("Code") or item.get("SecuritiesCode") or item.get("stockCode")
                    if code:
                        codes.add(str(code))
        except Exception as e:
            print(f"  ✗ {name} 清單失敗: {e}")

    # 上櫃清單
    try:
        url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            content = r.read()
            for item in json.loads(content):
                code = item.get("SecuritiesCode") or item.get("Code")
                if code:
                    codes.add(str(code))
    except Exception:
        pass  # 上櫃 API 不穩定，忽略失敗

    print(f"  ✓ 台股清單：{len(codes)} 筆（上市+上櫃）")
    if codes:
        json.dump(list(codes), open(CACHE_PATH, "w"))
    return codes


_tw_codes: set | None = None

def validate_tw(symbols: list[str]) -> list[str]:
    """過濾台股代號，只保留真實掛牌的"""
    global _tw_codes
    if _tw_codes is None:
        _tw_codes = load_tw_stock_list()
    if not _tw_codes:
        return symbols  # 無法驗證時保留原樣

    valid = []
    for s in symbols:
        if s in _tw_codes:
            valid.append(s)
        else:
            # 嘗試加 0 前綴（如 878 → 00878）
            padded = s.zfill(5) if len(s) == 3 else s
            if padded in _tw_codes:
                valid.append(padded)
            else:
                # 清單查無，用 yfinance 做最後確認（.TW 或 .TWO）
                confirmed = False
                for suffix in [".TW", ".TWO"]:
                    try:
                        price = getattr(yf.Ticker(s + suffix).fast_info, "last_price", None)
                        if price and price > 0:
                            valid.append(s)
                            confirmed = True
                            break
                    except Exception:
                        pass
                if not confirmed:
                    print(f"  ⚠ 台股代號不存在，移除: {s}")
    return valid


def validate_us(symbols: list[str]) -> list[str]:
    """過濾美股代號，排除黑名單後用 yfinance 確認"""
    valid = []
    for s in symbols:
        if s in US_BLACKLIST:
            print(f"  ⚠ 非股票縮寫，移除: {s}")
            continue
        # 用 yfinance 確認是否有真實報價
        try:
            info = yf.Ticker(s).fast_info
            # fast_info 有 last_price 表示真實股票
            price = getattr(info, "last_price", None)
            if price and price > 0:
                valid.append(s)
            else:
                print(f"  ⚠ 美股代號無報價，移除: {s}")
        except Exception:
            print(f"  ⚠ 美股代號驗證失敗，移除: {s}")
    return valid


def validate_episode(episode: dict) -> dict:
    """驗證並清理一集的股票代號"""
    tw_raw = episode.get("tw_stocks", [])
    us_raw = episode.get("us_stocks", [])

    if tw_raw:
        episode["tw_stocks"] = validate_tw(tw_raw)
    if us_raw:
        episode["us_stocks"] = validate_us(us_raw)

    return episode
