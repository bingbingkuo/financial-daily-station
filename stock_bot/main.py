"""
main.py — 盤前分析機器人主程式
執行方式：python main.py
排程建議：每天 08:30（台股盤前）執行

功能：
  1. 總經分析：GDP、CPI、失業率、零售銷售、美債殖利率、美元指數
  2. 前三大強勢族群（美股 + 台股）+ 題材分析
  3. 持股新聞 / 法說會 / 財報時間
"""

import sys
import traceback
from datetime import datetime

from modules.macro import get_all_macro, format_macro_section
from modules.sectors import get_us_top_sectors, get_tw_top_sectors, format_sectors_section
from modules.portfolio import get_all_us_portfolio, get_all_tw_portfolio, format_portfolio_section
from modules.notifier import send_telegram


def build_report() -> str:
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    sections = [f"\n📈 盤前分析日報  {now}"]
    sections.append("=" * 30)

    # 1. 總經數據
    print("📊 正在抓取總經數據...")
    try:
        macro_data = get_all_macro()
        sections.append(format_macro_section(macro_data))
    except Exception as e:
        sections.append(f"📊 【總經環境】\n  ⚠️ 抓取失敗：{e}")

    sections.append("")

    # 2. 前三大族群
    print("🏆 正在分析族群強弱...")
    try:
        us_sectors = get_us_top_sectors(3)
    except Exception as e:
        print(f"  ⚠️ 美股族群抓取失敗：{e}")
        us_sectors = []

    try:
        tw_sectors = get_tw_top_sectors(3)
    except Exception as e:
        print(f"  ⚠️ 台股族群抓取失敗：{e}")
        tw_sectors = []

    sections.append(format_sectors_section(us_sectors, tw_sectors))
    sections.append("")

    # 3. 持股動態
    print("📁 正在取得持股新聞與財報資訊...")
    try:
        us_data = get_all_us_portfolio()
    except Exception as e:
        print(f"  ⚠️ 美股持倉資料失敗：{e}")
        us_data = []

    try:
        tw_data = get_all_tw_portfolio()
    except Exception as e:
        print(f"  ⚠️ 台股持倉資料失敗：{e}")
        tw_data = []

    sections.append(format_portfolio_section(us_data, tw_data))

    return "\n".join(sections)


def main():
    print("🚀 盤前分析機器人啟動...")
    try:
        report = build_report()
        print("\n" + report)
        print("\n📤 正在發送 Telegram 通知...")
        success = send_telegram(report)
        if success:
            print("✅ 已成功推送至 Telegram！")
        else:
            print("⚠️  Telegram 未設定，報告已印出於終端機")
    except Exception:
        print("❌ 執行發生錯誤：")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
