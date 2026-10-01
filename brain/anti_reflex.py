"""反问硬拦截 - 检测连续反问并强制改结尾

理论依据：
- 对话引导机制导致模型默认用提问延续对话
- 软约束（prompt）执行力弱，硬约束（输出后处理）确定性强
- 检测不只看标点：还看末尾语气词（吗/嘛/呢），避免被修正后"洗白"
- 修正不只改末尾问号：整段问号全改，避免"中间问号"漏网
- 计数用"最近 N 轮里有 M 轮反问"，而非"连续"，避免修正后重置
"""
import re


# 真反问语气词（末尾出现=反问信号）
# 注意：故意不含"吧"，因为"说吧""好吧"是陈述语气
_QUESTION_PARTICLES = ("吗", "嗎", "嘛", "呢", "么", "麼")

_TRAILING_PUNCT = r'[。.！!？?…·~～\s\u201c\u201d\u2018\u2019）)」』】\]"\' ]+$'


def _strip_trailing(text):
    return re.sub(_TRAILING_PUNCT, '', text.strip())


def has_question_ending(text):
    """检测是否以问句结尾

    - "你今天怎么样？" → True
    - "你有啥事儿嘛。" → True（末尾"嘛"，修正后仍被识别）
    - "还没呢，哥哥你吃了没。" → False（末尾"没"不在语气词表）
    - "在呢，哥哥有啥事儿就说吧" → False（末尾"吧"不算）
    """
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

    return False


def count_recent_question_endings(history, max_check=4):
    """统计最近 N 轮 assistant 回复里有几轮以问句结尾

    关键改动：不再要求"连续"，而是"最近 N 轮里有几轮"。
    这样修正后的文本不会重置计数。
    """
    count = 0
    checked = 0
    for m in reversed(history):
        if m.get("role") != "assistant":
            continue
        checked += 1
        if checked > max_check:
            break
        if has_question_ending(m.get("content", "")):
            count += 1
    return count


def strip_trailing_question(text):
    """把反问改成陈述句

    策略：
    1. 特殊句式优先替换
    2. 去掉末尾语气词（吗/嘛/呢/么）
    3. 把整段所有问号改为句号
    """
    if not text:
        return text

    t = text.rstrip()

    # 特殊句式替换
    replacements = [
        (r"要不要(.+?)[？?]", r"想\1也可以"),
        (r"有没有(.+?)[？?]", r"\1也可以说说"),
        (r"有什么(.+?)[？?]", r"有\1的话可以说说"),
    ]
    for pat, rep in replacements:
        if re.search(pat, t):
            t = re.sub(pat, rep, t)

    # 去掉末尾语气词 + 标点，改为句号
    t = re.sub(
        r'(吗|嗎|嘛|呢|么|麼)[。.！!？?…·~～\s"\'）)」』】\]]*$',
        '。',
        t
    )

    # 整段所有问号改成句号
    t = t.replace("？", "。").replace("?", "。")

    # 去重句号
    t = re.sub(r'。{2,}', '。', t)

    # 兜底
    if t and not re.search(r'[。.！!…~～]$', t):
        t = t + "。"

    return t


def needs_anti_reflex(history, threshold=2, max_check=4):
    """是否触发反问拦截（最近 max_check 轮里有 threshold 轮反问）"""
    return count_recent_question_endings(history, max_check=max_check) >= threshold


def apply_anti_reflex(reply, history, threshold=2, max_check=4):
    """主入口：对回复应用反问拦截

    返回 (处理后的回复, 是否命中)
    """
    if not reply:
        return reply, False

    if not has_question_ending(reply):
        return reply, False

    if not needs_anti_reflex(history, threshold=threshold, max_check=max_check):
        return reply, False

    fixed = strip_trailing_question(reply)
    return fixed, True