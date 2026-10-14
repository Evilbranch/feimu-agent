"""知识门控：判断一个问题该不该搜索

对话内学习循环的第一道关卡：
- 先查 L0/L1 有没有已有认知
- 决定 skip / check / search

设计原则：
- 规则优先，LLM 兜底
- 只对"有 L0 命中"的情况调 LLM
- 普通闲聊不触发（不拖慢对话）
"""
import re

from brain import l0_model


_TOPIC_MATCH_MIN = 0.5
_CONF_SKIP = 0.6
_CONF_CHECK = 0.35
_MIN_LEN = 4

_CHITCHAT_PATTERNS = [
    r"^(早上|中午|下午|晚上|夜里)好",
    r"^(早|晚)安",
    r"在吗$", r"在不在",
    r"^你好",
    r"^嗨", r"^hi", r"^hello",
    r"^再见", r"^拜拜",
    r"^谢谢你", r"^感谢",
    r"^哈哈+$", r"^嘿嘿+$",
    r"^嗯+$", r"^哦+$", r"^好$", r"^好的$",
    r"^明白", r"^懂了",
    r"^吃饭了吗", r"^吃了吗",
    r"^睡了?吗",
    r"^怎么样", r"^怎么样啦",
]


def _is_chitchat(user_msg):
    """判断是不是闲聊/寒暄"""
    s = (user_msg or "").strip()
    if not s:
        return True
    if len(s) < _MIN_LEN:
        return True
    for pat in _CHITCHAT_PATTERNS:
        if re.search(pat, s):
            return True
    return False


def _match_l0_topic(user_msg):
    """从 L0 opinions 里找匹配的 topic"""
    opinions = l0_model.list_opinions()
    if not opinions:
        return None, None

    s = (user_msg or "").strip()
    if not s:
        return None, None

    best = (None, None, 0.0)
    for topic, op in opinions.items():
        if not topic:
            continue
        # 1. topic 直接出现在用户消息里
        if topic in s:
            score = 1.0
        else:
            # 2. 关键词匹配
            words = re.findall(r"[\u4e00-\u9fa5]{2,}", topic)
            hit = sum(1 for w in words if w in s)
            score = hit / max(1, len(words)) if words else 0.0

        if score > best[2]:
            best = (topic, op, score)

    if best[2] >= _TOPIC_MATCH_MIN:
        return best[0], best[1]
    return None, None


def quick_check(user_msg):
    """快速规则判断

    返回：
    {
        "verdict": "skip" | "check" | "search" | "chitchat",
        "l0_topic": str | None,
        "l0_confidence": float,
        "l0_view": str,
        "reason": str,
    }
    """
    result = {
        "verdict": "chitchat",
        "l0_topic": None,
        "l0_confidence": 0.0,
        "l0_view": "",
        "reason": "",
    }

    if _is_chitchat(user_msg):
        result["reason"] = "闲聊/寒暄，不需要搜索"
        return result

    topic, op = _match_l0_topic(user_msg)
    if not op:
        result["verdict"] = "search"
        result["reason"] = "L0 无相关认知，建议搜索"
        return result

    conf = float(op.get("confidence", 0.5))
    result["l0_topic"] = topic
    result["l0_confidence"] = conf
    result["l0_view"] = op.get("my_view", "")

    if conf >= _CONF_SKIP:
        result["verdict"] = "skip"
        result["reason"] = f"L0 已有高置信认知（{conf:.2f}）"
    elif conf >= _CONF_CHECK:
        result["verdict"] = "check"
        result["reason"] = f"L0 confidence 中等（{conf:.2f}），需 LLM 判断"
    else:
        result["verdict"] = "search"
        result["reason"] = f"L0 confidence 低（{conf:.2f}），直接搜索"

    return result


_JUDGE_SYSTEM = """你是绯木的决策助手。
哥哥问了一个问题，请判断：绯木现有的认知够不够回答？

现有认知：
话题：{topic}
观点：{view}
置信度：{confidence:.2f}

哥哥的问题：{question}

判断标准：
- 如果现有观点能覆盖问题的核心 → 不需要搜索
- 如果问题涉及新细节、新角度，现有观点不够 → 需要搜索
- 如果问题是对现有观点的追问 → 可能不需要搜索

直接输出 JSON，不要解释：
{{
  "need_search": true | false,
  "reason": "一句话",
  "search_query": "如果 need_search=true，给出一个搜索关键词"
}}
"""


def judge_need_search(client, provider, user_msg, l0_topic, l0_view,
                       l0_confidence):
    """LLM 判断：已有认知够不够回答"""
    fallback = {
        "need_search": True,
        "reason": "LLM 判断失败，默认搜索",
        "search_query": user_msg[:40],
    }

    if not client or not provider:
        return fallback

    system = _JUDGE_SYSTEM.format(
        topic=l0_topic,
        view=(l0_view or "")[:200],
        confidence=float(l0_confidence),
        question=user_msg[:200],
    )

    try:
        import json
        r = client.chat.completions.create(
            model=provider["model"],
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "判断。"},
            ],
            timeout=30,
            temperature=0.2,
            max_tokens=200,
            extra_body={"keep_alive": "24h", "think": False},
        )
        text = (r.choices[0].message.content or "").strip()
        if not text:
            return fallback
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return fallback
        data = json.loads(text[start:end + 1])
        return {
            "need_search": bool(data.get("need_search", True)),
            "reason": (data.get("reason") or "").strip(),
            "search_query": (data.get("search_query") or user_msg[:40]).strip(),
        }
    except Exception as e:
        print(f"[知识门控] judge 失败: {e}")
        return fallback