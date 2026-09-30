"""L2 情景记忆 - 绯木的数字自传体记忆

设计依据：
- Tulving (1983, 1985): 情景记忆三要素 - What/Where-When/Who
- ZifaMem (2026): 情感标记嵌入记忆表征，重要性驱动的提升机制
- Amory (2026): 叙事驱动的记忆组织，对话动量与主题连贯性
- HiMem (2026): Topic-Aware事件分段，认知一致的Episode构建

存储：data/episodes/YYYY-MM.json
写入：每轮对话后异步调用 record_episode()
检索：与现有 RAG (L1) 合并，加权的混合检索
"""
import os
import json
import time
import uuid
import threading
from datetime import datetime
from core.constants import DATA_DIR

EPISODES_DIR = os.path.join(DATA_DIR, "episodes")
_lock = threading.Lock()

# ══════════════════════════════════════════════════════
# 事件类型权重（用于 importance 计算）
# ══════════════════════════════════════════════════════
EVENT_TYPE_WEIGHTS = {
    "milestone":    1.0,
    "user_fact":    0.8,
    "self_emotion": 0.8,
    "user_emotion": 0.7,
    "interaction":  0.6,
    "discovery":    0.6,
    "casual":       0.2,
}


def _month_file(ts=None):
    """返回当月的情景记忆文件路径"""
    if ts is None:
        ts = time.time()
    dt = datetime.fromtimestamp(ts)
    return os.path.join(EPISODES_DIR, f"{dt.strftime('%Y-%m')}.json")


