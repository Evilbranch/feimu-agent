"""弹幕影响情绪 - 弹幕是氛围，不是指令

设计：
- 弹幕聚合 → 氛围标签 → 微调 v/a
- 不写 L0、不影响决策、不逐条响应

核心：
"她被弹幕感受，但不被弹幕指挥。"
"""
import time
from collections import Counter

from brain import persona as P


_MOOD_WORDS = {
    "positive": [
        "哈哈", "666", "笑死", "可爱", "喜欢", "爱了", "太好了",
        "太棒了", "牛", "nb", "好耶", "哈哈哈哈",
    ],
    "negative": [
        "无语", "无聊", "难看", "垃圾", "菜", "滚", "恶心",
        "差评", "退钱", "退了",
    ],
    "attack": [
        "傻", "智障", "白痴", "弱智", "蠢", "丑", "肥",
        "垃圾主播", "傻逼", "sb",
    ],
    "curious": [
        "为什么", "怎么", "什么", "？", "?", "求解", "不懂",
    ],
}


def _classify_one(text):
    if not text:
        return None
    low = text.lower()
    for label, words in _MOOD_WORDS.items():
        for w in words:
            if w in low:
                return label
    return None


def aggregate_mood(danmu_batch):
    """一批弹幕 → 氛围标签

    danmu_batch: [{"text": str, ...}, ...]
    """
    if not danmu_batch:
        return {"dominant": "neutral", "count": 0, "distribution": {}}

    labels = []
    for d in danmu_batch:
        label = _classify_one(d.get("text", ""))
        if label:
            labels.append(label)

    if not labels:
        return {"dominant": "neutral", "count": len(danmu_batch), "distribution": {}}

    c = Counter(labels)
    dominant = c.most_common(1)[0][0]
    return {
        "dominant": dominant,
        "count": len(danmu_batch),
        "distribution": dict(c),
    }


# 每次影响的幅度（保守，避免弹幕"控制"她）
_IMPACT = {
    "positive":  (+0.05, +0.03),
    "negative":  (-0.04, +0.02),
    "attack":    (-0.08, +0.05),
    "curious":   ( 0.00, +0.02),
    "neutral":   ( 0.00,  0.00),
}

_last_attack_ts = [0]
_ATTACK_DECAY_WINDOW = 300


def apply_mood(aggregate):
    """氛围 → persona v/a 微调"""
    dominant = aggregate.get("dominant", "neutral")
    if dominant == "neutral":
        return {"applied": False, "reason": "neutral"}

    dv, da = _IMPACT.get(dominant, (0.0, 0.0))
    if dv == 0 and da == 0:
        return {"applied": False, "reason": "no_impact"}

    if dominant == "attack":
        now = time.time()
        if now - _last_attack_ts[0] < _ATTACK_DECAY_WINDOW:
            dv *= 0.5
            da *= 0.5
        _last_attack_ts[0] = now

    try:
        with P._lock:
            data = P._load()
            for m in ("master", "audience"):
                e = data[m].get("emotion", {})
                cv = float(e.get("valence", 0.2))
                ca = float(e.get("arousal", 0.4))
                nv = max(-1.0, min(1.0, cv + dv))
                na = max(0.0, min(1.0, ca + da))
                e["valence"] = nv
                e["arousal"] = na
                e["label"] = P._label_from_va(nv, na)
                data[m]["emotion"] = e
            P._save(data)
        print(f"[弹幕氛围] {dominant} → v{dv:+.2f} a{da:+.2f}")
        return {"applied": True, "dominant": dominant, "dv": dv, "da": da}
    except Exception as e:
        print(f"[弹幕氛围] 应用失败: {e}")
        return {"applied": False, "reason": str(e)}


def write_l2_if_significant(aggregate):
    """氛围显著时写一条 L2"""
    count = aggregate.get("count", 0)
    dominant = aggregate.get("dominant", "neutral")
    if dominant == "neutral":
        return
    if count < 20 and not (dominant == "attack" and count >= 5):
        return

    try:
        from brain.episodic import record_episode
        from brain.persona import get_current_state
        st = get_current_state()
        _label_map = {
            "positive": "直播间气氛热烈",
            "negative": "直播间有些冷场",
            "attack": "直播间有人攻击我",
            "curious": "观众们很好奇",
        }
        content = _label_map.get(dominant, f"直播间氛围：{dominant}")
        record_episode(
            content=content,
            event_type="interaction",
            entities=["观众"],
            self_emotion=st.get("emotion"),
            self_intent="respond",
            self_role="streamer",
            channel="live",
            raw_context=f"弹幕 {count}，分布 {aggregate.get('distribution')}",
        )
    except Exception as e:
        print(f"[弹幕氛围] L2 写入失败: {e}")