"""
verifier.py — 驗證 X 貼文的可信度
  1. MOPS 重大訊息查詢（有無官方公告）
  2. 鉅亨網新聞比對（有無相關報導）
  3. yfinance 成交量異常偵測
  4. Gemini AI 綜合評分
"""

import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta

import requests
import yfinance as yf
from google import genai

BASE_DIR = Path(__file__).parent.parent

# ── 股票代號擷取 ──────────────────────────────────────────────

TW_TICKER_RE = re.compile(r'\b(\d{4,6})\b')
US_TICKER_RE = re.compile(r'\$([A-Z]{1,5})\b')

def _extract_tickers(text: str) -> tuple[list[str], list[str]]:
    tw = list(set(TW_TICKER_RE.findall(text)))
    us = list(set(US_TICKER_RE.findall(text)))
    return tw, us


# ── MOPS 重大訊息 ─────────────────────────────────────────────

def _check_mops(tw_tickers: list[str]) -> str:
    """查詢 MOPS 最近 3 天的重大訊息，回傳摘要文字"""
    if not tw_tickers:
        return ""

    results = []
    today = datetime.now()
    date_str = today.strftime("%Y%m%d")

    for ticker in tw_tickers[:3]:  # 最多查 3 檔避免太慢
        try:
            url = (
                "https://mops.twse.com.tw/mops/web/ajax_t05st01"
            )
            payload = {
                "encodeURIComponent": "1",
                "step": "1",
                "firstin": "1",
                "off": "1",
                "keyword4": "",
                "code1": "",
                "TYPEK2": "",
                "checkbtn": "",
                "queryName": "co_id",
                "inpuType": "co_id",
                "TYPEK": "all",
                "isnew": "false",
                "co_id": ticker,
                "begin_date": (today - timedelta(days=7)).strftime("%Y%m%d"),
                "end_date": date_str,
                "NUM_PER_PAGE": "10",
            }
            resp = requests.post(url, data=payload, timeout=8)
            if resp.status_code == 200 and ticker in resp.text:
                results.append(f"  ✅ {ticker}：MOPS 近 7 天有相關公告")
            else:
                results.append(f"  ❌ {ticker}：MOPS 近 7 天無相關公告")
        except Exception as e:
            results.append(f"  ⚠️ {ticker}：MOPS 查詢失敗（{e}）")

    return "【MOPS 重大訊息】\n" + "\n".join(results) if results else ""


# ── 鉅亨網新聞 ───────────────────────────────────────────────

def _check_cnyes(keywords: list[str]) -> str:
    """用關鍵字搜尋鉅亨網新聞，回傳最近相關標題"""
    if not keywords:
        return ""

    results = []
    for kw in keywords[:2]:
        try:
            url = f"https://api.cnyes.com/media/api/v1/newslist/category/headline?limit=5&keyword={kw}"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(url, headers=headers, timeout=8)
            data = resp.json()
            items = data.get("items", {}).get("data", [])
            if items:
                titles = [it.get("title", "") for it in items[:3]]
                results.append(f"  「{kw}」相關新聞：" + "、".join(titles))
            else:
                results.append(f"  「{kw}」：鉅亨網無近期新聞")
        except Exception as e:
            results.append(f"  「{kw}」：鉅亨網查詢失敗（{e}）")

    return "【鉅亨網新聞】\n" + "\n".join(results) if results else ""


# ── yfinance 成交量異常 ───────────────────────────────────────