def _load_month(ts=None):
    """加载当月的情景记忆"""
    path = _month_file(ts)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_month(items, ts=None):
    """保存当月的情景记忆"""
    path = _month_file(ts)
    os.makedirs(EPISODES_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════
# 重要性计算
# ══════════════════════════════════════════════════════
def _compute_importance(event_type, emotion_intensity, novelty=0.5):
    """计算情景记忆的重要性（0~1）

    importance = 0.4*情感强度 + 0.3*事件类型权重 + 0.2*新颖度 + 0.1*基础值
    """
    type_weight = EVENT_TYPE_WEIGHTS.get(event_type, 0.2)
    return min(1.0, max(0.0,
        0.4 * emotion_intensity +
        0.3 * type_weight +
        0.2 * novelty +
        0.1 * 0.5
    ))


# ══════════════════════════════════════════════════════
# 写入
# ══════════════════════════════════════════════════════
def record_episode(
    content,
    event_type="casual",
    entities=None,
    self_emotion=None,
    self_intent="idle",
    self_role="responder",
    channel="local",
    raw_context="",
    novelty=0.5,
    ts=None,
):
    """写入一条情景记忆

    由 my_ai.py 每轮对话后异步调用。
    不是每轮都写——调用方应先判断这轮是否“值得记”。
    """
    if not content or not content.strip():
        return None

    if ts is None:
        ts = time.time()

    dt = datetime.fromtimestamp(ts)
    ep_id = f"ep_{dt.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"

    emotion_intensity = 0.0
    if self_emotion and isinstance(self_emotion, dict):
        emotion_intensity = abs(self_emotion.get("valence", 0.0))

    importance = _compute_importance(event_type, emotion_intensity, novelty)

    entry = {
        "id": ep_id,
        "ts": ts,
        "day": dt.strftime("%Y-%m-%d"),

        "content": content.strip()[:300],
        "raw_context": raw_context[:500] if raw_context else "",
        "entities": entities or [],
        "event_type": event_type,

        "time_of_day": _time_of_day(dt.hour),
        "channel": channel,
        "conversation_id": None,

        "self_emotion": self_emotion or {"label": "平静", "valence": 0.2, "arousal": 0.4},
        "self_intent": self_intent,
        "self_role": self_role,
        "self_reflection": None,

        "emotion_intensity": round(emotion_intensity, 3),
        "importance": round(importance, 3),
        "refs": 0,
        "last_ref": ts,

        "thread_id": None,
        "thread_title": None,
        "related_ids": [],

        "status": "active",
        "consolidated_at": None,
        "decay_rate": 0.023,
    }

    with _lock:
        items = _load_month(ts)
        items.append(entry)
        _save_month(items, ts)

    print(f"[L2] 新情景记忆 {ep_id} type={event_type} imp={importance:.2f}")
    return ep_id


def _time_of_day(hour):
    if 5 <= hour < 9:
        return "morning"
    elif 9 <= hour < 12:
        return "forenoon"
    elif 12 <= hour < 14:
        return "noon"
    elif 14 <= hour < 18:
        return "afternoon"
    elif 18 <= hour < 23:
        return "evening"
    else:
        return "night"


# ══════════════════════════════════════════════════════
# 检索
# ══════════════════════════════════════════════════════
def retrieve_episodes(query=None, top_k=5, days_back=90, min_importance=0.3):
    """检索情景记忆

    检索策略（与纯向量检索不同）：
    1. 先按时间窗口过滤（默认最近 90 天）
    2. 按重要性过滤（低于阈值的丢弃）
    3. 按综合分数排序：
       score = 时间新鲜度 * 0.3 + 重要性 * 0.4 + 引用频率 * 0.3
    """
    now = time.time()
    cutoff = now - days_back * 86400
    all_items = []

    for path in sorted(
        [f for f in os.listdir(EPISODES_DIR) if f.endswith(".json")],
        reverse=True,
    )[:6]:
        try:
            with open(os.path.join(EPISODES_DIR, path), "r", encoding="utf-8") as f:
                items = json.load(f)
            all_items.extend([m for m in items if m.get("ts", 0) > cutoff])
        except Exception:
            continue

    all_items = [m for m in all_items if m.get("importance", 0) >= min_importance]

    scored = []
    for m in all_items:
        age_days = (now - m.get("ts", now)) / 86400
        freshness = max(0.0, 1.0 - age_days / 90)  # 90天线性衰减
        ref_score = min(1.0, m.get("refs", 0) / 5.0)
        score = freshness * 0.3 + m.get("importance", 0) * 0.4 + ref_score * 0.3
        scored.append((score, m))

    scored.sort(key=lambda x: -x[0])
    results = [m for _, m in scored[:top_k]]

    if results:
        _bump_refs([m["id"] for m in results])

    return results


def _bump_refs(ids):
    """增加被引用记忆的 refs 计数"""
    if not ids:
        return
    with _lock:
        items = _load_month()
        now = time.time()
        for m in items:
            if m.get("id") in ids:
                m["refs"] = m.get("refs", 0) + 1
                m["last_ref"] = now
        _save_month(items)


# ══════════════════════════════════════════════════════
# 巩固（L2 → L1/L0）
# ══════════════════════════════════════════════════════
def get_today_episodes():
    """获取今天的全部情景记忆（供每日巩固使用）"""
    today = time.strftime("%Y-%m-%d")
    items = _load_month()
    return [m for m in items if m.get("day") == today]


def mark_consolidated(ids):
    """标记为已巩固"""
    with _lock:
        items = _load_month()
        now = time.time()
        for m in items:
            if m.get("id") in ids:
                m["status"] = "consolidated"
                m["consolidated_at"] = now
        _save_month(items)


# ══════════════════════════════════════════════════════
# 供 prompt 使用：格式化
# ══════════════════════════════════════════════════════
def format_episodes_for_prompt(episodes, max_items=3):
    """把检索到的情景记忆格式化成 prompt 片段"""
    if not episodes:
        return ""
    lines = ["【我记得这些事】"]
    for e in episodes[:max_items]:
        day = e.get("day", "")
        content = e.get("content", "")
        self_emo = e.get("self_emotion", {}).get("label", "")
        lines.append(f"· {day}：{content}")
        if self_emo and self_emo != "平静":
            lines.append(f"  （我当时{self_emo}）")
    return "\n".join(lines)