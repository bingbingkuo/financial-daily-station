#!/usr/bin/env python3
"""播客財經摘要主程式"""
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from fetcher import fetch_all_podcasts
from summarizer import summarize_all
from mailer import send_digest
from tracker import update_watchlist

# 側欄外殼跟 stock_bot 共用（docs/ 是兩個專案共用的靜態網站）。
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stock_bot"))
from modules.site_shell import render_report_page

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_PATH = Path(__file__).parent / "config.json"
ALL_EPISODES_PATH = DATA_DIR / "all_episodes.json"

_DOCS_ROOT = os.path.join(os.path.dirname(__file__), "..", "docs")
_DOCS_DIR  = os.path.join(_DOCS_ROOT, "podcast")

# 跟 web/app.py 的首頁同一份模板（episode 清單 + 搜尋 + 展開詳情），
# 用 static_export=True 關掉需要 Flask 後端的功能（頂部導覽列、即時股價彈窗）。
_WEB_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "web", "templates")
_jinja_env = Environment(loader=FileSystemLoader(_WEB_TEMPLATES_DIR))


def _publish_to_docs(date_str: str) -> None:
    """把集數清單發布到 docs/podcast/（比照 stock_bot 的 main_scout.py）。"""
    try:
        os.makedirs(_DOCS_DIR, exist_ok=True)
        formatted   = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        active_file = f"{formatted}.html"

        episodes = load_all_episodes()
        template = _jinja_env.get_template("index.html")
        report_html = template.render(episodes=episodes, current_date=formatted, static_export=True)

        manifest_path = os.path.join(_DOCS_DIR, "manifest.json")
        if os.path.exists(manifest_path):
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        else:
            manifest = []

        entry = {"date": formatted, "file": active_file}
        if not any(e["date"] == formatted for e in manifest):
            manifest.insert(0, entry)
            manifest.sort(key=lambda e: e["date"], reverse=True)
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

        manifest_js_path = os.path.join(_DOCS_DIR, "manifest.js")
        with open(manifest_js_path, "w", encoding="utf-8") as f:
            f.write("window.PODCAST_MANIFEST = ")
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write(";\n")

        # 包上跟 docs/index.html 一致的側欄／頂部列外殼（比照台股/美股選股雷達）。
        page_html = render_report_page(
            docs_root=_DOCS_ROOT, kind="pod", date=formatted,
            active_file=active_file, report_html=report_html,
        )
        dest = os.path.join(_DOCS_DIR, active_file)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(page_html)
        shutil.copy(dest, os.path.join(_DOCS_DIR, "latest.html"))

        _publish_watchlist_and_trends(formatted)

        print(f"🌐 已發布到 docs/podcast/{formatted}.html")
    except Exception as e:
        print(f"⚠️  docs 發布失敗：{e}")


def _publish_watchlist_and_trends(formatted_date: str) -> None:
    """發布「股票追蹤」「產業趨勢」——跟每日摘要同一份固定導覽列可以互相連結，
    不進歷史存檔（沒有日期分頁概念，永遠是最新狀態）。"""
    watchlist_path  = DATA_DIR / "watchlist.json"
    industries_path = DATA_DIR / "industries.json"

    watchlist = {}
    if watchlist_path.exists():
        with open(watchlist_path, encoding="utf-8") as f:
            watchlist = json.load(f)
    wl_template = _jinja_env.get_template("watchlist.html")
    wl_html = wl_template.render(watchlist=watchlist, static_export=True)
    wl_page = render_report_page(
        docs_root=_DOCS_ROOT, kind="pod", date=formatted_date,
        active_file="watchlist.html", report_html=wl_html,
    )
    with open(os.path.join(_DOCS_DIR, "watchlist.html"), "w", encoding="utf-8") as f:
        f.write(wl_page)

    industries = {}
    if industries_path.exists():
        with open(industries_path, encoding="utf-8") as f:
            industries = json.load(f)
    # 依最新提及日期排序（新→舊），比照 web/app.py 的 /trends 路由
    def _latest_date(item):
        return max((m.get("date", "") for m in item[1].get("mentions", [])), default="")
    industries = dict(sorted(industries.items(), key=_latest_date, reverse=True))
    tr_template = _jinja_env.get_template("trends.html")
    tr_html = tr_template.render(industries=industries, static_export=True)
    tr_page = render_report_page(
        docs_root=_DOCS_ROOT, kind="pod", date=formatted_date,
        active_file="trends.html", report_html=tr_html,
    )
    with open(os.path.join(_DOCS_DIR, "trends.html"), "w", encoding="utf-8") as f:
        f.write(tr_page)


