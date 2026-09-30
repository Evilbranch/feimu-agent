"""情景记忆管理工具 - 命令行查看/删除/导出

用法：
  python tools/episode_manager.py list               # 列出最近 7 天
  python tools/episode_manager.py list --days 30     # 列出最近 30 天
  python tools/episode_manager.py list --all         # 列出全部
  python tools/episode_manager.py show <id>          # 看某条完整内容
  python tools/episode_manager.py delete <id>        # 删一条
  python tools/episode_manager.py prune --below 0.35 # 删所有 imp < 0.35 的
  python tools/episode_manager.py export             # 导出到 data/episodes_export.txt
"""
import os
import sys
import json
import glob

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EPISODES_DIR = os.path.join(SCRIPT_DIR, "data", "episodes")


def _load_all():
    """读取所有月份的 JSON"""
    if not os.path.isdir(EPISODES_DIR):
        print(f"[错误] 目录不存在: {EPISODES_DIR}")
        return []
    files = sorted(glob.glob(os.path.join(EPISODES_DIR, "*.json")))
    items = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            for m in data:
                m["_file"] = f
                items.append(m)
        except Exception as e:
            print(f"[警告] {f} 读取失败: {e}")
    items.sort(key=lambda x: x.get("ts", 0))
    return items


def _save_by_file(items):
    """按 _file 分组写回"""
    from collections import defaultdict
    groups = defaultdict(list)
    for m in items:
        f = m.pop("_file", None)
        if f:
            groups[f].append(m)
    for f, group in groups.items():
        with open(f, "w", encoding="utf-8") as fp:
            json.dump(group, fp, ensure_ascii=False, indent=2)


def cmd_list(args):
    days = None
    if "--all" in args:
        days = None
    elif "--days" in args:
        i = args.index("--days")
        days = int(args[i+1]) if i+1 < len(args) else 7
    else:
        days = 7

    import time
    now = time.time()
    items = _load_all()
    if days is not None:
        cutoff = now - days * 86400
        items = [m for m in items if m.get("ts", 0) > cutoff]

    if not items:
        print(f"（没有条目）")
        return

    print(f"\n共 {len(items)} 条：\n")
    for m in items:
        _id = m.get("id", "?")
        day = m.get("day", "?")
        etype = m.get("event_type", "?")
        imp = m.get("importance", 0)
        content = (m.get("content") or "")[:60]
        self_emo = m.get("self_emotion", {}).get("label", "")
        print(f"  {_id}")
        print(f"    [{day}] {etype:12s} imp={imp:.2f}  我当时:{self_emo}")
        print(f"    {content}")
        print()


def cmd_show(args):
    if not args:
        print("用法: show <id>")
        return
    target = args[0]
    items = _load_all()
    for m in items:
        if m.get("id") == target:
            m.pop("_file", None)
            print(json.dumps(m, ensure_ascii=False, indent=2))
            return
    print(f"[未找到] {target}")


def cmd_delete(args):
    if not args:
        print("用法: delete <id>")
        return
    target = args[0]
    items = _load_all()
    kept = [m for m in items if m.get("id") != target]
    if len(kept) == len(items):
        print(f"[未找到] {target}")
        return
    _save_by_file(kept)
    print(f"[已删除] {target}")


def cmd_prune(args):
    if "--below" not in args:
        print("用法: prune --below <阈值>   （删除 importance 低于阈值的条目）")
        return
    i = args.index("--below")
    try:
        threshold = float(args[i+1])
    except (IndexError, ValueError):
        print("[错误] 阈值必须是数字")
        return

    items = _load_all()
    before = len(items)
    kept = [m for m in items if m.get("importance", 1.0) >= threshold]
    removed = before - len(kept)
    if removed == 0:
        print(f"[无变化] 没有 imp < {threshold} 的条目")
        return
    _save_by_file(kept)
    print(f"[已删除 {removed} 条] 剩余 {len(kept)} 条")


def cmd_export(args):
    items = _load_all()
    if not items:
        print("（没有条目）")
        return
    out_path = os.path.join(SCRIPT_DIR, "data", "episodes_export.txt")
    lines = []
    for m in items:
        day = m.get("day", "?")
        etype = m.get("event_type", "?")
        imp = m.get("importance", 0)
        content = m.get("content", "")
        self_emo = m.get("self_emotion", {}).get("label", "")
        lines.append(f"[{day}] {etype} imp={imp:.2f}")
        lines.append(f"  {content}")
        if self_emo:
            lines.append(f"  （我当时{self_emo}）")
        lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[已导出] {out_path}  共 {len(items)} 条")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    args = sys.argv[2:]
    cmds = {
        "list": cmd_list,
        "show": cmd_show,
        "delete": cmd_delete,
        "prune": cmd_prune,
        "export": cmd_export,
    }
    if cmd not in cmds:
        print(f"[未知命令] {cmd}")
        print(__doc__)
        return
    cmds[cmd](args)


if __name__ == "__main__":
    main()