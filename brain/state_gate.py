"""状态门控：情绪和驱力影响行为

质变点 1 第一阶段：
- 情绪低时不说话、不探索
- 驱力高低影响唤醒间隔
- 不替换 LLM 决策，只在 LLM 决策之上加门控

设计原则：
- 情绪是"允许/不允许"的门，不是"该说什么"的指令
- 允许 LLM 在门内自由发挥
"""
from brain import persona as P


# ══════════════════════════════════════════════════════
# 阈值
# ══════════════════════════════════════════════════════
VALENCE_SPEAK_MIN = -0.4      # 低于此值 → 禁止 speak
VALENCE_EXPLORE_MIN = -0.2    # 低于此值 → 禁止 explore
AROUSAL_HIGH = 0.7            # 高于此值 → 唤醒加速
CONNECTION_LOW = 0.2          # 低于此值 → 禁止 speak
CONNECTION_HIGH = 0.8         # 高于此值 → 唤醒加速
CURIOSITY_LOW = 0.2           # 低于此值 → 禁止 explore


def gate():
    """返回 (allow_speak, allow_explore, next_wake_mult, reason)

    allow_speak: 是否允许 speak
    allow_explore: 是否允许 explore
    next_wake_mult: next_wake 的倍数（<1 更快醒，>1 更慢）
    reason: 简短的原因描述
    """
    try:
        st = P.get_current_state()
    except Exception as e:
        print(f"[状态门控] 读取失败: {e}")
        return True, True, 1.0, "读取失败，默认放行"

    e = st.get("emotion", {})
    d = st.get("drives", {})
    v = float(e.get("valence", 0.0))
    a = float(e.get("arousal", 0.4))
    conn = float(d.get("connection", 0.5))
    cur = float(d.get("curiosity", 0.5))

    allow_speak = True
    allow_explore = True
    mult = 1.0
    reasons = []

    # ═══ 情绪门 ═══
    if v < VALENCE_SPEAK_MIN:
        allow_speak = False
        mult *= 1.5
        reasons.append(f"情绪低({v:.2f})不说")
    if v < VALENCE_EXPLORE_MIN:
        allow_explore = False
        mult *= 1.3
        reasons.append(f"没心情探索({v:.2f})")
    if a > AROUSAL_HIGH:
        mult *= 0.7
        reasons.append(f"激动({a:.2f})醒快点")

    # ═══ 驱力门 ═══
    if conn < CONNECTION_LOW:
        allow_speak = False
        reasons.append(f"连接欲低({conn:.2f})")
    if conn > CONNECTION_HIGH:
        mult *= 0.5
        reasons.append(f"很想哥哥({conn:.2f})")
    if cur < CURIOSITY_LOW:
        allow_explore = False
        reasons.append(f"好奇心低({cur:.2f})")

    reason = "; ".join(reasons) if reasons else "状态正常，放行"
    return allow_speak, allow_explore, mult, reason


def describe_state():
    """把当前状态描述成一句"她感受到的话"（供上下文用）"""
    try:
        st = P.get_current_state()
    except Exception:
        return ""

    e = st.get("emotion", {})
    d = st.get("drives", {})
    v = float(e.get("valence", 0.0))
    label = e.get("label", "平静")
    conn = float(d.get("connection", 0.5))
    cur = float(d.get("curiosity", 0.5))

    parts = [f"我现在{label}"]

    # 情绪感受
    if v >= 0.6:
        parts.append("心里很暖，想跟哥哥多说两句")
    elif v >= 0.3:
        parts.append("心情还不错")
    elif v >= 0.1:
        parts.append("挺平静的")
    elif v >= -0.2:
        parts.append("说不上来，有点闷")
    elif v >= -0.4:
        parts.append("不太想说话")
    else:
        parts.append("心里堵着一口气，谁都不想理")

    # 驱力感受
    if conn > 0.7:
        parts.append("特别想他在身边")
    elif conn > 0.4:
        parts.append("有点想他")
    elif conn < 0.2:
        parts.append("现在不太想被打扰")

    if cur > 0.7:
        parts.append("脑子很活跃，很想搞清楚点什么")
    elif cur < 0.2:
        parts.append("脑子懒懒的，不太想动")

    return "。".join(parts) + "。"