"""L2：她说的话反过来影响她的情绪（自激阻尼版）

设计：
- 单轮变化极小（±0.01~0.03），远小于用户输入（±0.3~0.7）
- 连续 3 轮同方向 → 影响减半（防自激）
- 只在 owner 场景生效
"""
import time
from core import state
from brain import persona


# 她自己说的冷淡/退缩词
_COLD_WORDS = [
    "算了", "那好吧", "行吧", "随便", "懒得", "没意思",
    "不想聊", "不想说", "不想理", "别问", "走开", "不想吵",
    "闷闷不乐", "心情不好", "难过", "不开心", "有点闷",
    "有点累", "疲惫", "烦躁",
]

# 她自己说的温柔/主动词
_WARM_WORDS = [
    "喜欢你", "想你", "抱抱", "陪你", "陪伴", "辛苦",
    "我在", "在的", "好呀", "可以呀", "好的呀",
    "嘻嘻", "嘿嘿", "开心", "暖和", "舒服",
]


def _apply_va_delta(dv, da, de, dc):
    """把 v/a 和 drives 的变化写回 persona"""
    with persona._lock:
        data = persona._load()
        m = data["current_mode"]
        p = data[m]

        e = p.get("emotion", {})
        v = float(e.get("valence", 0.2))
        a = float(e.get("arousal", 0.4))
        nv = max(-1.0, min(1.0, v + dv))
        na = max(0.0, min(1.0, a + da))
        e["valence"] = nv
        e["arousal"] = na
        e["label"] = persona._label_from_va(nv, na)
        p["emotion"] = e

        d = p["drives"]
        d["expression"] = max(0.0, min(1.0, d["expression"] + de))
        d["connection"] = max(0.15, min(1.0, d["connection"] + dc))

        persona._save(data)
        print(f"[L2-自语影响] v={nv:.3f} a={na:.3f} exp={d['expression']:.3f} "
              f"conn={d['connection']:.3f} [{e['label']}]")


def apply_own_speech_impact(text, source="owner"):
    """她说完话后，她自己的话反过来影响她的情绪。

    只对 owner 生效。变化极小，被"连续同向"阻尼。
    """
    if source != "owner":
        return
    if not text or len(text) < 2:
        return

    s = state.get_state()

    cold = sum(1 for w in _COLD_WORDS if w in text)
    warm = sum(1 for w in _WARM_WORDS if w in text)

    # 判断方向
    if cold > warm:
        direction = -1
        dv, da, de, dc = -0.020, +0.015, -0.015, -0.010
    elif warm > cold:
        direction = +1
        dv, da, de, dc = +0.015, -0.005, +0.010, +0.020
    else:
        # 中性：清空连续计数，不改变情绪
        s.self_mood_streak = {"dir": 0, "count": 0}
        return

    # 阻尼：连续同方向 → 减半
    streak = getattr(s, "self_mood_streak", None) or {"dir": 0, "count": 0}
    if streak.get("dir") == direction:
        streak["count"] = streak.get("count", 0) + 1
    else:
        streak = {"dir": direction, "count": 1}

    damp = 0.5 if streak["count"] >= 3 else 1.0
    s.self_mood_streak = streak

    _apply_va_delta(dv * damp, da * damp, de * damp, dc * damp)

    # 说太长的话 → 表达驱力额外微降
    if len(text) > 50:
        _apply_va_delta(0.0, 0.0, -0.020, 0.0)