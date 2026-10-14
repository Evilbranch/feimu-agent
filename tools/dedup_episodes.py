"""清理 L2 重复条目

同一 raw_context 只保留最早的 id，其余删除
（按 content 去重不够，因为 LLM 每次生成的 content 文字不同）
"""
import os
import sys
import json
import glob

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

from core.constants import DATA_DIR

EPISODES_DIR = os.path.join(DATA_DIR, "episodes")


def _key(e):
    """去重键：优先用 raw_context，没有就用 content"""
    rc = (e.get("raw_context") or "").strip()
    if rc:
        return rc
    return (e.get("content") or "").strip()


def main():
    files = glob.glob(os.path.join(EPISODES_DIR, "*.json"))
    if not files:
        print(f"[dedup] 在 {EPISODES_DIR} 下没找到 .json")
        return

    total_removed = 0
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                items = json.load(fp)
        except Exception as e:
            print(f"[dedup] {os.path.basename(f)} 读取失败: {e}")
            continue

        seen = {}
        kept = []
        for e in items:
            key = _key(e)
            if not key:
                kept.append(e)
                continue
            if key in seen:
                total_removed += 1
                continue
            seen[key] = e.get("id")
            kept.append(e)

        if len(kept) != len(items):
            with open(f, "w", encoding="utf-8") as fp:
                json.dump(kept, fp, ensure_ascii=False, indent=2)
            print(f"[{os.path.basename(f)}] {len(items)} → {len(kept)}  删 {len(items)-len(kept)} 条")
        else:
            print(f"[{os.path.basename(f)}] {len(items)} 条，无重复")

    print(f"\n共删除 {total_removed} 条重复")


if __name__ == "__main__":
    main()