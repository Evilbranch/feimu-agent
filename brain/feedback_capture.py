import time

"""用户反馈捕获：肯定/否定

从用户的消息里判断：
- 是不是对绯木上一句的肯定/否定
- 针对哪个 L0 topic
- 怎么更新 confidence
"""
from brain import l0_model


_POSITIVE_STRONG = [
    "对的", "对的呀", "对呀", "对哦", "没错", "正确", "就是这样的",
    "完全正确", "答对了", "说的对", "说得对", "懂了", "明白了",
    "原来如此", "有道理", "就是这么回事",
]

_POSITIVE_WEAK_START = [
    "对", "是", "嗯", "好", "可以", "没错", "是的",
]

_NEGATIVE_STRONG = [
    "不对", "不是", "错的", "答错了", "说错了", "搞错了",
    "不是这样", "不是这样的", "不是这个意思", "你错了",
    "重新想", "再想想", "再搜", "重新搜", "你再想",
    "胡扯", "瞎说", "胡编", "编的",
]

_RESEARCH_VERBS = [
    "重新想", "再想一下", "再想想", "重想",
    "重搜", "再搜", "重新搜",
    "重新回答", "重新答", "重答", "重说", "重讲",
    "重新说", "重新讲", "再说一遍", "重新来",
    "你重新", "请你重新",
]

_NEGATIVE_WEAK_START = [
    "不", "别", "错", "否",
]

_MAX_FEEDBACK_LEN = 15
_POS_DELTA = 0.1
_NEG_DELTA = -0.15


def _is_pure_feedback(user_msg):
    """判断是不是单纯反馈"""
    s = (user_msg or "").strip().rstrip("。！!，,、. ")
    if not s:
        return False
    # 有重搜意图 → 不看长度
    for v in _RESEARCH_VERBS:
        if v in s:
            return True
    # 否定开头 → 不看长度
    for w in _NEGATIVE_WEAK_START:
        if s.startswith(w):
            return True
    # 普通消息限制长度
    if len(s) > _MAX_FEEDBACK_LEN:
        return False
    return True


def detect_feedback(user_msg, last_reply=""):
    """判断用户消息是不是反馈

    返回：
    {
        "type": "positive" | "negative" | "neutral",
        "topic": str | None,
        "confidence_delta": float,
        "reason": str,
    }
    """
    result = {
        "type": "neutral",
        "topic": None,
        "confidence_delta": 0.0,
        "reason": "",
    }

    if not _is_pure_feedback(user_msg):
        result["ts"] = time.time()
        return result

    s = (user_msg or "").strip().rstrip("。！!，,、. ")

    # 1. 强否定
    for w in _NEGATIVE_STRONG:
        if w in s:
            result["type"] = "negative"
            result["confidence_delta"] = _NEG_DELTA
            result["reason"] = f"命中强否定：{w}"
            return result

    # 1.5 重搜意图（也算否定）
    for v in _RESEARCH_VERBS:
        if v in s:
            result["type"] = "negative"
            result["confidence_delta"] = _NEG_DELTA
            result["reason"] = f"命中重搜意图：{v}"
            return result

    # 2. 强肯定
    for w in _POSITIVE_STRONG:
        if w in s:
            result["type"] = "positive"
            result["confidence_delta"] = _POS_DELTA
            result["reason"] = f"命中强肯定：{w}"
            return result

    # 3. 弱否定（句首）
    for w in _NEGATIVE_WEAK_START:
        if s.startswith(w):
            result["type"] = "negative"
            result["confidence_delta"] = _NEG_DELTA
            result["reason"] = f"句首弱否定：{w}"
            return result

    # 4. 弱肯定（句首，且后面跟语气词或结束）
    for w in _POSITIVE_WEAK_START:
        if s == w:
            result["type"] = "positive"
            result["confidence_delta"] = _POS_DELTA
            result["reason"] = f"句首弱肯定（独占）：{w}"
            return result
        for suffix in ["的", "呀", "哦", "啊", "呢", "，" ,"了"]:
            if s.startswith(w + suffix):
                result["type"] = "positive"
                result["confidence_delta"] = _POS_DELTA
                result["reason"] = f"句首弱肯定：{w}{suffix}"
                return result

    return result


def apply_feedback(feedback, topic=None):
    """把反馈写回 L0

    返回 (ok, info)
    """
    if not feedback or feedback.get("type") == "neutral":
        return False, {}

    t = topic or feedback.get("topic")
    if not t:
        return False, {"reason": "无 topic"}

    delta = feedback.get("confidence_delta", 0.0)
    if delta == 0:
        return False, {"reason": "delta 为 0"}

    reason = feedback.get("reason", "")
    try:
        ok, new_conf = l0_model.bump_confidence(t, delta, reason=reason)
        return ok, {"topic": t, "new_confidence": new_conf}
    except Exception as e:
        print(f"[反馈] apply 失败: {e}")
        return False, {"reason": str(e)}


def pick_topic_from_context(user_msg, last_topics):
    """从上一轮涉及的 topics 里挑最匹配的一个

    策略：
    1. topic 直接出现在消息里 → 取最长
    2. topic 的核心字都在消息里 → 取核心字最多的
    3. 兜底 → 取最后一个
    """
    if not last_topics:
        return None
    if len(last_topics) == 1:
        return last_topics[0]

    s = (user_msg or "").strip()
    user_chars = set(s)

    # 1. topic 直接出现
    matched = [t for t in last_topics if t and t in s]
    if matched:
        return max(matched, key=len)

    # 2. topic 的核心字都在消息里
    try:
        from brain.l0_model import _topic_core_chars
        candidates = []
        for t in last_topics:
            if not t:
                continue
            core_chars = _topic_core_chars(t)
            if core_chars and core_chars.issubset(user_chars):
                candidates.append((t, len(core_chars)))
        if candidates:
            return max(candidates, key=lambda x: x[1])[0]
    except Exception:
        pass

    # 3. 兜底
    return last_topics[-1]