import os
import json
import re
import time
from pathlib import Path
from google import genai
from google.genai import types
from dotenv import load_dotenv
from fetcher import download_audio
from clipper import inject_clips
from stock_validator import validate_episode

load_dotenv(dotenv_path=str(Path(__file__).parent / "../stock_bot/.env"))

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODELS = ["gemini-2.5-flash", "gemini-3.5-flash-lite"]

INDUSTRY_KEYWORDS = [
    "半導體", "AI", "人工智慧", "電動車", "新能源", "科技", "金融", "銀行",
    "保險", "房地產", "生技", "醫療", "零售", "電商", "雲端", "資安",
    "5G", "6G", "元宇宙", "加密貨幣", "比特幣", "ETF", "基金",
    "通膨", "升息", "降息", "Fed", "美聯儲", "央行", "景氣", "衰退",
    "原物料", "石油", "天然氣", "黃金", "鋼鐵", "航運", "航空",
    "遊戲", "軟體", "硬體", "晶片", "記憶體", "面板", "電池",
    "光伏", "太陽能", "風電", "核能", "儲能",
]

PROMPT = """你是專業財經播客分析師，請仔細聆聽這集 Podcast 的完整音頻內容，做出詳細的重點筆記。

請輸出 JSON 格式（不要加 markdown code block）：
{
  "summary": "4-6句話的完整摘要，涵蓋：本集主題、主持人的核心論點、對市場的判斷、以及最重要的結論或建議",
  "key_points": [
    {"text": "重點1（必須具體：包含數字、公司名稱、產品名稱、主持人的具體論點或預測）", "timestamp": 120},
    {"text": "重點2", "timestamp": 380},
    {"text": "重點3", "timestamp": 610},
    {"text": "重點4", "timestamp": 900},
    {"text": "重點5", "timestamp": 1200},
    {"text": "重點6", "timestamp": 1500},
    {"text": "重點7", "timestamp": 1800},
    {"text": "重點8", "timestamp": 2100}
  ],
  "tw_stocks": ["台股代號1", "台股代號2"],
  "us_stocks": ["美股代號1", "美股代號2"],
  "industries": ["產業或主題1", "產業或主題2"],
  "sentiment": "bullish / bearish / neutral",
  "notable_quotes": {
    "investing": ["與投資、選股、市場判斷、資產配置直接相關的金句（原話）"],
    "life": ["關於人生哲學、心態、生活方式的金句（原話）"]
  },
  "trading_insights": [
    {
      "title": "心法標題（5-10字，如「不要追高、等回檔再布局」）",
      "detail": "詳細說明主持人的具體操作邏輯、進出場條件、風險控管方式（2-4句，越具體越好）",
      "related_stocks": ["相關股票代號"],
      "timestamp": 450
    }
  ],
  "stock_views": {
    "股票代號": {
      "view": "主持人對此股票的具體看法，包含理由、目標價、風險等（1-3句）",
      "rating": "strong_buy / buy / neutral / sell / strong_sell"
    }
  },
  "industry_views": {
    "產業名稱": {
      "view": "主持人對此產業的具體看法、趨勢判斷、機會或風險（2-4句，盡量具體）",
      "outlook": "bullish / neutral / bearish",
      "catalysts": ["驅動因素1", "驅動因素2"],
      "risks": ["風險1", "風險2"],
      "related_stocks": ["相關股票代號"]
    }
  }
}

注意：
- tw_stocks 只填在台灣證券交易所或櫃買中心「實際掛牌交易」的股票代號（純數字，如 2330、00878）
  - 不要填年份、數量、規格數字
- us_stocks 只填在美國交易所「實際掛牌交易」的股票代碼（英文大寫，如 NVDA、AAPL）
  - 嚴格排除：產品型號（RTX 5070、M4、iPhone 16）、技術規格（DDR5、PCIe 5.0）、縮寫非股票（AI、VPN、CPU、GPU、RAM）、組織縮寫（Fed、ECB、TSMC產品線）等非股票內容
- key_points 要求 6-8 條，每條格式為 {"text": "...", "timestamp": 秒數}
  - text 必須具體有料：包含數字、公司全名、主持人的具體論點
  - timestamp 是該重點在音頻中「實際開始被討論」的秒數（整數）
    - 請仔細聆聽，給出盡量精確的時間點，誤差在 ±15 秒內
    - 不要平均分配，每條重點的時間點應反映其在音頻中的真實位置
  - 不要只寫「主持人討論了 XX」，要寫「主持人認為 XX，原因是 YY」
- trading_insights 每條也要附上精確的 timestamp（秒數）
- notable_quotes 分兩類：
  - investing：1-3 句與投資、選股、市場直接相關的金句
  - life：1-2 句人生哲學或生活心態的金句
  - 每類若沒有合適的就填空陣列，不要強行湊數
- sentiment 根據整集市場觀點判斷
- trading_insights 只填與「股票投資、選股、進出場、資產配置、風險控管」直接相關的操作心法
  - 必須是投資人可以實際參考執行的策略，例如：何時買、何時賣、停損設定、倉位控管、選股條件
  - 嚴格排除：生活哲學、旅遊技巧、飲食心得、人際關係、VPN 使用、廣告內容等非投資相關內容
  - 若本集沒有具體的股票操作心法，填空陣列即可
- stock_views 必須涵蓋 tw_stocks + us_stocks 中每一檔股票，不可遺漏
  - view：說明主持人如何提到這檔股票、給出什麼看法或評論（若只是順口一提，請寫「節目中簡短提及，未給出明確看法」）
  - rating 依主持人語氣判斷：strong_buy=強烈看好、buy=偏多、neutral=觀望/僅提及、sell=偏空、strong_sell=強烈看空
- industry_views 填節目中有明確觀點的產業或主題（如 AI、半導體、電動車等）
- outlook 依主持人對該產業的整體態度判斷
- catalysts 填主持人提到的利多驅動因素，risks 填提到的風險或隱憂
"""


