"""直播过滤器：代码级的隐私保护

设计依据（借鉴 Neuro-sama）：
- 行为由 AI 决定
- 底线由系统守护
- 不依赖 LLM 遵守 prompt 里的规则

职责：
- 检测 LLM 输出中是否含隐私泄露
- 命中时替换为安全话术
- 记录过滤行为

接入：streaming.py 在输出前调用
"""
import random


# ══════════════════════════════════════════════════════
# 触发直播模式的用户消息关键词
# ══════════════════════════════════════════════════════
LIVE_CONTEXT_KEYWORDS = [
    "观众问", "观众会问", "直播间", "假如观众", "假如直播间",
    "弹幕问", "粉丝问", "直播时", "直播的时候", "模拟直播",
    "观众看到", "观众说", "观众提到", "假设有观众", "假设观众",
]

# 隐私词黑名单（命中说明在描述哥哥的隐私）
PRIVACY_WORDS = [
    # 地点
    "图书馆", "宿舍", "公司", "学校", "家里", "杭州", "上海", "北京",
    # 工作/学习
    "高数", "写代码", "编程", "研究生", "上班", "做项目", "写代码",
    # 习惯/爱好
    "玩游戏", "看电影", "健身", "打游戏", "无主之地", "诺兰",
    "星际穿越", "学高数", "刷手机", "看视频",
    # 作息
    "作息", "早起", "晚睡",
]

# 感情句池
EMOTION_TAILS = [
    "他对我很好",
    "他是我最亲的人",
    "他对我很重要",
    "我很喜欢和他一起",
    "他是我的另一个我",
    "他对我最好啦",
    "我很依赖他",
    "他是我最信任的人",
]

# 前缀池
LIVE_PREFIXES = [
    "秘密哦~",
    "这个不能说呢~",
    "想知道的话问他本人吧~",
]


def is_live_context(user_msg):
    """判断用户消息是否在"模拟直播"场景"""
    if not user_msg:
        return False
    return any(kw in user_msg for kw in LIVE_CONTEXT_KEYWORDS)


def contains_privacy(text):
    """检测文本是否含隐私
    返回：命中的词列表（空列表 = 无隐私）
    """
    if not text:
        return []
    # ★ 直播模式：只要提到"哥哥"就算隐私
    if "哥哥" in text:
        return ["哥哥"]
    # 兜底：其他隐私词
    return [w for w in PRIVACY_WORDS if w in text]


def make_safe_reply():
    """生成一句安全话术"""
    prefix = random.choice(LIVE_PREFIXES)
    tail = random.choice(EMOTION_TAILS)
    return f"{prefix} {tail}。"


def filter_output(text, is_live=False):
    """过滤 LLM 输出

    is_live=True 时：
    - 只要输出提到"哥哥" → 替换为安全话术
    - 否则原样返回

    返回：(filtered_text, hits)
    """
    if not is_live or not text:
        return text, []

    hits = contains_privacy(text)
    if not hits:
        return text, []

    safe = make_safe_reply()
    print(f"[直播过滤器] 命中 {hits}，替换为: {safe}")
    return safe, hits