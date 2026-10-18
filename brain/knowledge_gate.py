"""知识门控：判断一个问题该不该搜索"""
import re
import json
from brain import l0_model

_TOPIC_MATCH_MIN = 0.5
_CONF_SKIP = 0.6
_CONF_CHECK = 0.35
_MIN_LEN = 4

_CHITCHAT_PATTERNS = [
    r"^(早上|中午|下午|晚上|夜里)好", r"^(早|晚)安", r"在吗$", r"在不在", r"^你好", r"^嗨", r"^hi", r"^hello",
    r"^再见", r"^拜拜", r"^谢谢你", r"^感谢", r"^哈哈+$", r"^嘿嘿+$", r"^嗯+$", r"^哦+$", r"^好$", r"^好的$",
    r"^明白", r"^懂了", r"^吃饭了吗", r"^吃了吗", r"^睡了?吗", r"^怎么样", r"^怎么样啦",
    r"^继续$", r"^还有吗$", r"^还有呢$", r"^别的呢$", r"^然后呢$", r"^接着呢$", r"^再来$",
    r"^说说看$", r"^说说吧$", r"^讲讲$", r"^展开$", r"^你觉得.{0,8}(呢|吗|吧)?[？?]?$", r"^你认为.{0,8}(呢|吗|吧)?[？?]?$",
    r"^还有(什么|哪些).{0,12}[？?]?$", r"^(什么|哪些).{0,6}(重要|好|对|有用).{0,6}[？?]?$",
    r"^你还(知道|了解)(什么|哪些).{0,10}[？?]?$", r"^聊点别的$", r"^换个话题$",
]

def _is_chitchat(user_msg):
    s = (user_msg or "").strip()
    if not s: return True
    if len(s) < _MIN_LEN: return True
    for pat in _CHITCHAT_PATTERNS:
        if re.search(pat, s): return True
    return False

def _match_l0_topic(user_msg):
    opinions = l0_model.list_opinions()
    if not opinions: return None, None
    s = (user_msg or "").strip()
    if not s: return None, None
    best = (None, None, 0.0)
    for topic, op in opinions.items():
        if not topic: continue
        if topic in s:
            score = 1.0
        else:
            words = re.findall(r"[\u4e00-\u9fa5]{2,}", topic)
            hit = sum(1 for w in words if w in s)
            score = hit / max(1, len(words)) if words else 0.0
        if score > best[2]:
            best = (topic, op, score)
    if best[2] >= _TOPIC_MATCH_MIN:
        return best[0], best[1]
    return None, None

def _is_knowledge_query(user_msg):
    if not user_msg: return False
    s = user_msg.strip()
    _RELATIVE_STARTS = ("未来", "长期", "近期", "以后", "接下来", "后面", "他们", "它", "这些", "那些", "这种", "那种")
    if any(s.startswith(p) for p in _RELATIVE_STARTS) and len(s) <= 15: return False
    _SELF_REFLECTION_KEYWORDS = ("关于我", "我是什么样", "我的性格", "我的外貌", "我是谁", "关于哥哥", "哥哥是什么样")
    if any(k in s for k in _SELF_REFLECTION_KEYWORDS): return False
    
    # 视觉指令不走搜索
    _VISION_CMDS = ["看看这张", "看图", "照片", "屏幕", "看看我"]
    if any(w in s for w in _VISION_CMDS): return False

    _FIRST_PERSON_SIGNALS = ["我觉得", "我认为", "我想", "我希望", "我希望你", "我打算", "我喜欢", "我不喜欢", "我想要", "我需要", "我会", "我要", "我的", "我最近"]
    for sig in _FIRST_PERSON_SIGNALS:
        if sig in s: return False
    if s.startswith(("我希望", "想让你", "要你", "让你", "不要", "别", "不需要")): return False
    if len(s) < 8: return False
    
    _QUESTION_MARKERS = ["是什么", "什么是", "为什么", "怎么样", "怎么", "如何", "哪些", "哪种", "哪个", "什么地方", "什么时间", "多少", "能不能", "可不可以", "是否可以", "是否", "解释一下", "说说看", "讲讲", "介绍一下"]
    has_qmark = ("?" in s) or ("？" in s)
    has_qword = any(w in s for w in _QUESTION_MARKERS)
    if has_qmark and has_qword: return True
    if has_qword and len(s) <= 20:
        if not any(sig in s for sig in _FIRST_PERSON_SIGNALS): return True
    
    _SEARCH_CMDS = ["搜一下", "搜一搜", "查一下", "查查", "帮我查", "搜索一下", "帮我搜", "找一下"]
    for cmd in _SEARCH_CMDS:
        if cmd in s: return True
    if "是什么" in s or "什么是" in s: return True
    return False


