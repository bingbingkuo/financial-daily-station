"""
git_publish.py — 把 docs/ 的異動 commit + push 到 GitHub。

設計成「失敗不影響主流程」：還沒設定 git remote、網路不通、push 衝突等
情況只印警告，不會讓 main_scout.py / main_us_scout.py 這些排程腳本失敗。
"""

import os
import subprocess

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def publish_docs(commit_message: str) -> None:
    """git add docs/ 之後，若有變動就 commit + push。"""
    try:
        subprocess.run(
            ["git", "add", "docs"],
            cwd=_REPO_ROOT, check=True, capture_output=True, text=True,
        )
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=_REPO_ROOT, capture_output=True,
        )
        if diff.returncode == 0:
            print("  📦 docs/ 無變動，略過推送")
            return

        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=_REPO_ROOT, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "push"],
            cwd=_REPO_ROOT, check=True, capture_output=True, text=True, timeout=60,
        )
        print("  🚀 已推送到 GitHub")
    except Exception as e:
        detail = e.stderr if hasattr(e, "stderr") and e.stderr else str(e)
        print(f"  ⚠️  推送 GitHub 失敗（不影響主流程）：{detail}")