def upload_audio_to_gemini(file_path: str) -> types.File | None:
    """上傳音頻到 Gemini Files API"""
    mime = "audio/mpeg" if file_path.endswith(".mp3") else "audio/mp4"
    print(f"    上傳音頻到 Gemini...")
    try:
        uploaded = client.files.upload(
            file=file_path,
            config=types.UploadFileConfig(mime_type=mime)
        )
        print(f"    ✓ 上傳完成: {uploaded.name}")
        return uploaded
    except Exception as e:
        print(f"    ✗ 上傳失敗: {e}")
        return None


def delete_gemini_file(file: types.File):
    try:
        client.files.delete(name=file.name)
    except Exception:
        pass


AUDIO_DIR = Path(__file__).parent / "data" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def _persistent_audio_path(episode: dict) -> Path:
    """依集數標題產生永久音頻儲存路徑"""
    import hashlib
    key = hashlib.md5(episode.get("title", "").encode()).hexdigest()[:10]
    suffix = ".mp3"
    if episode.get("audio_url", "").endswith(".m4a"):
        suffix = ".m4a"
    return AUDIO_DIR / f"{key}{suffix}"


def summarize_with_audio(episode: dict) -> dict:
    """用 Gemini 直接聆聽音頻做摘要"""
    audio_path = None
    gemini_file = None
    used_cache = False

    try:
        # 1. 下載音頻（若已有永久存檔則直接使用）
        persist_path = _persistent_audio_path(episode)
        if persist_path.exists():
            audio_path = str(persist_path)
            print(f"    ✓ 使用已存音頻: {persist_path.name}")
            used_cache = True
        else:
            print(f"    下載音頻: {episode['audio_url'][:60]}...")
            audio_path = download_audio(episode["audio_url"])

        if not audio_path:
            raise ValueError("音頻下載失敗")

        # 2. 上傳到 Gemini
        gemini_file = upload_audio_to_gemini(audio_path)
        if not gemini_file:
            raise ValueError("Gemini 上傳失敗")

        # 3. AI 聆聽並分析（每個 model 最多重試 2 次，503 自動降級）
        print(f"    Gemini 聆聽分析中...")
        last_err = None
        result = None
        for model in MODELS:
            for attempt in range(1, 3):
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=[
                            types.Part.from_uri(
                                file_uri=gemini_file.uri,
                                mime_type=gemini_file.mime_type,
                            ),
                            PROMPT,
                        ],
                    )
                    raw = response.text.strip()
                    raw = re.sub(r"^```json\s*", "", raw)
                    raw = re.sub(r"\s*```$", "", raw)
                    result = json.loads(raw)
                    result["analysis_source"] = "audio"
                    result["model_used"] = model
                    print(f"    ✓ 音頻分析完成（{model}）")
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    is_503 = "503" in str(e) or "UNAVAILABLE" in str(e)
                    if attempt == 1 and not is_503:
                        wait = 15
                        print(f"    ⚠ [{model}] 第 {attempt} 次失敗，{wait}s 後重試...")
                        time.sleep(wait)
                    elif attempt == 1 and is_503:
                        print(f"    ⚠ [{model}] 503 過載，直接重試一次...")
                        time.sleep(10)
            if result:
                break
            if "503" in str(last_err) or "UNAVAILABLE" in str(last_err):
                print(f"    ⚠ [{model}] 持續 503，降級到下一個 model...")

        if not result:
            raise last_err

        # 驗證股票代號
        print(f"    驗證股票代號...")
        validate_episode(result)

        # 剪輯每個重點的音頻片段，直接注入 clip_url
        merged = {**episode, **result}
        inject_clips(merged, audio_path)
        result["key_points"] = merged.get("key_points", result.get("key_points", []))
        result["trading_insights"] = merged.get("trading_insights", result.get("trading_insights", []))

    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
            print(f"    ⏳ Gemini 配額耗盡，標記為 pending（明日重設後自動補跑）")
        elif "503" in err_msg or "UNAVAILABLE" in err_msg:
            print(f"    ⏳ Gemini 伺服器過載，標記為 pending")
        else:
            print(f"    ✗ 音頻分析失敗: {e}，標記為 pending")
        result = {"analysis_source": "pending", "pending_reason": err_msg[:120]}

    finally:
        # 保留音頻：把暫存檔移到永久目錄
        if audio_path and os.path.exists(audio_path) and not used_cache:
            import shutil
            persist_path = _persistent_audio_path(episode)
            shutil.move(audio_path, str(persist_path))
            size_mb = persist_path.stat().st_size / 1024 / 1024
            print(f"    ✓ 音頻已保存: {persist_path.name} ({size_mb:.1f}MB)")
        # 刪除 Gemini 上的暫存檔案（節省配額）
        if gemini_file:
            delete_gemini_file(gemini_file)

    return {**episode, **result}


