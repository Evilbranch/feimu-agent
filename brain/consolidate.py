"""L2 → L1 巩固 - 每天一次，把情景记忆抽象成语义记忆

跑法：由 my_ai.py 后台线程每日触发
逻辑：
1. 读过去 24 小时的 L2 情景
2. 让 LLM 抽象出 3~5 条"关于哥哥的事实"
3. 写入 ChromaDB（L1）
4. 标记已巩固的 L2 条目
"""
import os
import json
import time
import threading
from core.constants import DATA_DIR
from core import state

CONSOLIDATE_LOG = os.path.join(DATA_DIR, "consolidate_log.json")
_last_run_file = os.path.join(DATA_DIR, "consolidate_last.txt")

CONSOLIDATE_SYSTEM = """你是绯木的记忆整理助手。

给你一批"哥哥最近做过/说过的事"，你要抽象出关于哥哥的**稳定事实**。

规则：
- 只抽象"稳定的、会持续成立的事实"
  ✓ "哥哥喜欢哲学"、"哥哥住在杭州"、"哥哥玩游戏"
  ✗ "哥哥今天吃了汉堡"（临时事件）
- 每条事实 1 句话，用第三人称
- 事实要是"能长期成立的"，不是某一刻的状态
- 最多 5 条，没有新事实就返回空

输出 JSON：
{
  "facts": [
    "哥哥喜欢哲学和科学话题",
    "哥哥的生活比较规律：9点学习、2点游戏、18点半健身"
  ]
}

如果没有稳定事实可抽象，返回：{"facts": []}

现在处理："""


def _load_last_ts():
    if not os.path.exists(_last_run_file):
        return 0
    try:
        with open(_last_run_file, "r") as f:
            return float(f.read().strip())
    except Exception:
        return 0


def _save_last_ts(ts):
    try:
        with open(_last_run_file, "w") as f:
            f.write(str(ts))
    except Exception:
        pass


def _load_episodes_since(ts):
    """读 L2 里比 ts 晚的所有条目"""
    from brain.episodic import _load_month
    items = _load_month()
    return [m for m in items if m.get("ts", 0) > ts]


def _mark_consolidated(ids):
    from brain.episodic import mark_consolidated
    mark_consolidated(ids)


def consolidate_once(client, provider):
    """跑一次巩固"""
    last_ts = _load_last_ts()
    since = max(last_ts, time.time() - 86400)  # 最多回看 24 小时

    episodes = _load_episodes_since(since)
    if len(episodes) < 3:
        print(f"[巩固] 只有 {len(episodes)} 条新情景，跳过")
        return

    # 拼输入
    lines = []
    for e in episodes:
        content = e.get("content", "")
        etype = e.get("event_type", "")
        if content:
            lines.append(f"- ({etype}) {content}")
    text = "\n".join(lines[:30])  # 最多取 30 条

    if not text:
        return

    try:
        msgs = [
            {"role": "system", "content": CONSOLIDATE_SYSTEM},
            {"role": "user", "content": text},
        ]
        r = client.chat.completions.create(
            model=provider["model"],
            messages=msgs,
            timeout=120,
            temperature=0.3,
            max_tokens=500,
            extra_body={"keep_alive": "30m", "think": False},
        )
        resp = (r.choices[0].message.content or "").strip()
        if not resp:
            return

        import re
        start = resp.find("{")
        end = resp.rfind("}")
        if start == -1 or end <= start:
            return

        data = json.loads(resp[start:end + 1])
        facts = data.get("facts", [])

        if not facts:
            print(f"[巩固] 未抽象出新事实")
            _save_last_ts(time.time())
            return

        # 写入 ChromaDB
        s = state.get_state()
        if s.rag and s.rag._loaded:
            for fact in facts:
                s.rag.add(
                    fact,
                    f"[从对话抽象] {fact}",
                    slot="owner",
                )
                print(f"[巩固] 写入 L1: {fact}")

        # 标记 L2 已巩固
        ids = [e.get("id") for e in episodes if e.get("id")]
        if ids:
            _mark_consolidated(ids)
            print(f"[巩固] 标记 {len(ids)} 条 L2 已巩固")

        # 记录
        _log_consolidation(facts, len(episodes))
        _save_last_ts(time.time())

    except Exception as e:
        print(f"[巩固] 异常: {e}")


def _log_consolidation(facts, n_episodes):
    try:
        log = []
        if os.path.exists(CONSOLIDATE_LOG):
            with open(CONSOLIDATE_LOG, "r", encoding="utf-8") as f:
                log = json.load(f)
        log.append({
            "ts": time.time(),
            "episodes_count": n_episodes,
            "facts": facts,
        })
        log = log[-30:]
        with open(CONSOLIDATE_LOG, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def consolidate_loop(client, provider):
    """后台线程：每 6 小时检查一次，看是否需要巩固"""
    s = state.get_state()
    print("[巩固] 巩固循环已启动（每 6 小时检查）")

    # 首次延迟 10 分钟
    s.shutdown_flag.wait(600)
    if s.shutdown_flag.is_set():
        return

    while not s.shutdown_flag.is_set():
        try:
            now = time.time()
            last = _load_last_ts()
            if now - last > 86400:  # 超过 24 小时才跑
                consolidate_once(client, provider)
        except Exception as e:
            print(f"[巩固] 循环异常: {e}")

        s.shutdown_flag.wait(6 * 3600)