def _check_volume(tw_tickers: list[str], us_tickers: list[str]) -> str:
    """偵測近 5 日成交量是否異常放大（超過 20 日均量 1.5 倍）"""
    results = []
    all_tickers = (
        [(t + ".TW") for t in tw_tickers[:2]] +
        [t for t in us_tickers[:2]]
    )

    for ticker in all_tickers:
        try:
            df = yf.download(ticker, period="30d", interval="1d", progress=False, auto_adjust=True)
            if df.empty or len(df) < 10:
                continue
            avg_vol = df["Volume"].iloc[:-5].mean()
            recent_vol = df["Volume"].iloc[-1]
            ratio = recent_vol / avg_vol if avg_vol > 0 else 0
            label = ticker.replace(".TW", "")
            if ratio >= 1.5:
                results.append(f"  ⚠️ {label}：近日成交量為均量 {ratio:.1f} 倍，異常放大")
            else:
                results.append(f"  ✅ {label}：成交量正常（均量 {ratio:.1f} 倍）")
        except Exception:
            continue

    return "【成交量偵測】\n" + "\n".join(results) if results else ""


# ── Gemini 可信度評分 ─────────────────────────────────────────

def _gemini_score(post: dict, evidence: str) -> dict:
    """
    請 Gemini 綜合評估貼文可信度。
    回傳 { score: 1-10, level: 高/中/低, reason: str, stocks: [str] }
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"score": 0, "level": "未知", "reason": "GEMINI_API_KEY 未設定", "stocks": []}

    client = genai.Client(api_key=api_key)

    prompt = f"""你是一位專業的股市分析師，專門識別 X（Twitter）上的股市小作文與傳聞。

請分析以下貼文的可信度：

【貼文內容】
作者：{post.get('author', '未知')}
時間：{post.get('time', '未知')}
內文：{post.get('text', '')}

【查證結果】
{evidence if evidence else '無外部資料'}

請從以下面向評估並給出可信度分數（1-10，10 最可信）：
1. 是否有具體數字、時間、公司名稱（越具體越可信）
2. 措辭是否像散戶臆測（「感覺」「應該」）還是有消息來源
3. 官方公告或新聞是否印證
4. 成交量是否配合

請以 JSON 格式回覆：
{{
  "score": <1-10>,
  "level": "<高|中|低>",
  "reason": "<2-3句說明>",
  "stocks": ["<提到的股票代號清單>"]
}}

注意：score 7+ 為高可信（值得關注），4-6 為中，3- 為低（可能為假消息或無意義謠言）。"""

    import time
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            text = resp.text.strip()
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
            break
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "503" in err_str:
                wait = 25 * (attempt + 1)
                print(f"  ⏳ Gemini 限流，等待 {wait}s 後重試...")
                time.sleep(wait)
            else:
                print(f"  ⚠️ Gemini 評分失敗：{e}")
                break

    return {"score": 0, "level": "未知", "reason": "評分失敗", "stocks": []}


# ── 主要入口 ──────────────────────────────────────────────────

def verify_post(post: dict) -> dict:
    """
    對單則貼文執行完整驗證，回傳包含可信度的 dict。
    只有 score >= 7 才算「值得關注」。
    """
    text = post.get("text", "")
    tw_tickers, us_tickers = _extract_tickers(text)

    # 取出前幾個關鍵字作為新聞搜尋詞
    keywords = tw_tickers[:2] + us_tickers[:2]
    if not keywords:
        # 從文字中取股票名稱
        for name in ["台積電", "輝達", "美光", "博通", "特斯拉"]:
            if name in text:
                keywords.append(name)

    evidence_parts = []

    mops_result = _check_mops(tw_tickers)
    if mops_result:
        evidence_parts.append(mops_result)

    cnyes_result = _check_cnyes(keywords)
    if cnyes_result:
        evidence_parts.append(cnyes_result)

    volume_result = _check_volume(tw_tickers, us_tickers)
    if volume_result:
        evidence_parts.append(volume_result)

    evidence = "\n\n".join(evidence_parts)

    rating = _gemini_score(post, evidence)

    return {
        **post,
        "score": rating.get("score", 0),
        "level": rating.get("level", "未知"),
        "reason": rating.get("reason", ""),
        "stocks": rating.get("stocks", []),
        "evidence": evidence,
    }