def summarize_with_description(episode: dict) -> dict:
    """fallback：用描述文字做摘要"""
    prompt = f"""根據以下 Podcast 集數資訊（標題 + 節目描述），產出繁體中文摘要。

節目：{episode['podcast']}
標題：{episode['title']}
描述：{episode['description']}

輸出 JSON（不要加 markdown code block）：
{{
  "summary": "根據標題和描述推測的摘要",
  "key_points": ["重點1", "重點2"],
  "tw_stocks": [],
  "us_stocks": [],
  "industries": [],
  "sentiment": "neutral",
  "notable_quotes": []
}}"""
    try:
        response = client.models.generate_content(model=MODELS[0], contents=prompt)
        raw = re.sub(r"^```json\s*", "", response.text.strip())
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception:
        return {
            "summary": episode.get("description", "（無法取得摘要）")[:200],
            "key_points": [],
            "tw_stocks": [],
            "us_stocks": [],
            "industries": [kw for kw in INDUSTRY_KEYWORDS if kw in episode.get("title", "") + episode.get("description", "")][:5],
            "sentiment": "neutral",
            "notable_quotes": [],
        }


def summarize_all(episodes: list[dict], existing: list[dict] = None) -> list[dict]:
    """
    批次分析集數。
    existing: 已存檔的舊資料，有 analysis_source == 'audio' 的直接沿用，
              'pending' 的重新嘗試音頻分析。
    """
    # 建立 title → 已完成分析的 dict
    done = {}
    if existing:
        for ep in existing:
            if ep.get("analysis_source") == "audio":
                done[ep["title"]] = ep

    results = []
    pending_count = 0
    for i, ep in enumerate(episodes, 1):
        title = ep["title"]

        # 已有音頻分析 → 直接沿用
        if title in done:
            print(f"\n  [{i}/{len(episodes)}] ✓ 已分析（沿用）→ {title[:40]}")
            results.append(done[title])
            continue

        # 需要（重新）分析
        if not ep.get("audio_url"):
            print(f"\n  [{i}/{len(episodes)}] ⚠ 無音頻 URL，跳過 → {title[:40]}")
            results.append({**ep, "analysis_source": "pending", "pending_reason": "no audio url"})
            pending_count += 1
            continue

        print(f"\n  [{i}/{len(episodes)}] 🎧 音頻分析 → {title[:40]}...")
        result = summarize_with_audio(ep)
        results.append(result)
        if result.get("analysis_source") == "pending":
            pending_count += 1

    if pending_count:
        print(f"\n  ⚠ {pending_count} 集待補跑（pending），下次執行將自動重試")
    return results
