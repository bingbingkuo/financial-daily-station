"""
main_tw.py — 台股盤前分析報告
執行時間：台灣時間 週一至週五 08:30
內容：台灣總經 / 台股前三族群+領頭股 / 台股持倉動態
"""

import sys
import traceback
from datetime import datetime

from modules.macro    import get_tw_macro, format_tw_macro
from modules.sectors  import get_tw_top_sectors, format_tw_sectors, enrich_tw_sectors_news
from modules.portfolio import (get_all_tw_portfolio, format_tw_portfolio,
                               get_portfolio_schedule, format_schedule,
                               get_tw_flagged_stocks, format_tw_flags,
                               fetch_cnyes_batch)
from config import TW_STOCKS, US_STOCKS
from modules.notifier import send_telegram


def build_tw_report() -> str:
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    lines = [f"\n🇹🇼 台股盤前分析  {now}", "=" * 30]

    # 1. 台灣總經
    print("📊 抓取台灣總經數據...")
    try:
        lines.append(format_tw_macro(get_tw_macro()))
    except Exception as e:
        lines.append(f"📊 【台灣總經環境】\n  ⚠️ 失敗：{e}")

    lines.append("")

    # 預先抓取鉅亨新聞 batch（族群 + 持倉共用，只抓一次）
    print("📰 抓取鉅亨網台股新聞...")
    try:
        cnyes_batch = fetch_cnyes_batch(5)
    except Exception:
        cnyes_batch = []

    # 2. 台股前三族群 + 領頭股 + 鉅亨新聞
    print("🏆 分析台股族群強弱...")
    try:
        tw_sectors = get_tw_top_sectors(3)
        tw_sectors = enrich_tw_sectors_news(tw_sectors, cnyes_batch)
        lines.append(format_tw_sectors(tw_sectors))
    except Exception as e:
        lines.append(f"🏆 【台股前三大強勢族群】\n  ⚠️ 失敗：{e}")

    lines.append("")

    # 3. 持倉近期行程
    print("📅 取得持倉財報/法說會行程...")
    try:
        schedule = get_portfolio_schedule(TW_STOCKS, {})
        lines.append(format_schedule(schedule))
    except Exception as e:
        lines.append(f"📅 【持倉近期行程】\n  ⚠️ 失敗：{e}")

    lines.append("")

    # 3-5. 持倉注意 / 處置警示
    print("🚨 查詢持倉注意/處置狀態...")
    try:
        flagged   = get_tw_flagged_stocks(TW_STOCKS)
        flag_text = format_tw_flags(flagged)
        if flag_text:
            lines.append(flag_text)
            lines.append("")
    except Exception as e:
        pass   # 靜默失敗，不影響主報告

    # 4. 台股持倉新聞（鉅亨 + Gemini 摘要）
    print("📁 取得台股持倉新聞...")
    try:
        tw_data = get_all_tw_portfolio(cnyes_batch)
        lines.append(format_tw_portfolio(tw_data))
    except Exception as e:
        lines.append(f"📁 【台股持倉動態】\n  ⚠️ 失敗：{e}")

    return "\n".join(lines)


def main():
    print("🚀 台股盤前報告啟動...")
    try:
        report  = build_tw_report()
        print("\n" + report)
        print("\n📤 發送 Telegram...")
        ok = send_telegram(report)
        print("✅ 台股報告推送成功！" if ok else "⚠️  Telegram 未設定")
    except Exception:
        traceback.print_exc()
        sys.exit(0)  # 保持 exit 0，避免 launchd throttle


if __name__ == "__main__":
    main()