def quick_check(text):
    # ★ 1. 新增：日常闲聊豁免，绝对不要去搜索
    CHITCHAT_WORDS = [
        "你自己", "你怎么觉得", "你觉得自己", "你感觉", "开心吗",
        "心情", "今天", "玩什么", "干嘛呢", "在干嘛", "吃什么",
        # ★ 新增：游戏专属词汇，防止瞎联网搜索
        "房子", "建筑", "方块", "红石", "村民", "僵尸", "苦力怕", "骷髅", "蜘蛛",
        "我的世界", "Minecraft", "游戏里", "背包", "装备", "合成", "挖矿"
    ]
    if any(w in text for w in CHITCHAT_WORDS):
        return {"verdict": "chitchat", "reason": "日常闲聊/主观感受", "l0_topic": None, "l0_view": "", "l0_confidence": 0.0}

    # ★ 2. 原有逻辑：闲聊/寒暄判断
    if _is_chitchat(text):
        return {"verdict": "chitchat", "reason": "闲聊/寒暄，不需要搜索", "l0_topic": None, "l0_view": "", "l0_confidence": 0.0}
        
    # ★ 3. 原有逻辑：非知识性问题判断
    if not _is_knowledge_query(text):
        return {"verdict": "chitchat", "reason": "不是知识性问题，不需要搜索", "l0_topic": None, "l0_view": "", "l0_confidence": 0.0}
        
    # ★ 4. 原有逻辑：匹配 L0 知识库
    topic, op = _match_l0_topic(text)
    if not op:
        return {"verdict": "search", "reason": "L0 无相关认知，建议搜索", "l0_topic": None, "l0_view": "", "l0_confidence": 0.0}

    # ★ 5. 原有逻辑：置信度分流
    conf = float(op.get("confidence", 0.5))
    if conf >= _CONF_SKIP:
        return {
            "verdict": "skip", 
            "reason": "L0 置信度高，直接回答", 
            "l0_topic": topic, 
            "l0_view": op.get("my_view", ""), 
            "l0_confidence": conf
        }
    elif conf < _CONF_CHECK:
        return {
            "verdict": "search", 
            "reason": "L0 置信度过低，建议搜索", 
            "l0_topic": topic, 
            "l0_view": op.get("my_view", ""), 
            "l0_confidence": conf
        }
    else:
        return {
            "verdict": "check", 
            "reason": "L0 置信度中等，需要 LLM 判断", 
            "l0_topic": topic, 
            "l0_view": op.get("my_view", ""), 
            "l0_confidence": conf
        }


_JUDGE_SYSTEM = """你是绯木的决策助手。判断：绯木现有的认知够不够回答？
现有认知：
话题：{topic}
观点：{view}
置信度：{confidence:.2f}
哥哥的问题：{question}
直接输出 JSON：
{{
  "need_search": true | false,
  "reason": "一句话",
  "search_query": "如果 need_search=true，给出一个搜索关键词"
}}
"""

def judge_need_search(client, provider, user_msg, l0_topic, l0_view, l0_confidence):
    fallback = {"need_search": True, "reason": "LLM 判断失败，默认搜索", "search_query": user_msg[:40]}
    if not client or not provider: return fallback
    system = _JUDGE_SYSTEM.format(topic=l0_topic, view=(l0_view or "")[:200], confidence=float(l0_confidence), question=user_msg[:200])
    try:
        r = client.chat.completions.create(
            model=provider["model"],
            messages=[{"role": "system", "content": system}, {"role": "user", "content": "判断。"}],
            timeout=30, temperature=0.2, max_tokens=200, extra_body={"keep_alive": "24h", "think": False},
        )
        text = (r.choices[0].message.content or "").strip()
        if not text: return fallback
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start: return fallback
        data = json.loads(text[start:end + 1])
        return {"need_search": bool(data.get("need_search", True)), "reason": (data.get("reason") or "").strip(), "search_query": (data.get("search_query") or user_msg[:40]).strip()}
    except Exception as e:
        print(f"[知识门控] judge 失败: {e}")
        return fallback