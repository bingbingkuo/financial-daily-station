"""
futures_signal.py — 微型台指期（微台，MXF 近月）波段進出場訊號判斷

⚠️ 資料來源限制：
微型台指期貨本身沒有公開免費的歷史 K 線資料源，本模組以「加權指數現貨
(^TWII)」的日K / 60分K 走勢作為訊號代理（兩者走勢高度連動，僅有正常
價差）。所有判斷出來的點位都是「加權指數現貨點位」，實際下單前務必自行
比對券商軟體上微台近月合約的真實報價與圖形。本模組只負責產生訊號，
不會、也不能自動下單。

策略依據：MTX-85 Swing Framework
  日K 趨勢濾網 → 60分K 拉回轉折進場 → 金字塔加碼(停損移保本) → 雙軌停利/移動出場
"""

import numpy as np
import pandas as pd
import yfinance as yf

TICKER = "^TWII"      # 加權指數現貨，作為微台走勢代理
POINT_VALUE = 10      # 微台每點 10 元


# ── 資料抓取 ──────────────────────────────────────────────

def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()


def fetch_daily(period: str = "1y") -> pd.DataFrame:
    df = yf.download(TICKER, period=period, interval="1d", progress=False, auto_adjust=False)
    return _flatten(df)


def fetch_60m(period: str = "60d") -> pd.DataFrame:
    df = yf.download(TICKER, period=period, interval="60m", progress=False, auto_adjust=False)
    return _flatten(df)


# ── 日K 趨勢濾網 ──────────────────────────────────────────

def daily_trend(df: pd.DataFrame) -> dict:
    """回傳日K多空判定：20MA 斜率 + MACD 柱狀體"""
    close = df["Close"]
    ma20 = close.rolling(20).mean()
    ma20_slope = ma20.iloc[-1] - ma20.iloc[-6]

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - macd_signal

    last_close, last_ma20, last_hist = close.iloc[-1], ma20.iloc[-1], hist.iloc[-1]

    if last_close > last_ma20 and ma20_slope > 0 and last_hist > 0:
        trend = "bull"
    elif last_close < last_ma20 and ma20_slope < 0 and last_hist < 0:
        trend = "bear"
    else:
        trend = "range"

    return {
        "trend": trend,
        "close": last_close,
        "ma20": last_ma20,
        "ma20_slope": ma20_slope,
        "macd_hist": last_hist,
    }


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ── 60分K 結構判斷 ────────────────────────────────────────

def find_pivots(df: pd.DataFrame, window: int = 3):
    """找 60分K 的 pivot high / pivot low（左右各 window 根都更低/更高才算數）"""
    highs, lows = df["High"].values, df["Low"].values
    n = len(df)
    pivot_highs, pivot_lows = [], []
    for i in range(window, n - window):
        seg_h = highs[i - window:i + window + 1]
        if highs[i] == seg_h.max() and np.argmax(seg_h) == window:
            pivot_highs.append((i, highs[i]))
        seg_l = lows[i - window:i + window + 1]
        if lows[i] == seg_l.min() and np.argmin(seg_l) == window:
            pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows


def detect_60m_structure(df: pd.DataFrame, direction: str, lookback: int = 40, window: int = 3) -> dict:
    """
    判斷是否符合「底底高、過前高」(多) 或「頭頭低、破前低」(空) 的 N 字結構，
    並判斷目前是否正拉回測試頸線/20MA支撐（阻力）。
    """
    d = df.tail(lookback).reset_index(drop=True)
    pivot_highs, pivot_lows = find_pivots(d, window)
    out = {"structure_ok": False, "pullback_ok": False, "support": None, "resistance": None}
    if len(d) < 20:
        return out

    last_close = d["Close"].iloc[-1]
    ma20 = d["Close"].rolling(20).mean().iloc[-1]

    if direction == "long":
        if len(pivot_lows) >= 2 and len(pivot_highs) >= 1:
            (i1, low1), (i2, low2) = pivot_lows[-2], pivot_lows[-1]
            highs_between = [h for idx, h in pivot_highs if i1 <= idx <= i2]
            if low2 > low1 and highs_between:
                neckline = max(highs_between)
                broke = i2 + 1 < len(d) and (d["Close"].iloc[i2 + 1:] > neckline).any()
                out["structure_ok"] = bool(broke)
                if broke:
                    support = max(neckline, low2)
                    near_support = last_close <= support * 1.01 and d["Low"].tail(3).min() >= support * 0.995
                    near_ma20 = pd.notna(ma20) and abs(last_close - ma20) / ma20 <= 0.006
                    out["pullback_ok"] = bool(near_support or near_ma20)
                    out["support"] = support
    else:  # short，對稱邏輯
        if len(pivot_highs) >= 2 and len(pivot_lows) >= 1:
            (i1, high1), (i2, high2) = pivot_highs[-2], pivot_highs[-1]
            lows_between = [l for idx, l in pivot_lows if i1 <= idx <= i2]
            if high2 < high1 and lows_between:
                neckline = min(lows_between)
                broke = i2 + 1 < len(d) and (d["Close"].iloc[i2 + 1:] < neckline).any()
                out["structure_ok"] = bool(broke)
                if broke:
                    resistance = min(neckline, high2)
                    near_resistance = last_close >= resistance * 0.99 and d["High"].tail(3).max() <= resistance * 1.005
                    near_ma20 = pd.notna(ma20) and abs(last_close - ma20) / ma20 <= 0.006
                    out["pullback_ok"] = bool(near_resistance or near_ma20)
                    out["resistance"] = resistance
    return out


