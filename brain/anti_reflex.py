"""反问硬拦截 - 重生成 + 规则兜底

两层：
1. LLM 重生成：让模型自己改成陈述句（质量好，但不稳定）
2. 规则兜底：重生成后还是问 → 按句切分，剔除反问句（确定性强，可能丢信息）

代价：每轮 +3~5 秒（多一次 LLM 调用）
"""
import re


_QUESTION_PARTICLES = ("吗", "嗎", "嘛", "呢", "么", "麼")
_TRAILING_PUNCT = r'[。.！!？?…·~～\s\u201c\u201d\u2018\u2019）)」』】\]"\' ]+$'

# 反问特征词（句中）
_QUESTION_MARKERS = (
    "怎么样", "怎麼樣", "如何",
    "什么", "什麼", "啥",
    "为什么", "為什麼", "为何",
    "要不要", "是不是", "有没有", "能不能", "可不可以",
)


REGEN_SYSTEM = """你是一个改写助手。

用户给你一句"绯木"说的话（可能包含问句），你把它改写成陈述句。

要求：
- 保留原意和语气，可以是 1~2 句
- 不要以问号结尾
- **完全消除问句**，不是改标点，而是改说法
- 不要出现"吗/呢/嘛/怎么样/什么/如何"这些问句特征词
- 直接输出改写后的文本，不要解释

示例：
输入："哥哥早呢，今天过得怎么样？"
输出："哥哥早。我今天挺好的。"

输入："在呢，哥哥找我有事吗？"
输出："在的，哥哥有事儿就说。"

输入："今天挺平静的。哥哥呢，今天怎么样？"
输出："今天挺平静的。"

输入："还没吃呢。哥哥你吃过了吗？"
输出："还没吃呢。哥哥应该吃过了。"

输入："在的，哥哥有什么事儿吗？"
输出："在的，哥哥有事儿就说。"

输入："还没吃呢。哥哥你刚吃完吗？"
输出："还没吃呢。哥哥应该刚吃完。"

现在，改写下面这句："""


def _strip_trailing(text):
    return re.sub(_TRAILING_PUNCT, '', text.strip())


def has_question_ending(text):
    """检测是否以问句结尾"""
    if not text:
        return False

    t = text.strip()
    t = t.rstrip("\"'\u201c\u201d\u2018\u2019）)」』】] \t\n")

    if t.endswith(("？", "?")):
        return True

    t_clean = _strip_trailing(t)
    if not t_clean:
        return False

    if t_clean.endswith(_QUESTION_PARTICLES):
        return True

    # 末尾是"怎么样/如何/什么/啥"
    if t_clean.endswith(("怎么样", "怎麼樣", "如何", "什么", "什麼", "啥")):
        return True

    return False


def has_inner_question(text):
    """检测是否整段包含问号"""
    if not text:
        return False
    return "？" in text or "?" in text


def is_question_like(text):
    """综合判断：末尾或中间有问号/反问特征"""
    if not text:
        return False
    if has_question_ending(text):
        return True
    if has_inner_question(text):
        return True
    # 检查反问特征词
    t = _strip_trailing(text)
    for m in _QUESTION_MARKERS:
        if m in t:
            return True
    return False


# ══════════════════════════════════════════════════════════
# 规则兜底
# ══════════════════════════════════════════════════════════
def _split_sentences(text):
    """按标点切句，保留标点"""
    if not text:
        return []
    parts = re.split(r'([。！!？?…])', text)
    sentences = []
    buf = ""
    for p in parts:
        buf += p
        if p in "。！!？?…":
            s = buf.strip()
            if s:
                sentences.append(s)
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    return sentences


def _sentence_is_question(sent):
    """判断单句是不是反问"""
    s = sent.strip()
    if not s:
        return False

    if s.endswith(("？", "?")):
        return True

    s_body = re.sub(r'[。！!？?…]+$', '', s)

    # 末尾语气词
    if s_body.endswith(("吗", "嗎", "嘛", "呢", "么", "麼")):
        return True

    # 末尾是"怎么样/如何/什么/啥"
    if s_body.endswith(("怎么样", "怎麼樣", "如何", "什么", "什麼", "啥")):
        return True

    # 含反问特征词
    for m in _QUESTION_MARKERS:
        if m in s_body:
            return True

    return False


def force_declarative(text):
    """规则兜底：剔除反问句，只留陈述句"""
    if not text:
        return text

    sentences = _split_sentences(text)
    if not sentences:
        return text

    kept = [s for s in sentences if not _sentence_is_question(s)]

    if not kept:
        return "嗯。"

    result = "".join(kept)

    if not re.search(r'[。.！!…~～]$', result):
        result = result + "。"

    return result


# ══════════════════════════════════════════════════════════
# LLM 重生成
# ══════════════════════════════════════════════════════════
def regenerate_question(client, provider, bad_reply):
    """让 LLM 重写成陈述句版本"""
    if not bad_reply or not client or not provider:
        return None

    try:
        msgs = [
            {"role": "system", "content": REGEN_SYSTEM},
            {"role": "user", "content": bad_reply},
        ]
        r = client.chat.completions.create(
            model=provider["model"],
            messages=msgs,
            timeout=30,
            temperature=0.5,
            max_tokens=120,
            extra_body={"keep_alive": "30m", "think": False},
        )
        new = (r.choices[0].message.content or "").strip()
        return new if new else None
    except Exception as e:
        print(f"[反问重生成] 异常: {e}")
        return None


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════
def apply_anti_reflex(reply, history=None, client=None, provider=None):
    """主入口：反问拦截

    策略：
    1. 不像反问 → 原样返回
    2. LLM 重生成 → 成功且不含问句特征 → 返回
    3. 规则兜底 → 剔除反问句，只留陈述句

    返回 (处理后的回复, 是否命中)
    """
    if not reply:
        return reply, False

    if not is_question_like(reply):
        return reply, False

    # 1. LLM 重生成
    if client and provider:
        new_reply = regenerate_question(client, provider, reply)
        if new_reply and not is_question_like(new_reply):
            return new_reply, True

    # 2. 规则兜底
    fixed = force_declarative(reply)
    if fixed and fixed != reply:
        return fixed, True

    return reply, False