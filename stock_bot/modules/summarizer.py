"""
summarizer.py — Gemini AI 新聞摘要模組
使用 Google Gemini 2.0 Flash（免費方案）閱讀全文並條列投資重點
"""

import time
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout

_GEMINI_TIMEOUT = 30  # Gemini API 每次呼叫超時上限（秒）

try:
    from google import genai
    _HAS_GENAI = True
except ImportError:
    _HAS_GENAI = False

_ARTICLE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://www.cnyes.com",
    "Referer": "https://www.cnyes.com/",
}

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not _HAS_GENAI:
        return None
    try:
        from config import GEMINI_API_KEY
        if not GEMINI_API_KEY:
            return None
        _client = genai.Client(api_key=GEMINI_API_KEY)
        return _client
    except Exception:
        return None


def fetch_article_text(news_id: int) -> str:
    """爬取鉅亨網文章全文（純文字）"""
    try:
        r = requests.get(
            f"https://news.cnyes.com/news/id/{news_id}",
            headers=_ARTICLE_HEADERS, timeout=10,
        )
        soup    = BeautifulSoup(r.text, "html.parser")
        article = soup.select_one("article")
        if article:
            # 移除 script / style
            for tag in article(["script", "style", "figure"]):
                tag.decompose()
            return article.get_text(separator=" ").strip()
    except Exception:
        pass
    return ""


def summarize_news(title: str, content: str) -> str:
    """
    呼叫 Gemini 閱讀新聞全文，條列 2-3 個繁體中文投資重點。
    若無 API key 或失敗，回傳空字串。
    """
    client = _get_client()
    if not client or not content:
        return ""
    try:
        prompt = (
            "請閱讀以下台股新聞，用繁體中文條列 2-3 個投資重點（每點不超過 35 字，"
            "只寫重點，不要加說明）：\n\n"
            f"標題：{title}\n\n"
            f"內容：{content[:3000]}\n\n"
            "輸出格式（只輸出重點，不需其他文字）：\n"
            "• 重點一\n• 重點二\n• 重點三（可省略）"
        )
        def _call():
            return client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt
            )
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_call)
            try:
                resp = fut.result(timeout=_GEMINI_TIMEOUT)
            except _FuturesTimeout:
                return ""
        time.sleep(0.5)   # 避免連續呼叫觸發 10 RPM 限制（新聞摘要一次最多 16 篇）
        return resp.text.strip()
    except Exception:
        return ""


def fetch_and_summarize(news_id: int, title: str, fallback_summary: str = "") -> str:
    """
    1. 先試爬全文（鉅亨為 SPA，通常拿不到完整內文）
    2. 全文夠長用全文，否則用 fallback_summary（API 內建摘要）
    3. 用 Gemini 整理成 2-3 個投資重點
    4. Gemini 不可用時直接回傳原始 summary
    """
    text = fetch_article_text(news_id)
    content = text if len(text) > 100 else fallback_summary
    if not content:
        return fallback_summary
    result = summarize_news(title, content)
    return result if result else fallback_summary
