"""L2 记忆衰减

每天跑一次：
- 重要记忆（事实、观点、里程碑、情感强烈的）永久保留
- 普通记忆（互动、探索、闲聊）逐渐衰减

衰减规则：
- 每 24 小时 importance *= (1 - decay_rate)
- importance < 0.2 且衰减过 5 次以上 → status=archived
- archived 记忆不参与检索（但仍存盘）

设计目的：
不是省空间（L2 本来就不大），是让"不重要的记忆自然淡出"——
检索时她不会被无关紧要的旧事干扰。
"""
import os
import json
import time
import threading
from datetime import datetime

from core.constants import DATA_DIR

EPISODES_DIR = os.path.join(DATA_DIR, "episodes")
DECAY_LOG = os.path.join(DATA_DIR, "decay_log.json")
DECAY_STATE = os.path.join(DATA_DIR, "decay_last.txt")

_lock = threading.Lock()

# ══════════════════════════════════════════════════════
# 衰减规则
# ══════════════════════════════════════════════════════
DECAY_RATES = {
    "milestone":      0.0,     # 不衰减
    "user_fact":      0.0,
    "user_view":      0.0,
    "self_reflection": 0.0,
    "user_emotion":   0.01,
    "self_emotion":   0.01,
    "interaction":    0.03,
    "discovery":      0.02,
    "casual":         0.05,
}

# 情感强度超过这个值 → 不衰减
EMOTION_PROTECT = 0.6

# 低于这个值 + 衰减过 5 次以上 → archived
ARCHIVE_THRESHOLD = 0.2
ARCHIVE_MIN_DECAYS = 5

# ══════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════
def _load_last_ts():
    if not os.path.exists(DECAY_STATE):
        return 0
    try:
        with open(DECAY_STATE, "r") as f:
            return float(f.read().strip())
    except Exception:
        return 0


def _save_last_ts(ts):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DECAY_STATE, "w") as f:
            f.write(str(ts))
    except Exception:
        pass


def _append_log(entry):
    try:
        logs = []
        if os.path.exists(DECAY_LOG):
            with open(DECAY_LOG, "r", encoding="utf-8") as f:
                logs = json.load(f)
        logs.append(entry)
        logs = logs[-30:]
        with open(DECAY_LOG, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _should_protect(m):
    """判断这条记忆是否受保护（不衰减）"""
    etype = m.get("event_type", "casual")
    if DECAY_RATES.get(etype, 0.03) == 0.0:
        return True
    if float(m.get("emotion_intensity", 0)) > EMOTION_PROTECT:
        return True
    return False


# ══════════════════════════════════════════════════════
# 主逻辑
# ══════════════════════════════════════════════════════
def decay_once(force=False):
    """跑一次衰减

    返回 (processed, decayed, archived)
    """
    last = _load_last_ts()
    now = time.time()
    if not force and (now - last) < 24 * 3600:
        return 0, 0, 0

    if not os.path.exists(EPISODES_DIR):
        return 0, 0, 0

    with _lock:
        processed = 0
        decayed = 0
        archived = 0

        for fname in os.listdir(EPISODES_DIR):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(EPISODES_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    items = json.load(f)
            except Exception:
                continue

            changed = False
            for m in items:
                if m.get("status") == "archived":
                    continue
                processed += 1

                # 受保护 → 跳过
                if _should_protect(m):
                    continue

                etype = m.get("event_type", "casual")
                rate = DECAY_RATES.get(etype, 0.03)
                old_imp = float(m.get("importance", 0.5))
                new_imp = max(0.0, old_imp * (1 - rate))
                m["importance"] = round(new_imp, 3)
                m["decay_count"] = m.get("decay_count", 0) + 1
                decayed += 1
                changed = True

                # 归档
                if (new_imp < ARCHIVE_THRESHOLD
                        and m["decay_count"] >= ARCHIVE_MIN_DECAYS):
                    m["status"] = "archived"
                    archived += 1

            if changed:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(items, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

        _save_last_ts(now)
        _append_log({
            "ts": now,
            "processed": processed,
            "decayed": decayed,
            "archived": archived,
        })

        print(f"[衰减] 处理 {processed} 条，衰减 {decayed} 条，归档 {archived} 条")
        return processed, decayed, archived


def decay_loop():
    """后台线程：每 6 小时检查一次，超过 24h 就跑"""
    from core import state
    s = state.get_state()
    print("[衰减] 衰减循环已启动（每 6 小时检查）")

    s.shutdown_flag.wait(600)
    if s.shutdown_flag.is_set():
        return

    while not s.shutdown_flag.is_set():
        try:
            decay_once()
        except Exception as e:
            print(f"[衰减] 异常: {e}")
        s.shutdown_flag.wait(6 * 3600)