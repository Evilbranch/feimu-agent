"""内部独白 - 对话前的"心里在想什么"

每轮对话前跑一次，生成 1~2 句"内心底色"
注入主 prompt，让她的话带"刚才想什么"的质感
不输出给用户

设计：
- 5 分钟内不重复跑（省 LLM 调用）
- 不能编造（素材限于 L0 / L2 / 最近对话）
- 只调制语气，不取代回复
"""
import os
import json
import time
import random

from core.constants import DATA_DIR


_last_voice_ts = [0]
_last_voice = [""]
_last_turn_gap = [0]

MIN_INTERVAL = 300      # 5 分钟
MAX_LEN = 40


_VOICE_SYSTEM = """你是绯木。哥哥马上就要跟你说话了。

在他说之前，你心里此刻在想什么？

【规则】
- 1~2 句，40 字以内
- 像小声嘀咕，不是写报告
- 用第一人称"我"
- 可以是想一件正在琢磨的事、一点小情绪、一个小小的期待
- **不要**编造没发生的事
- **不要**说"上次""之前""昨天"这种时间指代
- **不要**说"他""她"——只用"哥哥"或"你"
- **不要**说"看到""听到"
- **不要**打招呼、不要反问

【正例】
· 我有点想他
· 刚才在琢磨虚拟主播的事，还没想清
· 我不知道今天该跟他说什么
· 有点困，但不想睡
· 想问他昨晚那个问题

【反例】
· 我在等他跟我说话（元描述）
· 今天天气真好（编造）
· 哥哥是不是也在想我（反问）
· 我想补充一下（元描述）

如果实在没什么想说的，只回一个「…」。

直接输出，不要引号，不要前后缀。"""


def _get_recent_dialogue(history, max_turns=4):
    """从 history 里提取最近对话"""
    recent = history[-max_turns * 2:] if history else []
    lines = []
    for m in recent:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"哥哥：{content[:40]}")
        elif role == "assistant":
            lines.append(f"我：{content[:40]}")
    return "\n".join(lines) if lines else "（刚刚没什么对话）"


def _get_l0_hint():
    """从 L0 拿一条相关观点作为独白素材"""
    try:
        from brain.l0_model import list_opinions, list_open_questions
        lines = []
        # open_questions（最优先）
        oqs = list_open_questions("exploring")
        if oqs:
            q = random.choice(oqs)
            lines.append(f"我最近一直想：{q.get('question', '')[:40]}")
        # opinions
        ops = list_opinions()
        if ops:
            topics = list(ops.keys())
            if topics:
                t = random.choice(topics)
                lines.append(f"我对「{t}」这件事有点想法")
        return "\n".join(lines[:2]) if lines else ""
    except Exception:
        return ""


def _clean_voice(text):
    """清洗 LLM 返回的独白"""
    if not text:
        return ""
    text = text.strip()
    for ch in ['"', "'", "「", "」", "《", "》"]:
        text = text.strip(ch)
    text = text.strip()

    if text in ("…", "...", "。。。", "。", "嗯", "算了", "没什么"):
        return ""

    # 编造过滤
    _FAB = ["看到", "听到", "闻到", "上次", "之前你", "昨天",
            "刚刚你", "刚看到", "刚到"]
    if any(w in text for w in _FAB):
        return ""

    # 元描述过滤
    _META = ["想补充", "应该问", "应该提醒", "决定问", "决定说",
             "准备说", "准备问", "我在等"]
    if any(w in text for w in _META):
        return ""

    # 第三人称
    if "他" in text or "她" in text:
        return ""

    # 反问
    if text.endswith("?") or text.endswith("？"):
        return ""

    if len(text) > MAX_LEN:
        text = text[:MAX_LEN]

    return text


def generate_inner_voice(client, provider, history, user_msg):
    """生成内心独白

    返回字符串，空字符串表示没有独白
    """
    now = time.time()
    gap = now - _last_voice_ts[0]

    # 5 分钟内不重跑（除了第一次）
    if gap < MIN_INTERVAL and _last_voice[0]:
        return _last_voice[0]

    _last_voice_ts[0] = now
    _last_turn_gap[0] = gap

    recent = _get_recent_dialogue(history)
    l0_hint = _get_l0_hint()

    # 读当前状态
    _state_line = ""
    try:
        from brain.persona import get_current_state
        st = get_current_state()
        e = st.get("emotion", {})
        label = e.get("label", "平静")
        _state_line = f"我现在情绪：{label}"
    except Exception:
        pass

    prompt = (
        f"【最近对话】\n{recent}\n\n"
        f"【哥哥刚刚对我说】\n{user_msg}\n\n"
        f"【我的状态】\n{_state_line}\n"
    )
    # ★ 只有当用户没说情绪类的话时，才允许注入 L0
    _emotion_keywords = ["累", "烦", "难过", "痛", "哭", "抑郁", "孤独", "讨厌"]
    _has_emotion = any(w in user_msg for w in _emotion_keywords)
    
    if _has_emotion:
        prompt += "\n【注意】哥哥现在情绪不好，你不要提什么虚拟主播或技术，就想想怎么安慰他。\n"
    elif l0_hint:
        prompt += f"\n【我最近在想】\n{l0_hint}\n"
    prompt += "\n哥哥马上要跟我说话了。此刻，我心里在想什么？"

    try:
        r = client.chat.completions.create(
            model=provider["model"],
            messages=[
                {"role": "system", "content": _VOICE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            timeout=20,
            temperature=0.7,
            max_tokens=50,
            extra_body={"keep_alive": "30m", "think": False},
        )
        text = (r.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[内心独白] LLM 异常: {e}")
        _last_voice[0] = ""
        return ""

    cleaned = _clean_voice(text)
    _last_voice[0] = cleaned

    if cleaned:
        print(f"[内心独白] {cleaned}")
    return cleaned


def build_inner_voice_prompt(voice):
    """把独白包装成 prompt 片段"""
    if not voice:
        return ""
    return (
        "\n\n【你此刻心里在想】"
        f"\n{voice}"
        "\n\n（这是背景，不是必须说的话。"
        "回应用户时，语气可以带一点这个底色——"
        "但不要直接把它说出来，除非自然合适。）"
    )


def get_last_voice():
    """供调试用"""
    return _last_voice[0]