def structural_break(df: pd.DataFrame, direction: str, lookback: int = 40, window: int = 3) -> bool:
    """
    判斷波段結構是否被破壞：多單看是否跌破最近一個 60分K pivot低點，
    空單看是否突破最近一個 60分K pivot高點（頭頭低轉底底高 / 底底高轉頭頭低）。
    """
    d = df.tail(lookback).reset_index(drop=True)
    pivot_highs, pivot_lows = find_pivots(d, window)
    last_close = d["Close"].iloc[-1]
    if direction == "long":
        if not pivot_lows:
            return False
        _, last_low = pivot_lows[-1]
        return bool(last_close < last_low)
    else:
        if not pivot_highs:
            return False
        _, last_high = pivot_highs[-1]
        return bool(last_close > last_high)


def strong_daily_candle(df_daily: pd.DataFrame, direction: str) -> bool:
    """日K是否收出順勢方向的強勢實體K棒（實體佔比大、收在當日高/低附近）"""
    bar = df_daily.iloc[-1]
    o, h, l, c = bar["Open"], bar["High"], bar["Low"], bar["Close"]
    body = abs(c - o)
    rng = max(h - l, 1e-9)
    if direction == "long":
        return bool(c > o and body / c >= 0.006 and (h - c) <= 0.25 * rng)
    else:
        return bool(c < o and body / c >= 0.006 and (c - l) <= 0.25 * rng)


def detect_reversal_candle(bar_prev: pd.Series, bar_last: pd.Series, direction: str) -> bool:
    """判斷最後一根 60分K 是否為轉折K棒（吞噬 or 長影線槌子/上吊）"""
    o, h, l, c = bar_last["Open"], bar_last["High"], bar_last["Low"], bar_last["Close"]
    body = abs(c - o)
    rng = max(h - l, 1e-9)
    if direction == "long":
        bullish = c > o and body / rng >= 0.4
        engulf = c > bar_prev["High"]
        hammer = (min(o, c) - l) >= 1.5 * body if body > 0 else False
        return bool(bullish and (engulf or hammer))
    else:
        bearish = c < o and body / rng >= 0.4
        engulf = c < bar_prev["Low"]
        hammer = (h - max(o, c)) >= 1.5 * body if body > 0 else False
        return bool(bearish and (engulf or hammer))


# ── 綜合判斷：依目前部位狀態決定下一步動作 ─────────────────

