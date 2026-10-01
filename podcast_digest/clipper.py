"""
音頻片段剪輯器：從完整音檔剪出每個重點對應的片段
每段預設從 timestamp-5s 開始，長度 90 秒
"""
import subprocess
import os
import hashlib
from pathlib import Path

CLIPS_DIR = Path(__file__).parent / "data" / "clips"
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

CLIP_BEFORE = 5    # timestamp 前幾秒
CLIP_DURATION = 90  # 片段長度（秒）


def clip_key(episode_title: str, timestamp: int) -> str:
    """產生唯一的片段 ID"""
    raw = f"{episode_title}:{timestamp}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def extract_clip(audio_path: str, timestamp: int, clip_id: str) -> str | None:
    """
    從 audio_path 剪出 timestamp 附近的片段
    回傳儲存路徑（相對於 data/clips/），失敗回傳 None
    """
    out_path = CLIPS_DIR / f"{clip_id}.mp3"
    if out_path.exists():
        return str(out_path)

    start = max(0, timestamp - CLIP_BEFORE)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", audio_path,
        "-t", str(CLIP_DURATION),
        "-acodec", "libmp3lame",
        "-b:a", "64k",   # 低碼率，檔案小
        "-ac", "1",       # 單聲道
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and out_path.exists():
            size_kb = out_path.stat().st_size // 1024
            return str(out_path)
        return None
    except Exception as e:
        print(f"    ✗ 剪片段失敗 (ts={timestamp}s): {e}")
        return None


def inject_clips(episode: dict, audio_path: str) -> dict:
    """
    剪出所有重點和心法片段，直接把 clip_url 寫入每個 item。
    修改並回傳 episode dict（in-place）。
    """
    title = episode.get("title", "")

    # 收集所有需要剪的 timestamp（去重）
    ts_set = set()
    for kp in episode.get("key_points", []):
        if isinstance(kp, dict) and kp.get("timestamp") is not None:
            ts_set.add(int(kp["timestamp"]))
    for ins in episode.get("trading_insights", []):
        if isinstance(ins, dict) and ins.get("timestamp") is not None:
            ts_set.add(int(ins["timestamp"]))

    if not ts_set:
        return episode

    print(f"    剪輯 {len(ts_set)} 個片段...")

    # 剪片段並建 ts → url 映射
    ts_to_url = {}
    for ts in sorted(ts_set):
        cid = clip_key(title, ts)
        path = extract_clip(audio_path, ts, cid)
        if path:
            ts_to_url[ts] = f"/clips/{cid}.mp3"

    # 注入 clip_url
    for kp in episode.get("key_points", []):
        if isinstance(kp, dict) and kp.get("timestamp") is not None:
            url = ts_to_url.get(int(kp["timestamp"]))
            if url:
                kp["clip_url"] = url

    for ins in episode.get("trading_insights", []):
        if isinstance(ins, dict) and ins.get("timestamp") is not None:
            url = ts_to_url.get(int(ins["timestamp"]))
            if url:
                ins["clip_url"] = url

    print(f"    ✓ 片段注入完成（{len(ts_to_url)}/{len(ts_set)} 成功）")
    return episode
