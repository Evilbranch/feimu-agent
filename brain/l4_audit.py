"""L4 自语编造检查（对内：她自己的思考）

2026-10-14 拆分：
- 原来和 output_audit 共用一套严格规则，导致她无法正常反思（"今天我..." 被拦）
- 现在改为只拦"物理感知"类编造
- 时间/频次词允许（"今天我有点低落"、"最近我总在想"是合法的自我观察）

策略：
- 命中感知/地点编造 → 丢弃这次自语
- 只有时间/频次词 → 允许
"""

# 只保留感知动作和物理地点
_PERCEPTION_WORDS = [
    "听到", "看到", "闻到", "看到过", "听到过",
    "路过", "经过", "遇到", "碰见", "碰到",
    "去了", "走到", "来到", "跑到",
]

_PLACE_WORDS = [
    "在公园", "在外面", "在街上", "在夜市", "在咖啡厅",
    "在家里", "在房间", "在窗边", "在阳台",
    "在商场", "在学校", "在公司", "在医院",
]

_FABRICATION_WORDS = _PERCEPTION_WORDS + _PLACE_WORDS


def find_fabrication(text):
    """返回命中的编造词列表；未命中返回 []"""
    if not text:
        return []
    hits = []
    for w in _FABRICATION_WORDS:
        if w in text:
            hits.append(w)
    return hits


def is_fabricated(text):
    """是否有编造嫌疑。True 表示应该丢弃"""
    return bool(find_fabrication(text))


def sanitize_reflection(text):
    """清洗自语。命中编造词返回空串（丢弃），否则原样返回"""
    if not text:
        return ""
    hits = find_fabrication(text)
    if hits:
        return ""
    return text