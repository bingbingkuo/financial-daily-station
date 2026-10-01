"""
main_futures.py — 微型台指期（微台，MXF 近月）波段進出場訊號監測

⚠️ 僅供訊號提醒，不會自動下單。資料以加權指數現貨(^TWII)作為走勢代理，
   實際下單前請自行核對券商軟體上微台近月合約的真實報價與圖形。

部位狀態由 futures_state.json 手動維護（stage/direction/lots/entry_price/
entry_date/stop_price/partial_taken），本腳本只讀取該檔案來判斷下一步該
提示什麼；執行後你若依建議實際下單，需自行回去更新 futures_state.json。

執行時機：建議比照 main_tw.py 用 launchd 於盤中（日盤 09:00-13:30，
如需涵蓋夜盤請自行擴充排程）定時執行，只有出現「可執行動作」才會推播
Telegram，避免盤中洗版；沒有新動作時只印出目前狀態到 log。
"""

import json
import os
import sys
import traceback
from datetime import datetime

from modules.futures_signal import fetch_daily, fetch_60m, evaluate, POINT_VALUE
from modules.notifier import send_telegram

STATE_FILE = os.path.join(os.path.dirname(__file__), "futures_state.json")

# 需要推播提醒的動作（其餘 no_signal / hold 只記錄不推播）
ALERT_ACTIONS = {"entry", "add", "profit_take_1", "trail_exit", "stop_hit"}

ACTION_LABELS = {
    "entry": "🟡 建議進場",
    "add": "🟠 建議加碼",
    "profit_take_1": "🟢 建議停利(第一軌)",
    "trail_exit": "🔴 移動出場觸發",
    "stop_hit": "🔴 停損觸價",
    "hold": "⏸ 持有中",
    "no_signal": "⚪ 無訊號",
}


def load_state() -> dict:
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_alert_marker(state: dict, alert_key: str) -> None:
    """只更新 last_alert / updated_at，不動使用者手動維護的部位欄位"""
    state["last_alert"] = alert_key
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def build_message(result: dict, state: dict) -> str:
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    label = ACTION_LABELS.get(result["action"], result["action"])
    dir_label = {"long": "做多", "short": "做空"}.get(result.get("direction") or state.get("direction"), "")
    lines = [
        f"📟 微台波段訊號  {now}",
        "=" * 26,
        f"{label}｜{dir_label}",
        result["detail"],
        f"現價代理(加權指數)：{result['price']:.0f}",
        f"日K趨勢：{result['trend']['trend']}（MA20={result['trend']['ma20']:.0f}, MACD柱={result['trend']['macd_hist']:.1f}）",
    ]
    if state.get("lots"):
        lines.append(f"目前部位：{state['lots']}口 / 成本 {state.get('entry_price')}")
    lines.append("")
    lines.append("⚠️ 訊號代理自加權指數現貨，非期貨即時報價；下單前請核對券商軟體實際報價，本工具不會自動下單。")
    return "\n".join(lines)


def main():
    print("🚀 微台波段訊號監測啟動...")
    try:
        state = load_state()
        df_daily = fetch_daily()
        df60 = fetch_60m()

        result = evaluate(state, df_daily, df60)
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] action={result['action']} detail={result['detail']}")

        if result["action"] in ALERT_ACTIONS:
            # 避免同一個訊號每次排程重複推播：用 action+價位(取整) 當作去重 key
            alert_key = f"{result['action']}:{round(result['price'] / 5) * 5}"
            if state.get("last_alert") != alert_key:
                msg = build_message(result, state)
                ok = send_telegram(msg)
                print("✅ 推播成功" if ok else "⚠️ Telegram 未設定，訊息已印出")
                save_alert_marker(state, alert_key)
            else:
                print("↩️ 與上次推播內容相同，略過重複推播")
        else:
            print("（無需推播的一般狀態）")

    except Exception:
        traceback.print_exc()
        sys.exit(0)  # 保持 exit 0，避免 launchd throttle


if __name__ == "__main__":
    main()
