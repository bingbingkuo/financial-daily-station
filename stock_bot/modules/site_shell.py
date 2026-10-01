"""
site_shell.py — 把選股雷達報告包上跟 docs/index.html 一致的側欄／頂部列外殼。

為什麼需要這個：docs/index.html 原本是用 iframe 把報告嵌進主頁（SPA 式切換），
但使用者是雙擊本機檔案打開（file://），瀏覽器會擋掉 file:// 頁面裡
<iframe src="file://...">的載入，畫面會整個空白。改成這裡的做法：報告發布
時直接把側欄／頂部列「烤」進每個報告 HTML 檔案本身，網站內用一般 <a> 連結
互相導覽（純瀏覽器導航，file:// 下保證能動），側欄自然就一直都在。
"""

import json
import os
import re

_PAGE_CSS = """
:root {
  --dr-bg:#0d1117; --dr-surface:#161b22; --dr-surface2:#21262d; --dr-border:#30363d;
  --dr-text:#e6edf3; --dr-muted:#7d8590; --dr-tw:#ef4444; --dr-us:#22c55e; --dr-pod:#a78bfa;
  --dr-twma:#2dd4bf; --dr-all:#f59e0b; --dr-radius:8px;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    --dr-bg:#f6f8fa; --dr-surface:#ffffff; --dr-surface2:#f0f2f5;
    --dr-border:#d0d7de; --dr-text:#1f2328; --dr-muted:#656d76;
  }
}
:root[data-theme="dark"]  { --dr-bg:#0d1117;--dr-surface:#161b22;--dr-surface2:#21262d;--dr-border:#30363d;--dr-text:#e6edf3;--dr-muted:#7d8590; }
:root[data-theme="light"] { --dr-bg:#f6f8fa;--dr-surface:#ffffff;--dr-surface2:#f0f2f5;--dr-border:#d0d7de;--dr-text:#1f2328;--dr-muted:#656d76; }
*,*::before,*::after { box-sizing:border-box; margin:0; padding:0; }
[hidden] { display:none !important; }
/* 注意：下面的殼層樣式全部用 .dr- 前綴 class 跟 --dr- 前綴變數，
   跟被包進來的報告自己的 CSS（.nav/.header/--bg 這類常見命名、
   甚至 body{} 全站選取器）完全不共用命名，兩邊不會互相覆蓋。
   側欄／頂部列因此不管包哪份報告，字體、字級、顏色都固定一致。 */
.dr-layout {
  display:flex; min-height:100vh;
  font-family:'IBM Plex Sans',sans-serif;
}
.dr-sidebar {
  width:220px; flex-shrink:0; background:var(--dr-surface); border-right:1px solid var(--dr-border);
  display:flex; flex-direction:column; position:fixed; top:0; left:0; bottom:0; z-index:50; overflow-y:auto;
}
.dr-brand { padding:20px 18px 16px; border-bottom:1px solid var(--dr-border); text-decoration:none; display:block; }
.dr-brand-name { font-size:14px; font-weight:700; letter-spacing:.5px; color:var(--dr-text); }
.dr-brand-sub  { font-size:11px; color:var(--dr-muted); margin-top:2px; }
.dr-nav { padding:12px 8px; flex:1; }
.dr-nav-item {
  display:flex; align-items:center; gap:10px; padding:9px 10px; border-radius:6px;
  font-size:13.5px; font-weight:500; color:var(--dr-muted); text-decoration:none; margin-bottom:2px;
}
.dr-nav-item:hover { background:var(--dr-surface2); color:var(--dr-text); }
.dr-nav-item.active { color:var(--dr-text); background:var(--dr-surface2); }
.dr-nav-item.soon { opacity:.5; pointer-events:none; }
.dr-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; background:var(--dr-border); }
.dr-dot-tw  { background:var(--dr-tw); }
.dr-dot-us  { background:var(--dr-us); }
.dr-dot-pod { background:var(--dr-pod); }
.dr-dot-twma { background:var(--dr-twma); }
.dr-nav-subitems { margin:0 0 4px 10px; padding-left:10px; border-left:1px solid var(--dr-border); }
.dr-nav-subitem {
  display:flex; align-items:center; gap:6px; padding:5px 8px; border-radius:5px;
  font-size:12px; color:var(--dr-muted); text-decoration:none; font-family:'IBM Plex Mono',monospace;
}
.dr-nav-subitem:hover { background:var(--dr-surface2); color:var(--dr-text); }
.dr-nav-subitem.active { color:var(--dr-text); font-weight:600; }
.dr-main { margin-left:220px; flex:1; display:flex; flex-direction:column; min-width:0; }
.dr-topbar {
  background:var(--dr-surface); border-bottom:1px solid var(--dr-border); padding:12px 24px;
  display:flex; align-items:center; justify-content:space-between; gap:12px;
  position:sticky; top:0; z-index:40;
}
.dr-topbar-left { display:flex; align-items:center; gap:12px; }
.dr-page-title { font-size:15px; font-weight:600; color:var(--dr-text); }
.dr-date-badge {
  font-size:12px; font-family:'IBM Plex Mono',monospace; background:var(--dr-surface2); color:var(--dr-muted);
  padding:3px 10px; border-radius:20px; border:1px solid var(--dr-border);
}
.dr-theme-btn {
  background:var(--dr-surface2); border:1px solid var(--dr-border); border-radius:6px;
  padding:5px 10px; color:var(--dr-muted); font-size:12px; cursor:pointer; font-family:inherit;
}
.dr-theme-btn:hover { color:var(--dr-text); }
.dr-content { padding:24px; flex:1; background:var(--dr-bg); color:var(--dr-text); }
@media (max-width:767px) {
  .dr-sidebar { position:static; width:100%; height:auto; border-right:none; border-bottom:1px solid var(--dr-border); }
  .dr-main { margin-left:0; }
  .dr-content { padding:16px; }
}
"""