def load_all_episodes() -> list[dict]:
    """載入所有歷史集數"""
    if ALL_EPISODES_PATH.exists():
        with open(ALL_EPISODES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def _ep_num(ep: dict) -> int:
    """從標題取出集數號碼，例如 'EP698 | 🎮' → 698"""
    import re
    m = re.search(r'EP(\d+)', ep.get("title", ""))
    return int(m.group(1)) if m else 0


def save_all_episodes(episodes: list[dict]):
    """儲存所有集數（依集數號碼由新到舊排序）"""
    episodes.sort(key=_ep_num, reverse=True)
    with open(ALL_EPISODES_PATH, "w", encoding="utf-8") as f:
        json.dump(episodes, f, ensure_ascii=False, indent=2)


def merge_episodes(existing: list[dict], new_episodes: list[dict]) -> tuple[list[dict], int]:
    """
    把新集數合併進現有清單。
    - 以 title 為唯一鍵
    - 若已存在且為 audio，保留舊的（不重複分析）
    - 若已存在但為 pending，用新的覆蓋
    回傳 (合併後清單, 新增/更新筆數)
    """
    by_title = {ep["title"]: ep for ep in existing}
    updated = 0

    for ep in new_episodes:
        title = ep["title"]
        old = by_title.get(title)
        if old is None:
            by_title[title] = ep
            updated += 1
        elif old.get("analysis_source") != "audio" and ep.get("analysis_source") == "audio":
            # pending → audio，更新
            by_title[title] = ep
            updated += 1
        # 已是 audio 則保留舊的，不覆蓋

    return list(by_title.values()), updated


def run(send_email: bool = False):
    print(f"\n{'='*50}")
    print(f"播客財經摘要  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*50)

    # 載入歷史資料
    all_eps = load_all_episodes()
    existing_by_title = {ep["title"]: ep for ep in all_eps}
    pending = [e for e in all_eps if e.get("analysis_source") == "pending"]
    done = [e for e in all_eps if e.get("analysis_source") == "audio"]
    print(f"\n歷史記錄：{len(done)} 集完成、{len(pending)} 集 pending")

    print("\n[1/3] 抓取 RSS 最新集數...")
    new_episodes = fetch_all_podcasts(str(CONFIG_PATH))

    # 加入尚未分析的 pending 集數一起補跑
    pending_to_retry = [
        e for e in pending
        if e["title"] not in {ep["title"] for ep in new_episodes}
    ]
    if pending_to_retry:
        print(f"  → 補跑 {len(pending_to_retry)} 集 pending")
    to_analyze = new_episodes + pending_to_retry

    # 已有 audio 分析的跳過
    to_analyze = [ep for ep in to_analyze
                  if existing_by_title.get(ep["title"], {}).get("analysis_source") != "audio"]

    if not to_analyze:
        print("  → 無需分析的新集數，略過分析步驟")
    else:
        # 補上 audio_url（pending 集數可能沒有）
        for ep in to_analyze:
            if not ep.get("audio_url") and ep["title"] in existing_by_title:
                ep["audio_url"] = existing_by_title[ep["title"]].get("audio_url", "")

        print(f"\n[2/3] AI 音頻分析（共 {len(to_analyze)} 集）...")
        analyzed = summarize_all(to_analyze, existing=None)

        all_eps, updated = merge_episodes(all_eps, analyzed)
        save_all_episodes(all_eps)
        audio_done = sum(1 for e in analyzed if e.get("analysis_source") == "audio")
        print(f"\n  ✓ 已儲存 all_episodes.json（新增/更新 {updated} 集，音頻完成 {audio_done} 集）")

        print("\n[股票追蹤] 更新 watchlist...")
        audio_analyzed = [e for e in analyzed if e.get("analysis_source") == "audio"]
        if audio_analyzed:
            update_watchlist(audio_analyzed)
            _publish_to_docs(datetime.now().strftime("%Y%m%d"))

        if send_email and audio_analyzed:
            print("\n[寄信] 發送 Email...")
            send_digest(audio_analyzed)

    print("\n✓ 完成！\n")


if __name__ == "__main__":
    send_email = "--email" in sys.argv  # 預設不寄，加 --email 才寄
    run(send_email=send_email)
