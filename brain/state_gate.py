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
    """返回 (allow_speak, allow_explore, reason)

    只做"允许/不允许"的硬门控。
    next_wake 的调制由 drive_modifier() 独立负责。
    """
    try:
        st = P.get_current_state()
    except Exception as e:
        print(f"[状态门控] 读取失败: {e}")
        return True, True, "读取失败，默认放行"

    e = st.get("emotion", {})
    d = st.get("drives", {})
    v = float(e.get("valence", 0.0))
    conn = float(d.get("connection", 0.5))
    cur = float(d.get("curiosity", 0.5))

    allow_speak = True
    allow_explore = True
    reasons = []

    # ═══ 情绪门 ═══
    if v < VALENCE_SPEAK_MIN:
        allow_speak = False
        reasons.append(f"情绪低({v:.2f})不说")
    if v < VALENCE_EXPLORE_MIN:
        allow_explore = False
        reasons.append(f"没心情探索({v:.2f})")

    # ═══ 驱力门 ═══
    if conn < CONNECTION_LOW:
        allow_speak = False
        reasons.append(f"连接欲低({conn:.2f})")
    if cur < CURIOSITY_LOW:
        allow_explore = False
        reasons.append(f"好奇心低({cur:.2f})")

    reason = "; ".join(reasons) if reasons else "状态正常，放行"
    return allow_speak, allow_explore, reason


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

# ══════════════════════════════════════════════════════
# 质变点 1 Stage 3：驱力驱动 next_wake
# ══════════════════════════════════════════════════════
def _map_connection(c):
    """connection 映射：越高 → 醒越频繁"""
    if c >= 0.85:  return 0.4
    if c >= 0.70:  return 0.6
    if c >= 0.50:  return 0.85
    if c >= 0.30:  return 1.0
    if c >= 0.15:  return 1.2
    return 1.5


def _map_curiosity(c):
    """curiosity 映射：越高 → 探索越多 → 醒越频繁"""
    if c >= 0.80:  return 0.5
    if c >= 0.65:  return 0.7
    if c >= 0.45:  return 0.9
    if c >= 0.30:  return 1.0
    if c >= 0.15:  return 1.2
    return 1.4


def _map_valence(v):
    """valence 映射：负面 → 醒得慢（不想动）"""
    if v >= 0.60:   return 0.85
    if v >= 0.30:   return 0.95
    if v >= -0.10:  return 1.0
    if v >= -0.30:  return 1.2
    if v >= -0.50:  return 1.5
    return 1.8


def _map_arousal(a):
    """arousal 映射：激动 → 节奏加快"""
    if a >= 0.70:   return 0.7
    if a >= 0.55:   return 0.85
    if a >= 0.35:   return 1.0
    if a >= 0.20:   return 1.1
    return 1.2


def drive_modifier():
    """驱力/情绪综合产生的 next_wake 调制倍数

    返回 (multiplier, reason)
    - < 1.0 → 醒得更频繁
    - > 1.0 → 醒得更慢
    - clamp 到 [0.4, 2.0]

    设计原则：
    - 每个维度独立映射
    - 平均值综合（避免任何单一驱力过度主导）
    - 只做"调制"，不替换 LLM 的决定
    """
    try:
        st = P.get_current_state()
    except Exception as e:
        return 1.0, f"读取失败({e})"

    d = st.get("drives", {})
    e = st.get("emotion", {})

    conn = float(d.get("connection", 0.5))
    cur = float(d.get("curiosity", 0.5))
    v = float(e.get("valence", 0.2))
    a = float(e.get("arousal", 0.4))

    m_conn = _map_connection(conn)
    m_cur = _map_curiosity(cur)
    m_v = _map_valence(v)
    m_a = _map_arousal(a)

    mult = (m_conn + m_cur + m_v + m_a) / 4.0
    mult = max(0.4, min(2.0, mult))

    reason = (
        f"conn×{m_conn:.2f} cur×{m_cur:.2f} "
        f"v×{m_v:.2f} a×{m_a:.2f}"
    )
    return mult, reason