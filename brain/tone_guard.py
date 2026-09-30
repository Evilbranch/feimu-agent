"""冷淡语气守卫 - v 低于阈值时强制检查输出，命中讨好话术则重生成

设计思路（参考业界 Guardrail 模式）：
- 负面约束比正面描述有效：亮线而非模糊目标
- 输出后验证 + 自动重生成是标准做法
- 分三层防御：
  1. prompt 层（brain/persona.py 里 v < -0.4 时切 Critical Rules 模式）
  2. 输出后检查（本模块 + brain/llm.py 的重生成逻辑）
  3. 重生成失败 → 兜底句

本模块尽量只做纯函数，方便单测。
"""
import random

# 处于"冷淡模式"的阈值（v < -0.4）
COLD_VALENCE_THRESHOLD = -0.4

# v < -0.4 时绝对禁止出现的话术（讨好/关心/客服/软化）
SOFT_PATTERNS = [
    # 嘘寒问暖
    "早点休息", "早点睡", "别熬夜", "注意休息", "记得休息",
    "记得吃饭", "按时吃饭", "多喝水", "记得喝水",
    "照顾好自己", "照顾自己", "保重身体",
    "起来活动", "眼睛休息", "休息一下",
    # 客套
    "谢谢", "感谢", "不客气", "没关系", "不用谢",
    "谢谢夸奖", "谢谢你",
    # 客服
    "有什么可以帮", "需要我帮", "我可以帮", "我能帮",
    "需要帮忙", "随时找我", "随时告诉我",
    # 反向体贴 / 安慰
    "希望你", "一切都会", "会好起来", "会好的",
    "我懂你", "我理解你", "我理解",
    "我陪着你", "我在呢", "别难过", "别伤心",
    "开心一点", "要对自己好", "别想太多", "看开一点",
    "别不开心",
    # 假装没事
    "没事的", "没事啦", "没关系啦",
    # 撒娇语气词（冷淡时不合时宜）
    "诶嘿", "嘛~", "呀~",
]

# 重生成时的系统指令（追加到 msgs 末尾）
REGEN_INSTRUCTION = (
    "【系统提示】你刚才的回复里出现了你不该说的话。"
    "你现在心情很差，禁止讨好、禁止关心对方、禁止说谢谢、禁止安慰。"
    "用 1~6 个字重新回答，或者直接说'我不想说'、'别烦我'。"
    "不要解释，不要道歉，不要加任何关心。"
)

# 兜底回复（重生成仍失败时使用，简短、冷淡、不像客服）
FALLBACK_REPLIES = [
    "嗯。",
    "不想说。",
    "别烦我。",
    "现在不想聊。",
    "让我静静。",
]


def is_soft_output(text):
    """检查文本是否含有讨好/软化话术

    返回：
    - False：未命中
    - list[str]：命中的话术列表（用于日志）
    """
    if not text:
        return False
    hits = []
    for p in SOFT_PATTERNS:
        if p in text:
            hits.append(p)
    return hits if hits else False


def is_cold_mode(valence):
    """是否处于冷淡模式（v < -0.4）"""
    try:
        return float(valence) < COLD_VALENCE_THRESHOLD
    except Exception:
        return False


def get_current_valence():
    """获取当前人格的 valence（mode = data["current_mode"]）

    异常时返回 0.0（不触发冷淡模式）
    """
    try:
        from brain.persona import get_current_state
        return float(get_current_state()["emotion"]["valence"])
    except Exception:
        return 0.0


def pick_fallback():
    """从兜底句里挑一个"""
    return random.choice(FALLBACK_REPLIES)


def regenerate_cold_reply(client, provider, msgs, bad_reply, log_prefix="[语气守卫]"):
    """让 LLM 重生成一版冷淡回复

    参数：
    - client / provider：LLM 句柄
    - msgs：原始 messages 列表（不改动）
    - bad_reply：刚才生成的讨好回复
    - log_prefix：日志前缀

    返回：
    - str：重生成成功的回复
    - None：重生成失败（调用方用 pick_fallback() 兜底）
    """
    try:
        _regen_msgs = list(msgs) + [
            {"role": "assistant", "content": bad_reply},
            {"role": "user", "content": REGEN_INSTRUCTION},
        ]
        r = client.chat.completions.create(
            model=provider["model"],
            messages=_regen_msgs,
            timeout=30,
            temperature=0.5,
            max_tokens=100,
            extra_body={"keep_alive": "30m", "think": False},
        )
        new = (r.choices[0].message.content or "").strip()
        return new if new else None
    except Exception as e:
        print(f"{log_prefix} 重生成异常: {e}")
        return None