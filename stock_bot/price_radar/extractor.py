"""
Gemini 萃取模組 — 從新聞標題+摘要中提取功率元件漲價資訊
"""
import json
import os
import sys
import time
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from google import genai
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

_client = None

def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY", "")
        _client = genai.Client(api_key=api_key)
    return _client


PROMPT = """你是半導體產業分析師，專門追蹤功率元件（MOSFET、IGBT、SiC、GaN、整流器等）的漲價動態。

請從以下新聞中萃取漲價相關資訊，**只輸出 JSON，不要任何說明文字**。

格式：
{{
  "is_relevant": true/false,
  "company": "公司名稱（無則null）",
  "product": "產品名稱，例如 SiC MOSFET、IGBT 模組（無則null）",
  "price_change_pct": 漲幅數字如 10（無則null，只填數字不填%）,
  "effective_date": "生效日期 YYYY-MM-DD（無則null）",
  "downstream": "下游通知對象，例如：電動車廠、充電樁廠（無則null）",
  "reason": "漲價原因一句話摘要（無則null）",
  "confidence": 0.0到1.0之間的數字（對漲價事實的確信程度）
}}

若文章與功率元件漲價/供貨吃緊/報價調整**完全無關**，回傳 {{"is_relevant": false}}。

新聞標題：{title}
新聞摘要：{summary}
"""


def extract(article: dict) -> dict | None:
    """
    傳入 article dict（需有 title、summary、url、source_title、published_at）
    回傳結構化 signal dict，或 None（不相關）
    """
    client = _get_client()
    text_input = PROMPT.format(
        title=article.get("title", ""),
        summary=article.get("summary", "")[:1000],
    )

    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=text_input,
            )
            raw = resp.text.strip()

            # 去掉可能的 markdown code block
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            data = json.loads(raw)
            break
        except Exception as e:
            err_str = str(e)
            if "429" in err_str and attempt < 2:
                # 從錯誤訊息解析等待秒數，預設 60 秒
                import re
                wait_match = re.search(r"retry in (\d+)", err_str)
                wait = int(wait_match.group(1)) + 2 if wait_match else 60
                print(f"  [extractor] rate limit，等待 {wait} 秒後重試...")
                time.sleep(wait)
            else:
                print(f"  [extractor] 解析失敗：{e}")
                return None
    else:
        return None

    if not data.get("is_relevant"):
        return None

    return {
        "company": data.get("company"),
        "product": data.get("product"),
        "price_change_pct": data.get("price_change_pct"),
        "effective_date": data.get("effective_date"),
        "downstream": data.get("downstream"),
        "reason": data.get("reason"),
        "confidence": float(data.get("confidence", 0.0)),
        "source_url": article.get("url", ""),
        "source_title": article.get("title", ""),
        "published_at": article.get("published"),
    }