def evaluate(state: dict, df_daily: pd.DataFrame, df60: pd.DataFrame) -> dict:
    """
    根據 futures_state.json 的目前部位階段，回傳下一步建議：
      no_signal / hold / entry / add / profit_take_1 / trail_exit / stop_hit
    """
    trend_info = daily_trend(df_daily)
    atr14 = atr(df_daily, 14).iloc[-1]
    ma10 = df_daily["Close"].rolling(10).mean().iloc[-1]

    last_price = float(df60["Close"].iloc[-1]) if len(df60) else float(df_daily["Close"].iloc[-1])
    last_bar, prev_bar = df60.iloc[-1], df60.iloc[-2]

    result = {"action": "no_signal", "price": last_price, "trend": trend_info, "detail": ""}

    stage = state.get("stage", "flat")
    direction = state.get("direction")
    stop_price = state.get("stop_price")

    # 1) 有部位時，優先檢查是否已觸及停損（以60分K高低價判斷是否曾經觸價）
    if stage != "flat" and stop_price is not None:
        hit = (last_bar["Low"] <= stop_price) if direction == "long" else (last_bar["High"] >= stop_price)
        if hit:
            result.update(
                action="stop_hit", direction=direction,
                detail=f"價格觸及停損 {stop_price:.0f}，依 OCO 紀律建議出場全部 {state.get('lots', 0)} 口",
            )
            return result

    # 2) 有部位（base/pyramid 皆同一套邏輯）→ 依序檢查加碼／雙軌停利／移動出場
    if stage in ("base", "pyramid"):
        entry_price = state["entry_price"]
        lots = state.get("lots", 0)
        max_lots = state.get("max_lots", 6)  # 未設定時預設沿用原文件「主升段極限6口」
        profit_pts = (last_price - entry_price) if direction == "long" else (entry_price - last_price)

        # 2a) 加碼條件：浮盈達標 + (60分K創波段新高/新低 或 日K收強勢實體K)，且未超過你設定的口數上限
        struct = detect_60m_structure(df60, direction)
        recent = df60["Close"].iloc[-11:-1]
        breakout = (last_price > recent.max()) if direction == "long" else (last_price < recent.min())
        breakout = breakout or strong_daily_candle(df_daily, direction)
        if profit_pts >= 600 and struct["structure_ok"] and breakout:
            next_lots = lots + 2
            if next_lots <= max_lots:
                warn = ""
                if next_lots > 6:
                    warn = ("\n⚠️ 已超過原策略「主升段極限6口」，愈接近原文件定義的「禁區」高槓桿範圍，"
                            "請務必先確認保證金餘額足夠承受逆向波動、且行情仍是超強單邊，才執行。")
                result.update(
                    action="add", direction=direction,
                    detail=f"浮盈 {profit_pts:.0f} 點且出現新突破，可加碼2口(共{next_lots}口)，"
                           f"並將全部{next_lots}口停損上移至目前成本價 {entry_price:.0f}（保本）{warn}",
                )
                return result
            # 已達你設定的口數上限，不再建議加碼，往下繼續檢查停利/出場條件

        # 2b) 雙軌停利：至少已加碼過（4口以上）才有「留倉續抱」的意義
        if lots >= 4 and not state.get("partial_taken") and profit_pts >= 1200:
            round_level = round(last_price / 500) * 500
            result.update(
                action="profit_take_1", direction=direction,
                detail=f"浮盈 {profit_pts:.0f} 點，可於整數關卡({round_level:.0f})附近或日K前高反壓，"
                       f"市價停利2口，剩餘{max(lots - 2, 0)}口移動停利續抱",
            )
            return result

        # 2c) 移動出場：原文件這條規則是給「第一軌停利後、續抱的剩餘部位」用的移動停利，
        #     還沒觸發過 profit_take_1（partial_taken）之前，下檔保護一律只看 stop_price，
        #     避免部位還沒真的獲利了結過、就被10MA雜訊洗出場。
        if state.get("partial_taken"):
            entry_date = state.get("entry_date")
            since = df_daily[df_daily.index >= pd.Timestamp(entry_date)] if entry_date else df_daily
            if direction == "long":
                extreme = since["High"].max()
                retrace = extreme - last_price
                trail_break = df_daily["Close"].iloc[-1] < ma10
            else:
                extreme = since["Low"].min()
                retrace = last_price - extreme
                trail_break = df_daily["Close"].iloc[-1] > ma10
            atr_break = retrace >= 2 * atr14
            struct_break = structural_break(df60, direction)

            if trail_break or atr_break or struct_break:
                ma_reason = "日K跌破10MA" if direction == "long" else "日K收盤重新站上10MA"
                reasons = []
                if trail_break:
                    reasons.append(ma_reason)
                if atr_break:
                    reasons.append(f"自波段極值回檔達2×ATR(≈{2 * atr14:.0f}點)")
                if struct_break:
                    reasons.append("60分K結構破壞(" + ("跌破前波低點" if direction == "long" else "突破前波反彈高點") + ")")
                result.update(
                    action="trail_exit", direction=direction,
                    detail=f"觸發移動出場條件（{'、'.join(reasons)}），建議剩餘部位全部出場",
                )
                return result

        result.update(action="hold", direction=direction, detail=f"部位持有中({lots}口)，浮盈 {profit_pts:.0f} 點")
        return result

    # 4) 空手 → 尋找進場訊號（依日K趨勢決定只找多或只找空）
    for dir_try in ("long", "short"):
        if dir_try == "long" and trend_info["trend"] != "bull":
            continue
        if dir_try == "short" and trend_info["trend"] != "bear":
            continue
        struct = detect_60m_structure(df60, dir_try)
        if struct["structure_ok"] and struct["pullback_ok"] and detect_reversal_candle(prev_bar, last_bar, dir_try):
            level = struct.get("support") or struct.get("resistance")
            stop = (level - 50) if dir_try == "long" else (level + 50)
            result.update(
                action="entry", direction=dir_try, price=last_price,
                detail=f"{'多方' if dir_try == 'long' else '空方'}訊號成立：拉回頸線/20MA出現轉折K，"
                       f"建議建立底倉2口，進場價約 {last_price:.0f}，初始停損 {stop:.0f}",
            )
            return result

    result.update(detail=f"日K趨勢：{trend_info['trend']}，尚無符合條件的60分K進場訊號")
    return result