_KIND_DIR = {"tw": "tw_scout", "us": "us_scout", "pod": "podcast", "twma": "tw_ma"}
_KIND_TITLE = {"tw": "台股選股雷達", "us": "美股選股雷達", "pod": "Podcast 摘要", "twma": "台股族群強弱"}


def _load_manifest(docs_root: str, kind: str) -> list[dict]:
    path = os.path.join(docs_root, _KIND_DIR[kind], "manifest.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _extract(html: str, start_tag: str, end_tag: str) -> str:
    i = html.find(start_tag)
    j = html.find(end_tag, i)
    if i == -1 or j == -1:
        return ""
    return html[i + len(start_tag):j]


def _extract_body(html: str) -> str:
    """抽出 <body>...</body> 內文。用 find("<body") 而非 find("<body>")，
    因為有些報告的 <body> 帶 style 屬性（例如 podcast 摘要），不是乾淨的
    <body> 純標籤。"""
    i = html.find("<body")
    if i == -1:
        return ""
    i = html.find(">", i)
    if i == -1:
        return ""
    j = html.find("</body>", i)
    if j == -1:
        return ""
    return html[i + 1:j]


def _extract_head_scripts(html: str) -> str:
    """抽出 <head> 裡的外部 <script src="..."> 標籤（例如 Chart.js CDN）。
    包外殼時只會重組 <style> + <body>，原本 <head> 裡其他東西（含這些外部
    script）會被丟掉，報告如果依賴它們（畫圖表之類）就會整個壞掉，所以要
    另外保留、接到新外殼的 <head> 裡。"""
    head_end = html.find("<body")
    if head_end == -1:
        head_end = len(html)
    head = html[:head_end]
    return "".join(re.findall(r'<script[^>]*\bsrc=[^>]*></script>', head))


def _subnav_html(manifest: list[dict], kind: str, active_file: str | None) -> str:
    rows = []
    for entry in manifest[:5]:
        active = " active" if entry["file"] == active_file else ""
        rows.append(
            f'<a class="dr-nav-subitem{active}" href="../{_KIND_DIR[kind]}/{entry["file"]}">{entry["date"]}</a>'
        )
    return "".join(rows)


def render_report_page(
    *, docs_root: str, kind: str, date: str, active_file: str, report_html: str,
) -> str:
    """把單篇報告 HTML 包上側欄／頂部列外殼。

    kind: "tw" / "us" / "pod"。report_html 是已經套過深色配色的完整報告文件
    （自己有一份 <style> + <body>，或是全 inline style 也可以——沒有
    <style> 區塊就當作空字串處理），這裡把它的 CSS／內文抽出來，放進跟
    docs/index.html 同樣視覺語彙的外殼裡。
    """
    report_css     = _extract(report_html, "<style>", "</style>")
    report_body    = _extract_body(report_html)
    report_scripts = _extract_head_scripts(report_html)

    title = _KIND_TITLE[kind]
    nav_items = ""
    # pod 不顯示近五日快照子選單——它自己的頁面有一致的固定導覽列
    # （每日摘要／股票追蹤／產業趨勢），不需要側欄重複列日期。
    for k, dot_cls, label, show_subnav in (
        ("pod", "dr-dot-pod", "Podcast 摘要", False),
        ("tw", "dr-dot-tw", "台股選股雷達", True),
        ("us", "dr-dot-us", "美股選股雷達", True),
        ("twma", "dr-dot-twma", "台股族群強弱", True),
    ):
        active_cls = " active" if kind == k else ""
        subnav_html = ""
        if show_subnav:
            manifest = _load_manifest(docs_root, k)
            subnav_html = f'\n      <div class="dr-nav-subitems">{_subnav_html(manifest, k, active_file if kind == k else None)}</div>'
        nav_items += f"""
      <a class="dr-nav-item{active_cls}" href="../{_KIND_DIR[k]}/latest.html">
        <span class="dr-dot {dot_cls}"></span> {label}
      </a>{subnav_html}"""

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · {date} · 財經日報站</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{_PAGE_CSS}</style>
<style>{report_css}</style>
{report_scripts}
</head>
<body>
<div class="dr-layout">
  <aside class="dr-sidebar">
    <a class="dr-brand" href="../index.html">
      <div class="dr-brand-name">📡 財經日報站</div>
      <div class="dr-brand-sub">Stock · Market · Podcast</div>
    </a>
    <nav class="dr-nav">
      <a class="dr-nav-item" href="../index.html">
        <span class="dr-dot" style="background:var(--dr-all)"></span> 總覽
      </a>{nav_items}
    </nav>
  </aside>

  <div class="dr-main">
    <header class="dr-topbar">
      <div class="dr-topbar-left">
        <span class="dr-page-title">{title}</span>
        <span class="dr-date-badge">{date}</span>
      </div>
      <button class="dr-theme-btn" onclick="drToggleTheme()" id="dr-theme-btn">🌙</button>
    </header>
    <main class="dr-content">
      {report_body}
    </main>
  </div>
</div>
<script>
(function() {{
  var saved = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  document.getElementById('dr-theme-btn').textContent = saved === 'dark' ? '🌙' : '☀️';
}})();
function drToggleTheme() {{
  var cur = document.documentElement.getAttribute('data-theme');
  var next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  document.getElementById('dr-theme-btn').textContent = next === 'dark' ? '🌙' : '☀️';
}}
</script>
</body>
</html>"""
