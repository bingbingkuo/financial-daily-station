"""
dark_theme.py — 把 email 版報告（淺色配色）轉成深色，供 docs/ 網頁發布使用。

email 版報告要相容各家 mail client，維持淺色配色不動；docs 網頁版要符合
財經日報站的深色主題，所以在發布前用這份對照表把顏色轉暗，兩邊互不影響。

key/value 都含屬性名（如 "background:#fff"）而非裸色碼，避免同一色碼在不同
屬性（背景 vs 文字）誤用同一套轉換 —— 例如 #fff 當背景要轉深色 surface，
當文字色（白字在深色徽章上）則要維持不變。
"""

# 台股／美股選股雷達報告共用的基礎色盤（兩份報告的 CSS 大多共用同一組色碼）。
BASE_DARK_COLOR_MAP = [
    # 卡片/區塊背景 → 深色 surface
    ("background:#fff",    "background:#161b22"),
    ("background:#fafafa", "background:#21262d"),
    ("background:#f4f4f4", "background:#0d1117"),
    ("background:#eceff1", "background:#21262d"),
    ("background:#fff8e1", "background:#161b22"),
    ("background:#bdbdbd", "background:#30363d"),
    # 淺色提示框 → 低透明度色塊
    ("background:#e8f5e9", "background:rgba(74,222,128,.12)"),
    ("background:#ffebee", "background:rgba(248,113,113,.12)"),
    ("background:#e3f2fd", "background:rgba(96,165,250,.12)"),
    ("background:#fff3e0", "background:rgba(251,146,60,.12)"),
    ("background:#f3e5f5", "background:rgba(192,132,252,.12)"),
    ("background:#fce4ec", "background:rgba(244,114,182,.12)"),
    # 邊框
    ("border-top:1px solid #eee",       "border-top:1px solid #30363d"),
    ("border-bottom:1px solid #eee",    "border-bottom:1px solid #30363d"),
    ("border-bottom:1px solid #f0f0f0", "border-bottom:1px solid #30363d"),
    ("border-right:1px solid #f0f0f0",  "border-right:1px solid #30363d"),
    ("border-bottom:1px solid #ffe082", "border-bottom:1px solid rgba(245,158,11,.3)"),
    ("border:1px solid #e65100",        "border:1px solid #fb923c"),
    ("border-left:4px solid #3f51b5",   "border-left:4px solid #5b6eea"),
    # 文字顏色 → 提高亮度以符合深色底
    ("color:#333",    "color:#e6edf3"),
    ("color:#444",    "color:#d0d7de"),
    ("color:#555",    "color:#c9d1d9"),
    ("color:#666",    "color:#9198a1"),
    ("color:#888",    "color:#7d8590"),
    ("color:#aaa",    "color:#6e7681"),
    ("color:#1a237e", "color:#8ab4f8"),
    ("color:#1565c0", "color:#60a5fa"),
    ("color:#0d47a1", "color:#60a5fa"),
    ("color:#1a0dab", "color:#60a5fa"),
    ("color:#2e7d32", "color:#4ade80"),
    ("color:#1b5e20", "color:#4ade80"),
    ("color:#c62828", "color:#f87171"),
    ("color:#b71c1c", "color:#f87171"),
    ("color:#e65100", "color:#fb923c"),
    ("color:#bf360c", "color:#fb923c"),
    ("color:#37474f", "color:#c9d1d9"),
    ("color:#6a1b9a", "color:#c4b5fd"),
]

# 美股選股雷達報告額外用到、台股報告沒有的色碼。
US_EXTRA_DARK_COLOR_MAP = [
    ("background:#ede7f6",              "background:rgba(167,139,250,.12)"),
    ("border-bottom:1px solid #ede7f6", "border-bottom:1px solid #30363d"),
    ("background:#faf8ff",              "background:#1f1b2e"),
    ("color:#4527a0",                   "color:#c4b5fd"),
    ("border-left:4px solid #3949ab",   "border-left:4px solid #6674e0"),
    ("background:#fff9c4",              "background:rgba(245,158,11,.15)"),
    ("color:#f57f17",                   "color:#fbbf24"),
    ("background:#fffde7",              "background:rgba(245,158,11,.10)"),
    ("color:#388e3c",                   "color:#4ade80"),
]


# podcast_digest/mailer.py 的摘要報告完全用 inline style（沒有 <style> 區塊），
# 色盤跟台股/美股報告不共用，所以整組獨立列出（不是疊加在 BASE 之上）。
PODCAST_DARK_COLOR_MAP = [
    ("background:#fff",    "background:#161b22"),
    ("background:#f1f5f9", "background:#21262d"),
    ("background:#f8fafc", "background:#21262d"),
    ("border:1px solid #e2e8f0",        "border:1px solid #30363d"),
    ("border-top:1px solid #f1f5f9",    "border-top:1px solid #30363d"),
    ("border-bottom:1px solid #f1f5f9", "border-bottom:1px solid #30363d"),
    ("border-bottom:1px solid #e2e8f0", "border-bottom:1px solid #30363d"),
    ("border-left:3px solid #e2e8f0",   "border-left:3px solid #30363d"),
    ("border-left:2px solid #e2e8f0",   "border-left:2px solid #30363d"),
    ("border-left:2px solid #cbd5e1",   "border-left:2px solid #30363d"),
    ("color:#94a3b8", "color:#7d8590"),
    ("color:#0f172a", "color:#e6edf3"),
    ("color:#1e293b", "color:#e6edf3"),
    ("color:#64748b", "color:#9198a1"),
    ("color:#cbd5e1", "color:#6e7681"),
    ("color:#475569", "color:#c9d1d9"),
    ("color:#16a34a", "color:#4ade80"),
    ("color:#dc2626", "color:#f87171"),
    ("color:#6b7280", "color:#9198a1"),
    ("color:#22c55e", "color:#4ade80"),
    ("color:#f97316", "color:#fb923c"),
]


# 台股族群強弱（均線分析）報告額外用到、跟台股/美股選股雷達色碼不完全重疊的部分。
MA_EXTRA_DARK_COLOR_MAP = [
    ("background:#f0f0f0", "background:#21262d"),
    ("border:1px solid #eee",           "border:1px solid #30363d"),
    ("border-bottom:1px solid #f5f5f5", "border-bottom:1px solid #30363d"),
    ("border-bottom:1px solid #eee",    "border-bottom:1px solid #30363d"),
    ("border-bottom:1px dashed #ccc",   "border-bottom:1px dashed #6e7681"),
    ("border:1px solid #ffcdd2",        "border:1px solid rgba(248,113,113,.35)"),
    ("border:1px solid #c8e6c9",        "border:1px solid rgba(74,222,128,.35)"),
    ("border-color:#ffe082",            "border-color:rgba(245,158,11,.4)"),
    ("color:#999", "color:#9198a1"),
    ("color:#777", "color:#9198a1"),
]


def apply_dark_theme(html: str, extra_map: list[tuple[str, str]] | None = None) -> str:
    """把報告 HTML 的淺色配色轉成深色，只用於 docs 網頁發布，不動 email 版本。"""
    for old, new in BASE_DARK_COLOR_MAP + (extra_map or []):
        html = html.replace(old, new)
    return html
