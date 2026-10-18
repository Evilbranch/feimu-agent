"""笔记 - 记录 / 查询 / 删除"""
import os
import json
import time
from core.constants import DATA_DIR

NOTES_FILE = os.path.join(DATA_DIR, "notes.json")


def _load():
    if not os.path.exists(NOTES_FILE): return []
    try:
        with open(NOTES_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return []


def _save(notes):
    try:
        with open(NOTES_FILE, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)
    except: pass


def add(content):
    if not content or not content.strip():
        return "内容为空呢。"
    content = content.strip()
    # 🆕 剥掉"记一下/记一下,/帮我记/记："等前缀
    import re
    prefixes = [
        "记一下笔记", "记个笔记", "帮我记笔记", "添加笔记", "新增笔记",
        "记一下：", "记一下:", "记一下，", "记一下,",
        "记一条：", "记一条:", "记一条，", "记一条,",
        "记：", "记:", "記：", "記:",
        "记一下", "記一下", "记下来", "記下来",
        "帮我记一下", "帮我记下来", "帮我记个", "帮我記",
        "帮我记", "幫我記",
    ]
    for p in prefixes:
        if content.startswith(p):
            content = content[len(p):].lstrip("，,：: ")
            break
    if not content:
        return "内容为空呢。"
    notes = _load()
    notes.append({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "content": content
    })
    _save(notes)
    return f"记下了：{content}"


def list_recent(n=5):
    notes = _load()
    if not notes:
        return "还没有笔记呢。"
    recent = notes[-n:]
    lines = [f"{note['content']}（{note['time'][5:16]}）" for note in recent]
    return "最近的笔记：" + "；".join(lines)


def delete_last():
    notes = _load()
    if not notes:
        return "没有笔记可以删呢。"
    removed = notes.pop()
    _save(notes)
    return f"删掉了：{removed['content']}"


def clear_all():
    notes = _load()
    n = len(notes)
    _save([])
    return f"清空了 {n} 条笔记。"