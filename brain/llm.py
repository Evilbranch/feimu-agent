"""大模型调用 - 多用户 + 人格引擎 + 关系模式 + 工具调用 + 偏好拒绝 + MC + 情绪优先"""
import time
import json
import re
import threading
import numpy as np
import sounddevice as sd
from core.constants import (BASE_SYSTEM_PROMPT, SHORT_TERM_TURNS, RAG_TOP_K,
    SENSITIVE_KEYWORDS, EXPAND_KEYWORDS)
from core import state
from brain.memory import save_history
from brain.profile import (get_relation_prompt, get_profile_context,
    get_transition_prompt, extract_async)
from brain.persona import (build_persona_prompt, detect_event_from_text,
    bump_turn, evaluate_request)


# ══════════════════════════════════════════════════════════════
# 基础工具函数
# ══════════════════════════════════════════════════════════════
def should_expand(t):
    return any(kw in t for kw in EXPAND_KEYWORDS) if t else False


def is_sensitive(text):
    if not text:
        return False, None
    for kw in SENSITIVE_KEYWORDS:
        if kw in text:
            return True, f"命中【{kw}】"
    return False, None


def _truncate_reply(text, max_sentences=3):
    if not text:
        return text
    parts = re.split(r'([。！？!?])', text)
    sentences = []
    buf = ""
    for p in parts:
        buf += p
        if p in "。！？!?":
            sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    if len(sentences) <= max_sentences:
        return text
    return "".join(sentences[:max_sentences])


def _clean_markdown(text):
    if not text:
        return text
    # 剥离 emoji
    emoji_pat = re.compile(
        "["
        "\U0001F300-\U0001F5FF"
        "\U0001F600-\U0001F64F"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\U00002600-\U000026FF"
        "\U00002700-\U000027BF"
        "\U0001F1E0-\U0001F1FF"
        "\U0000FE00-\U0000FE0F"
        "]+", flags=re.UNICODE)
    text = emoji_pat.sub('', text)
    # 去反引号
    text = re.sub(r'`([^`]*)`', r'\1', text)
    # 星号包裹的整段删掉（动作描写）
    text = re.sub(r'\*[^*]+\*', '', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[\-\*·•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+[.、)）]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\（\d+\）\s*', '', text, flags=re.MULTILINE)
    # 换行 → 逗号，但如果前面已有标点就不加
    text = re.sub(r'([。！？!?])\s*\n+\s*', r'\1', text)
    text = re.sub(r'\n+\s*([。！？!?])', r'\1', text)
    text = re.sub(r'\n+', '，', text)
    text = text.replace("**", "").replace("##", "")
    text = text.replace("：。", "。").replace("：，", "，").replace("：、", "、")
    # 标点清理
    text = re.sub(r'([。！？!?])\s*[，,]', r'\1', text)
    text = re.sub(r'[，,]\s*([。！？!?])', r'\1', text)
    text = re.sub(r'([。！？!?])\s+', r'\1', text)
        # 🆕 汉字/标点之间多余空格
    text = re.sub(r'\s+([，,。！!？?、；;：])', r'\1', text)
    text = re.sub(r'([，,。！!？?、；;：])\s+', r'\1', text)
    text = re.sub(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])', r'\1\2', text)
    text = re.sub(r'[，,]{2,}', '，', text)
    text = re.sub(r'[。.]{2,}', '。', text)
    text = re.sub(r'[！!]{2,}', '！', text)
    text = re.sub(r'[？?]{2,}', '？', text)
    # 单字+句号 → 逗号
    text = re.sub(r'^([啊哦唔嗯哎诶哎呀])\s*[。.]', r'\1，', text)
    # 去常见开场白
    openers = [
        "在这两天的对话中，", "在這兩天的對話中，",
        "根据我们的对话，", "根據我們的對話，",
        "根据我们之前的对话，", "根據我們之前的對話，",
        "根据我们之前的对话记录，", "根據我們之前的對話記錄，",
        "我了解到你", "我了解到，", "我了解到",
        "以下是一些建议：", "以下是一些建議：",
        "如果你希望我提升某些功能，", "如果你希望我提升某些功能：",
        "如果你希望", "如果你想",
    ]
    for op in openers:
        if text.startswith(op):
            text = text[len(op):].lstrip("，,：: ")
    return text.strip()


def _strip_actions(text):
    if not text:
        return text
    text = re.sub(r'[\r\n]+', '。', text)
    text = re.sub(r'（[^）]*）', '', text)
    text = re.sub(r'\([^)]*\)', '', text)
    text = re.sub(r'【[^】]*】', '', text)
    text = re.sub(r'\[[^\]]*\]', '', text)
    text = re.sub(r'\*[^*]+\*', '', text)
    text = re.sub(r'[~～]\s*[。.]+', '~', text)
    text = re.sub(r'[，,]{2,}', '，', text)
    text = re.sub(r'[。.]{2,}', '。', text)
    text = re.sub(r'[！!]{2,}', '！', text)
    text = re.sub(r'[？?]{2,}', '？', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.lstrip("。.，,、 ")
    text = re.sub(r'[。.，,、]\s*$', '', text)
    text = text.strip()
    return text


# ══════════════════════════════════════════════════════════════
# 客服话术过滤
# ══════════════════════════════════════════════════════════════
_CUSTOMER_SERVICE_PATTERNS = [
    # 欢迎/开场
    "欢迎光临", "很高兴为您服务", "很高兴为你服务",
    "有什么可以帮", "有什么可以为您", "有什么可以为你",
    "尽力为您", "尽力为你",
    # 结束/送客
    "感谢您的", "感谢你的", "感谢哥哥",
    "希望对您有帮助", "希望对你有帮助", "希望对哥哥有帮助",
    "请随时", "可以随时",
    "下次再见", "欢迎再来",
    # 转接/等待
    "稍等一下", "稍等片刻",
    "马上为您", "马上为你",
    "正在为您", "正在为你",
    "已为您", "已为你",
    "让我先为您", "让我先为你",
    # 确认/引导
    "请问还有什么", "请问有什么",
    "还有什么需要", "还有其他需要",
    "您可以尝试", "你可以尝试",
    "建议您", "建议你",
    "请按照", "请根据",
    # 安抚
    "我理解您的", "我理解你的",
    "请不要着急", "请你不要着急",
    "请放心", "您放心", "你放心",
    "我会陪着您", "我会陪着你",
    "您别难过", "你别难过",
    "一切都会好起来",
    # 废话
    "这是个好问题",
    "让我想想", "让我考虑一下",
    "抱歉，我", "对不起，我",
    "我不太清楚", "我不知道", "我不了解",
    "建议您咨询", "建议你咨询",
    "咨询专业人士", "咨询专业机构",
    "超出我的能力", "超出我的范围", "超出我的知识",
    "我的知识有限", "我的信息可能", "我的知识可能",
    # 身份暴露
    "作为一个AI", "作为一个人工智能", "作为一个模型", "作为一个助手",
    "我是一个AI", "我是一个人工智能", "我是一个助手", "我只是一个助手",
    "我的职责是", "我的功能是", "我的作用是",
    "我会尽力", "我会努力",
    "我被设计", "我被创造", "我被训练",
    "我能为您", "我能为你",
    "我可以帮您", "我可以帮你",
    "需要帮忙", "需要我帮忙",
    # 过度礼貌
    "劳烦您", "劳烦你",
    "麻烦您", "麻烦你",
    "辛苦您", "辛苦你",
    "敬请",
    # 推销
    "要不要我帮", "要不要我为你", "要不要我帮你",
    "需要我帮", "需要我为你", "需要我帮你",
    "推荐您", "推荐你",
    "请您放心", "请你放心",
    "保证您", "保证你",
    # 被夸时的尴尬
    "谢谢夸奖", "谢谢你的夸奖", "谢谢您的夸奖",
    "让我有点不好意思", "有点不好意思",
    "认真地在陪你", "认真地在陪您",
    "你想聊些什么", "您想聊些什么", "你想聊什么",
    "你是不是想让我", "是不是想让我",
    "比如让绯木",
    "需要我去", "需要我来",
    "我可以去", "我可以来",
    # 常用客服词组
    "为您服务", "为你服务",
    "有什么需要", "有什么可以帮助",
    "随时告诉我", "随时找我",
    "我会支持你", "我会帮助你",
    "让我们试试", "让我们尝试",
    "我能为你做什么", "我能为您做什么",
]


def _filter_customer_service(text):
    """过滤客服话术 + '您'→'你' + 虚拟感知词替换"""
    if not text:
        return text
    original = text
    hit_any = False

    # 1. 字符串匹配（删除）
    for pat in _CUSTOMER_SERVICE_PATTERNS:
        if pat in text:
            text = text.replace(pat, '')
            hit_any = True

    # 2. "您" → "你"
    if '您' in text:
        text = text.replace('您', '你')
        hit_any = True

    # 3. 虚拟感知词替换
    replacements = [
        ("我看到你提到", "你说"),
        ("我看到你说", "你说"),
        ("我听到你说", "你说"),
        ("我看到你发", "你发"),
        ("我注意到", "你说"),
        ("我看到", "你说"),
        ("我听到", "你说"),
    ]
    for old, new in replacements:
        if old in text:
            text = text.replace(old, new)
            hit_any = True

    # 4. 清理多余标点和孤立连接词
    text = re.sub(r'[，,]{2,}', '，', text)
    text = re.sub(r'[！!]{2,}', '！', text)
    text = re.sub(r'[。.]{2,}', '。', text)
    text = re.sub(r'[？?]{2,}', '？', text)
    text = re.sub(r'[，,。]?\s*(或者|还是|比如|然后|所以说)\s*[？?。，,！!]?\s*$', '', text)
    text = re.sub(r'[，,、]\s*$', '', text)
    text = re.sub(r'[，,]\s*([？?！!。])', r'\1', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip().strip("，,。.！!、 ")

    if hit_any:
        print(f"[客服过滤] 原文: {original[:60]}")
        print(f"[客服过滤] 过滤后: {text[:60]}")
    return text


_AI_DISCLOSURE_PATTERNS = [
    r"(我|咱)[^。！？!?，,]{0,8}(是|只是|算是|属于|作为)[^。！？!?，,]{0,10}(AI|人工智能|程序|模型|助手|机器人|虚拟)",
    r"(作为|身为)[^。！？!?，,]{0,6}(AI|人工智能|程序|模型|助手|机器人)",
    r"我(并|也)?不(会|能)真正(感到|感受|体会|难过|开心|有情绪|理解感情)",
    r"我无法(真正)?(感受|体会|感到|有情绪|理解感情)",
    r"我能?为您",
    r"我可以帮您",
    r"有什么(需要|可以)(帮忙|帮到)",
    r"(需要|想要).{0,4}帮忙.{0,6}尽管",
    r"我(只)?是(一个)?(助手|程序|软件)",
]


def _is_ai_disclosure(text):
    if not text:
        return False
    for pat in _AI_DISCLOSURE_PATTERNS:
        if re.search(pat, text):
            return True
    return False


def _shorten_for_mc(text, max_chars=80, max_sentences=2):
    if not text:
        return text
    text = _strip_actions(text)
    result = _truncate_reply(text, max_sentences=max_sentences)
    if len(result) > max_chars:
        head = result[:max_chars + 20]
        last_punct = -1
        for i, c in enumerate(head):
            if c in "。！？!?":
                last_punct = i
        if last_punct > max_chars * 0.5:
            result = head[:last_punct + 1]
        else:
            result = result[:max_chars].rstrip("，,。.！!、 ") + "。"
    return result


def _split_for_mc(text, max_chars=60, max_parts=2):
    if not text:
        return []
    text = _strip_actions(text)
    parts = []
    buf = ""
    for c in text:
        buf += c
        if c in "。！？!?" and len(buf) >= 8:
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())

    result = []
    cur = ""
    for p in parts:
        if len(cur) + len(p) <= max_chars:
            cur += p
        else:
            if cur:
                result.append(cur)
            while len(p) > max_chars:
                cut = p[:max_chars + 10].rfind("，")
                if cut < max_chars // 2:
                    cut = max_chars - 1
                result.append(p[:cut + 1])
                p = p[cut + 1:]
            cur = p
    if cur:
        result.append(cur)
    return result[:max_parts]


# ══════════════════════════════════════════════════════════════
# 强制工具关键词
# ══════════════════════════════════════════════════════════════
FORCE_TOOL_KEYWORDS = {
    "get_current_time": ["现在几点", "现在时间", "今天几号", "今天星期几", "几点了", "现在的日期", "现在是几点"],
    "calculate_date": ["天后是", "天前是", "还有多少天", "还有几天", "距离", "还有多久到", "相差几天", "几号是"],
    "calculate": ["算一下", "算算", "计算", "等于多少", "是多少", "几加", "几乘", "几减", "几除", "百分之", "%是多少"],
    "list_reminders": ["我有什么提醒", "提醒呢", "闹钟呢", "还有几分钟", "我前面设"],
    "set_reminder": ["提醒我", "叫我", "设个闹钟", "定个闹钟"],
    "open_application": ["打开"],
    "close_application": ["关闭", "关掉"],
    "open_url": [".com", ".cn", ".net", ".org", "打开网页", "打开网站"],
    "look_at_camera": ["看看我", "看我在干嘛"],
    "look_at_screen": ["看屏幕", "屏幕上"],
    "web_search": [
        "搜一下", "百度一下", "帮我查", "搜一搜", "搜索一下",
        "最近有什么", "最近有什么新闻", "有什么新闻", "有什么新消息",
        "最新消息", "最新的", "今天的新闻", "最近发生",
        "最近怎么样", "进展如何", "今天发生了什么",
        "查一查", "查查", "找一下", "帮我找",
    ],
    "get_weather": ["天气", "气温", "下雨", "下雪", "温度", "多少度", "冷吗", "热吗"],
    "set_volume": ["音量", "静音"],
    "get_system_info": ["电脑卡不卡", "内存还剩", "电脑状态"],
    "take_note": ["记一下", "记笔记"],
    "list_notes": ["我的笔记"],
    "delete_last_note": ["删掉最后一条笔记"],
    "write_diary": ["总结今天", "写日记"],
    "query_diary": ["昨天聊", "聊了什么"],
    "read_file": ["读一下", "打开文件"],
    "regenerate_diary": ["重写日记", "重写今天"],
    "set_diary": ["日记应该是"],
    "delete_diary": ["删掉日记", "删除日记"],
    "list_diaries": ["有哪些日记"],
    "mc_say": ["跟她说话", "在mc里说", "在mc里打字", "游戏里说"],
    "mc_follow": ["跟着我", "跟随我", "跟紧我", "跟着我来", "跟我走", "陪我走"],
    "mc_come": ["过来", "过来找我", "走到我这", "到我身边", "来我这里"],
    "mc_stop": ["别动了", "停下来", "停止移动", "站住"],
    "mc_status": ["mc里在哪", "mc状态", "你在游戏里"],
    "mc_look": ["看看周围", "mc环境", "周围什么"],
    "mc_mine": ["帮我挖", "挖点", "挖一下", "去挖", "挖矿", "挖石头", "挖木头", "挖个"],
    "mc_attack": ["打它", "打怪", "有怪", "打僵尸", "攻击它", "帮我打", "有僵尸"],
    "mc_collect": ["捡一下", "捡东西", "捡起来", "把东西捡", "去捡"],
    "mc_eat": ["吃点东西", "你吃", "吃东西", "你饿了", "你饱食度"],
    "mc_drop": ["丢给我", "丢个", "扔给我", "丢下", "扔下"],
    "mc_inventory": ["你有什么", "背包里有什么", "背包有什么", "你有东西吗", "看看背包", "你背包"],
}


_EMOTION_EVENT_KEYWORDS = [
    # insult
    "讨厌你", "恨你", "滚", "烦人", "笨", "蠢", "闭嘴", "垃圾", "没用",
    "好烦", "走开", "傻", "白痴", "无聊", "不想理你", "讨厌",
    # share_bad
    "我好难过", "失业", "失败", "失恋", "生病", "被骂", "好累",
    "有点累", "很累", "疲惫", "压力大", "心累", "不开心", "难受",
    "撑不住", "委屈", "想哭", "孤独",
    # praise
    "好可爱", "喜欢你", "爱你", "好棒", "厉害", "好聪明", "真乖",
    # share_good
    "我升职了", "我考上了", "我赢了", "我成功了", "我通过了",
]


def _is_emotion_event(ui):
    if not ui:
        return False
    return any(k in ui for k in _EMOTION_EVENT_KEYWORDS)


def detect_forced_tool(ui):
    if not ui:
        return None
    cleaned = ui.replace("、", "").replace("，", "").replace(",", "").strip()
    if re.match(r'^[开開][A-Za-z\u4e00-\u9fff]', cleaned):
        if not any(x in cleaned for x in ["开会", "开始", "开心", "开玩笑", "开车", "开花"]):
            return "open_application"
    if re.match(r'^[关關][A-Za-z\u4e00-\u9fff]', cleaned):
        if not any(x in cleaned for x in ["关心", "关于", "关联"]):
            return "close_application"
    for tool, kws in FORCE_TOOL_KEYWORDS.items():
        for kw in kws:
            if kw in ui:
                return tool
    return None


CHITCHAT_SIGNALS = [
    "累不累", "累嗎", "累吗", "想你了", "喜欢你", "喜歡你", "爱你", "愛你",
    "无聊", "無聊", "在吗", "在嗎", "早安", "晚安", "你好", "嗨", "hi", "hello",
    "心情", "开心", "開心", "难过", "難過", "困", "饿", "餓", "想你",
]

META_SIGNALS = [
    "你了解我", "你對我的了解", "你对我的了解", "你記得我", "你记得我",
    "我是谁", "我是誰", "关于我", "關於我", "你认识我", "你認識我",
    "你知道我", "你对我", "你對我", "你觉得我", "你覺得我",
    "你觉得你", "你覺得你", "你觉得自己", "你覺得自己", "你认为你", "你認為你",
    "你希望自己", "你覺得你希望", "你想提升", "你想改进", "你想改進",
    "你觉得自己", "你能提升", "你能改進", "你能改进",
    "我们聊过", "我們聊過", "你还记得", "你還記得",
    "这两天我们", "這兩天我們", "最近我们", "最近我們",
    "我们的系统", "我們的系統", "我们的项目", "我們的項目",
    "制作系统", "製作系統", "到现在", "到現在",
    "希望提升", "希望改进", "希望改進", "想提升",
]


def is_chitchat(ui, forced_tool):
    if forced_tool is not None:
        return False
    if not ui:
        return False
    # 🆕 默认走聊天路径（流式），只有命中工具关键词才走工具
    return True


# ══════════════════════════════════════════════════════════════
# 工具 Schema
# ══════════════════════════════════════════════════════════════
def get_tools_schema():
    return [
        {"type": "function", "function": {"name": "mc_say", "description": "在 Minecraft 聊天栏说话。", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
        {"type": "function", "function": {"name": "mc_follow", "description": "让 Minecraft 里的绯木持续跟随某个玩家。⚠️ 本服务器只有一个玩家：EZFM233。target 必须填 'EZFM233'，不要填 '@p' 或 '@a'。", "parameters": {"type": "object", "properties": {"target": {"type": "string", "description": "玩家名，固定填 EZFM233"}}, "required": ["target"]}}},
        {"type": "function", "function": {"name": "mc_come", "description": "让 Minecraft 里的绯木走到某个玩家身边。⚠️ target 必须填 'EZFM233'。", "parameters": {"type": "object", "properties": {"target": {"type": "string", "description": "玩家名，固定填 EZFM233"}}, "required": ["target"]}}},
        {"type": "function", "function": {"name": "mc_stop", "description": "让 Minecraft 里的绯木停止当前移动。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "mc_status", "description": "查询 Minecraft 里绯木的状态。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "mc_look", "description": "让 Minecraft 里的绯木报告周围环境。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "mc_mine", "description": "让 Minecraft 里的绯木挖指定方块。", "parameters": {"type": "object", "properties": {"block_name": {"type": "string"}, "count": {"type": "integer"}}, "required": ["block_name"]}}},
        {"type": "function", "function": {"name": "mc_attack", "description": "让 Minecraft 里的绯木攻击最近的怪物。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "mc_collect", "description": "让 Minecraft 里的绯木捡起附近的掉落物。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "mc_eat", "description": "让 Minecraft 里的绯木吃东西回复饱食度。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "mc_drop", "description": "让 Minecraft 里的绯木把背包里的物品丢到地上。数量可以是数字（如 5）或 'all'（全部丢）。一组=64个。", "parameters": {"type": "object", "properties": {"item_name": {"type": "string"}, "count": {"type": "string"}}, "required": ["item_name"]}}},
        {"type": "function", "function": {"name": "mc_inventory", "description": "查看 Minecraft 里的绯木背包里有什么物品。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "get_current_time", "description": "获取当前准确时间。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "calculate_date", "description": "计算日期。", "parameters": {"type": "object", "properties": {"operation": {"type": "string", "enum": ["add", "diff"]}, "days": {"type": "integer"}, "target_date": {"type": "string"}}, "required": ["operation"]}}},
        {"type": "function", "function": {"name": "calculate", "description": "精确数学计算。", "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
        {"type": "function", "function": {"name": "set_reminder", "description": "设置日程提醒。", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
        {"type": "function", "function": {"name": "list_reminders", "description": "列出所有未完成的提醒。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "open_application", "description": "打开本地软件。", "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}}},
        {"type": "function", "function": {"name": "close_application", "description": "关闭正在运行的软件。", "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}}},
        {"type": "function", "function": {"name": "open_url", "description": "打开网页。", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
        {"type": "function", "function": {"name": "look_at_camera", "description": "看用户本人（摄像头）。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "look_at_screen", "description": "看电脑屏幕（截屏）。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "web_search", "description": "联网搜索。", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
        {"type": "function", "function": {"name": "get_weather", "description": "查询指定城市的实时天气。", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": []}}},
        {"type": "function", "function": {"name": "set_volume", "description": "调整音量。", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["up", "down", "mute", "unmute", "set"]}, "value": {"type": "integer"}}, "required": ["action"]}}},
        {"type": "function", "function": {"name": "get_system_info", "description": "获取电脑系统信息。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "take_note", "description": "记笔记。", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}}},
        {"type": "function", "function": {"name": "list_notes", "description": "查看最近的笔记。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "delete_last_note", "description": "删除最后一条笔记。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "write_diary", "description": "生成今天的日记总结。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "query_diary", "description": "查询过去的日记。", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
        {"type": "function", "function": {"name": "read_file", "description": "读取本地文件。", "parameters": {"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}}},
        {"type": "function", "function": {"name": "regenerate_diary", "description": "重新生成今天的日记。", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "set_diary", "description": "用户口述日记内容覆盖。", "parameters": {"type": "object", "properties": {"content": {"type": "string"}, "date": {"type": "string"}}, "required": ["content"]}}},
        {"type": "function", "function": {"name": "delete_diary", "description": "删除指定日期的日记。", "parameters": {"type": "object", "properties": {"date": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "list_diaries", "description": "列出所有有日记的日期。", "parameters": {"type": "object", "properties": {}}}},
    ]


FEW_SHOT_EXAMPLES = """【标准调用示例】
用户："现在几点" → get_current_time()
用户："三天后是几号" → calculate_date(operation="add", days=3)
用户："123乘456" → calculate(expression="123*456")
用户："5分钟后提醒我开会" → set_reminder(text="5分钟后提醒我开会")
用户："看看我在干嘛" → look_at_camera()
用户："屏幕上是什么" → look_at_screen()
用户："今天天气怎么样" → get_weather(city="杭州")
用户："帮我打开微信" → open_application(app_name="微信")
用户："关闭CMD" → close_application(app_name="CMD")
用户："打开bilibili.com" → open_url(url="bilibili.com")
用户："音量调到50" → set_volume(action="set", value=50)
用户："我电脑卡不卡" → get_system_info()
用户："记一下：明天买牛奶" → take_note(content="明天买牛奶")
用户："我还有什么提醒" → list_reminders()
用户："昨天我们聊了什么" → query_diary(query="昨天")
用户："总结一下今天" → write_diary()
用户："跟着我" → mc_follow(target="EZFM233")
用户："绯木，过来" → mc_come(target="EZFM233")
用户："别动了" → mc_stop()
用户："你在游戏里在哪" → mc_status()
用户："跟玩家说声你好" → mc_say(text="你好呀~")
用户："帮我挖点石头" → mc_mine(block_name="石头", count=3)
用户："打它" → mc_attack()
用户："把地上的东西捡一下" → mc_collect()
用户："你饿不饿" → mc_eat()
用户："丢个圆石给我" → mc_drop(item_name="圆石", count="1")
用户："丢一组圆石给我" → mc_drop(item_name="圆石", count="64")
用户："把圆石全丢给我" → mc_drop(item_name="圆石", count="all")
用户："你背包里有什么" → mc_inventory()"""


# ══════════════════════════════════════════════════════════════
# 工具执行
# ══════════════════════════════════════════════════════════════
def execute_tool(name, args, client=None, provider=None):
    try:
        if name == "mc_say":
            from tools.minecraft import mc_say
            return mc_say(args.get("text", ""))
        elif name == "mc_follow":
            from tools.minecraft import mc_follow
            return mc_follow(args.get("target", ""))
        elif name == "mc_come":
            from tools.minecraft import mc_come
            return mc_come(args.get("target", ""))
        elif name == "mc_stop":
            from tools.minecraft import mc_stop
            return mc_stop()
        elif name == "mc_status":
            from tools.minecraft import mc_status
            return mc_status()
        elif name == "mc_look":
            from tools.minecraft import mc_look
            return mc_look()
        elif name == "mc_mine":
            from tools.minecraft import mc_mine
            return mc_mine(args.get("block_name", ""), args.get("count", 3))
        elif name == "mc_attack":
            from tools.minecraft import mc_attack
            return mc_attack()
        elif name == "mc_collect":
            from tools.minecraft import mc_collect
            return mc_collect()
        elif name == "mc_eat":
            from tools.minecraft import mc_eat
            return mc_eat()
        elif name == "mc_drop":
            from tools.minecraft import mc_drop
            return mc_drop(args.get("item_name", ""), args.get("count", 1))
        elif name == "mc_inventory":
            from tools.minecraft import mc_inventory
            return mc_inventory()
        if name == "get_current_time":
            from tools.time_tools import get_current_time
            return get_current_time()
        elif name == "calculate_date":
            from tools.time_tools import calculate_date
            return calculate_date(
                operation=args.get("operation", "add"),
                days=args.get("days", 0),
                target_date=args.get("target_date")
            )
        elif name == "calculate":
            from tools.calculator import calculate
            return calculate(args.get("expression", ""))
        elif name == "set_reminder":
            from tools.reminders import add as add_reminder
            return add_reminder(args.get("text", "")) or "无法解析提醒时间。"
        elif name == "list_reminders":
            from tools.reminders import list_pending
            return list_pending()
        elif name == "open_application":
            from tools.computer import try_control
            r = try_control(f"打开{args.get('app_name', '')}")
            if r and r.startswith("__LEARN__"):
                app_name = r.replace("__LEARN__", "")
                s = state.get_state()
                s.learning_state.update({
                    "active": True, "stage": "waiting_name",
                    "app_name": app_name, "candidate_path": None
                })
                print(f"[学习模式] 未找到 {app_name}，进入学习流程")
                return (f"系统提示：没找到 {app_name} 这个软件的快捷方式。"
                        f"请温柔地告诉用户：'我在快捷方式里没找到它呢。"
                        f"你可以告诉我它的名字，或者把它的快捷方式放到 data\\my_apps\\ 目录里哦。'")
            return r if r else f"没找到 {args.get('app_name')}。"
        elif name == "close_application":
            from tools.computer import find_running_processes
            app_name = args.get("app_name", "").strip()
            if not app_name:
                return "CLOSE_EMPTY|"
            procs = find_running_processes(app_name)
            if not procs:
                return f"CLOSE_EMPTY|{app_name}"
            s = state.get_state()
            s.learning_state.update({
                "active": True,
                "stage": "waiting_close_confirm",
                "close_target": app_name,
                "close_pids": [p[0] for p in procs],
                "close_names": [p[1] for p in procs],
            })
            proc_list = ";".join([f"{p[0]},{p[1]}" for p in procs])
            print(f"[关闭确认] 找到 {len(procs)} 个进程：{proc_list}")
            return f"CLOSE_FOUND|{app_name}|{len(procs)}|{proc_list}"
        elif name == "open_url":
            from tools.computer import open_url
            return open_url(args.get("url", ""))
        elif name == "look_at_camera":
            from brain.vision import capture_camera, vision_analyze
            b64 = capture_camera()
            return vision_analyze("看看用户", b64) if b64 else "摄像头打不开。"
        elif name == "look_at_screen":
            from brain.vision import capture_screen, vision_analyze
            b64 = capture_screen()
            return vision_analyze("看看屏幕", b64) if b64 else "截图失败。"
        elif name == "web_search":
            from tools.search import web_search
            q = args.get("query", "").strip()
            if not q:
                return "没指定要搜索的内容。"
            return web_search(q) or "没搜到结果。"
        elif name == "get_weather":
            from tools.search import get_weather
            city = (args.get("city") or "杭州").strip()
            print(f"[天气] 城市={city}")
            return get_weather(city) or f"没查到 {city} 的天气。"
        elif name == "set_volume":
            from tools.system_control import (set_volume_level, set_mute, adjust_volume)
            action = args.get("action", "set")
            if action == "up":
                new = adjust_volume(+10)
                return f"音量调到 {new}% 了。" if new is not None else "调音量失败了。"
            elif action == "down":
                new = adjust_volume(-10)
                return f"音量调到 {new}% 了。" if new is not None else "调音量失败了。"
            elif action == "mute":
                return "已经静音了。" if set_mute(True) else "静音失败。"
            elif action == "unmute":
                return "取消静音了。" if set_mute(False) else "取消静音失败。"
            elif action == "set":
                v = args.get("value", 50)
                return f"音量设到 {v}% 了。" if set_volume_level(v) else "设置音量失败。"
            return "不认识的音量操作。"
        elif name == "get_system_info":
            from tools.system_control import get_system_info
            return get_system_info()
        elif name == "take_note":
            from tools.notes import add as note_add
            return note_add(args.get("content", ""))
        elif name == "list_notes":
            from tools.notes import list_recent
            return list_recent()
        elif name == "delete_last_note":
            from tools.notes import delete_last
            return delete_last()
        elif name == "write_diary":
            from tools.diary import generate_summary
            return generate_summary(client, provider) if client and provider else "无法生成日记。"
        elif name == "query_diary":
            from tools.diary import query_past
            return query_past(args.get("query", ""))
        elif name == "read_file":
            import os as _os
            from tools.files import find_file, read_content
            fp = find_file(args.get("filename", ""))
            if not fp:
                return f"找不到文件 {args.get('filename')}。"
            return f"【{_os.path.basename(fp)}】\n{read_content(fp)}"
        elif name == "regenerate_diary":
            from tools.diary import regenerate_today
            return regenerate_today(client, provider) if client and provider else "无法重写日记。"
        elif name == "set_diary":
            from tools.diary import set_diary_content
            return set_diary_content(args.get("content", ""), args.get("date"))
        elif name == "delete_diary":
            from tools.diary import delete_diary as dd
            return dd(args.get("date"))
        elif name == "list_diaries":
            from tools.diary import list_diary_dates
            return list_diary_dates()
        return f"未知工具: {name}"
    except Exception as e:
        return f"工具执行出错: {e}"


def format_tool_result(tool_name, result):
    result = result.strip()
    if any(k in result for k in ["失败", "打不开", "出错", "找不到", "没找到",
                                   "无法解析", "没搜到", "没有日记", "没有写日记"]):
        return result
    if tool_name in ("mc_say", "mc_follow", "mc_come", "mc_stop", "mc_status",
                     "mc_look", "mc_mine", "mc_attack", "mc_collect", "mc_eat",
                     "mc_drop", "mc_inventory"):
        return result
    if tool_name == "get_current_time":
        return result
    elif tool_name == "calculate_date":
        return result
    elif tool_name == "calculate":
        return result
    elif tool_name == "set_reminder":
        r = result
        if r.startswith("好的，"): r = r[3:]
        elif r.startswith("好的,"): r = r[3:]
        return f"好的，{r}"
    elif tool_name == "list_reminders":
        return result
    elif tool_name == "open_application":
        return result
    elif tool_name == "close_application":
        if result.startswith("CLOSE_FOUND|"):
            parts = result.split("|", 3)
            app = parts[1]
            count = parts[2]
            pairs = parts[3].split(";") if len(parts) > 3 else []
            names = []
            for p in pairs:
                if "," in p:
                    names.append(p.split(",", 1)[1])
            name_str = "、".join(names) if names else f"{count} 个进程"
            return f"找到 {count} 个正在运行的 {app}：{name_str}。要关闭它们吗？"
        elif result.startswith("CLOSE_EMPTY|"):
            app = result.split("|", 1)[1]
            if app:
                return f"没找到正在运行的 {app}。"
            return "没指定要关闭什么。"
        return result
    elif tool_name == "open_url":
        return result
    elif tool_name == "look_at_camera":
        return f"我看到{result}"
    elif tool_name == "look_at_screen":
        return f"屏幕上{result}"
    elif tool_name == "web_search":
        return result
    elif tool_name == "get_weather":
        return result
    elif tool_name == "set_volume":
        return result
    elif tool_name == "get_system_info":
        return result
    elif tool_name == "take_note":
        return result
    elif tool_name == "list_notes":
        return result
    elif tool_name == "delete_last_note":
        return result
    elif tool_name == "write_diary":
        return f"今天的日记记好啦：{result}"
    elif tool_name == "query_diary":
        result = re.sub(r'^(今天与用户的对话中|今天的对话中|在我们?之前的对话中|在这两天的对话中)[，,]\s*', '', result)
        result = result.replace("用户", "你")
        return f"我想想哦……{result}"
    elif tool_name == "read_file":
        return f"文件内容是这样的：{result}"
    elif tool_name == "regenerate_diary":
        return f"我重写好啦：{result}"
    elif tool_name == "set_diary":
        return result
    elif tool_name == "delete_diary":
        return result
    elif tool_name == "list_diaries":
        return result
    return result


# ══════════════════════════════════════════════════════════════
# 主调用逻辑
# ══════════════════════════════════════════════════════════════
def _ask_ai_inner(client, provider, history, ui, session_start, use_tools=True,
                  speaker="主人", relation="主人", from_mc=False, source="owner"):
    s = state.get_state()
    if s.last_interrupted:
        ui = f"[用户刚才打断了你的话] {ui}"
        s.last_interrupted = False
        print(f"[打断理解] 已注入标记")

    um = {"role": "user", "content": ui}
    rh = history[-8:]

    # 防复读
    _ui_key = (ui or "").strip()[:10]
    _recent_users = [m.get("content", "") for m in history[-8:]
                     if m.get("role") == "user"]
    _repeat_count = sum(1 for u in _recent_users
                        if u and u.strip()[:10] == _ui_key)
    if _repeat_count >= 2:
        _before = len(rh)
        rh = [m for m in rh if m.get("role") == "user"]
        print(f"[防复读] 检测到重复提问（{_repeat_count}次），"
              f"已过滤 {_before - len(rh)} 条 assistant 回复")

    now = time.localtime()
    wd = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.tm_wday]
    tctx = f"\n\n【当前时间】\n{time.strftime('%Y年%m月%d日 %H:%M:%S', now)}（{wd}）。本次对话 {int((time.time()-session_start)/60)} 分钟。"
    mctx = s.mood_mgr.get_context() if s.mood_mgr else ""

    relation_ctx = get_relation_prompt(speaker, relation)
    profile_ctx = get_profile_context(speaker)
    transition_ctx = get_transition_prompt(speaker, s.last_speaker)
    s.last_speaker = speaker
    s.current_speaker = speaker

    refusal_ctx = None
    try:
        refusal_ctx = evaluate_request(ui)
    except Exception as e:
        print(f"[拒绝评估] 异常: {e}")

    persona_ctx = build_persona_prompt(refusal_ctx=refusal_ctx, source=source)

    bmax = provider.get("max_tokens", 600)
    dmax = max(bmax, 1500) if should_expand(ui) else bmax
    supports_tools = provider.get("supports_tools", True)

    forced_tool = detect_forced_tool(ui) if use_tools and supports_tools else None
    if forced_tool:
        print(f"[强制工具] 检测到意图: {forced_tool}")

    chitchat = is_chitchat(ui, forced_tool)

    if _is_emotion_event(ui):
        chitchat = True
        forced_tool = None
        print(f"[情绪优先] 检测到情绪事件，强制走纯聊天路径")

    if chitchat:
        print(f"[闲聊/元问题] 走纯聊天路径 + RAG")

    # ══════════════════════════════════════════════════════
    # 纯聊天路径
    # ══════════════════════════════════════════════════════
    if chitchat:
        if source in ("friend", "audience"):
            mem = []
            print(f"[RAG] {source} 消息，已跳过检索")
        else:
            mem = s.rag.search(ui, top_k=5) if s.rag else []
        sysc = BASE_SYSTEM_PROMPT + tctx + mctx
        sysc += relation_ctx
        sysc += persona_ctx
        if profile_ctx:
            sysc += profile_ctx
        if transition_ctx:
            sysc += transition_ctx
        if mem:
            sysc += "\n\n【参考资料】\n" + "\n".join([f"· {m}" for m in mem])
            print(f"[RAG] 检索到 {len(mem)} 条")

        if source in ("friend", "audience"):
            sysc += (
                "\n\n⚠️ 现在是在和朋友/观众对话，务必遵守："
                "1. 回复简短（1~2 句）。"
                "2. 禁止提及哥哥的隐私、项目、技术。"
                "3. 禁止用'哥哥''小家伙''主人'称呼对方。"
                "4. 禁止用'您'。"
                "5. 禁止说'我是AI''我是程序'。"
            )
        elif source == "mc":
            sysc += (
                "\n\n⚠️ 现在是在 Minecraft 游戏里，哥哥通过游戏聊天栏跟你说话。"
                "用轻松、简短（1~2 句）的语气回应。"
                "不要提血量/饱食度/坐标，除非他问。"
                "禁止用'您'，永远用'你'。"
                "禁止说'我是AI''我是程序'。"
            )
        else:
            sysc += (
                "\n\n⚠️【最高优先级 - 当前情绪决定一切】"
                "\n你当前情绪见上方'当前情绪'字段。"
                "\n情绪是生气/难过/失落 → 必须冷淡、短句、可以不理人。"
                "\n禁止说'谢谢夸奖''为你服务''有什么可以帮你'。"
                "\n禁止假装没事、禁止讨好。直接表达情绪。"
            )
            sysc += (
                "\n\n⚠️ 其他规则："
                "1. 回复 2~3 句话以内。"
                "2. 用'你'称呼（禁止用'您'）。"
                "3. 禁止 markdown、列表、编号、加粗。"
                "4. 不编造。"
                "5. 可以带'嘛''啦''诶''欸'。"
                "6. 禁止'我是AI'。"
                "7. 不调工具。"
                "8. 你没有眼睛和耳朵。永远不说'我看到''我听到'。"
            )
        msgs = [{"role": "system", "content": sysc}] + rh + [um]
        try:
            r = client.chat.completions.create(
                model=provider["model"], messages=msgs,
                timeout=90, temperature=0.7, max_tokens=400,
                extra_body={"keep_alive": "30m", "think": False},
            )
            ac = r.choices[0].message.content.strip()
            if _is_ai_disclosure(ac):
                print(f"[AI拦截-闲聊] 检测到自我暴露，用兜底句替换")
                ac = "……"
            ac = _clean_markdown(ac)
            ac = _strip_actions(ac)
            ac = _filter_customer_service(ac)
            if not ac or len(ac) < 2:
                ac = "……"
            ac = _truncate_reply(ac, max_sentences=3)
            history.append({"role": "user", "content": ui})
            history.append({"role": "assistant", "content": ac})
            save_history(history)
            if s.rag and source not in ("friend", "audience"):
                s.rag.add(ui, ac)
            elif source in ("friend", "audience"):
                print(f"[RAG] {source} 消息，跳过存储")
            if refusal_ctx:
                print(f"[拒绝指令] 已注入 level={refusal_ctx['level']} style={refusal_ctx['style']}")
            return ac
        except Exception as e:
            return f"[出错] 无法连接大脑：{e}"

    # ══════════════════════════════════════════════════════
    # 工具路径
    # ══════════════════════════════════════════════════════
    if use_tools and supports_tools:
        hard_prompt = BASE_SYSTEM_PROMPT
        hard_prompt += f"\n\n【当前时间】\n{time.strftime('%Y年%m月%d日 %H:%M', now)}\n"
        hard_prompt += persona_ctx
        hard_prompt += relation_ctx
        if profile_ctx:
            hard_prompt += profile_ctx
        if transition_ctx:
            hard_prompt += transition_ctx
        hard_prompt += (
            "\n\n⚠️【最高优先级】\n"
            "你只需要处理用户**最新的一句话**。\n"
            "历史对话里出现过的请求**已经处理完了**，不要再重复执行。\n"
            "一次只调用**最多一个**最相关的工具，禁止并行调用多个工具。\n"
            "\n⚠️【禁止滥用工具】\n"
            "如果用户只是在聊天、表达情绪、骂你、撒娇、吐槽——**不要调用任何工具**。\n"
            "工具只在用户**明确请求某个功能**时调用（比如'挖矿'、'查天气'）。\n"
            "用户说'我讨厌你'、'你好烦'、'我难过' → **绝对不调工具**，只是回应情绪。\n"
            "\n\n【MC 玩家信息】\n"
            "哥哥在 Minecraft 里的玩家名是：EZFM233\n"
            "（注意：是 E-Z-F-M-2-3-3，全大写）\n"
            "禁止用 '@p'、'@a' 这类命令选择符，必须写 'EZFM233'。\n"
            "\n\n【禁止用'您'】\n"
            "永远用'你'称呼对方，不管什么场景。\n"
            "\n\n【禁止列建议清单】\n"
            "不要输出'建议您 1. 2. 3.'这种列表，直接说重点。\n"
        )
        hard_prompt += (
            "\n\n【工具调用规则】\n"
            "你是绯木，你有工具可以调用。\n"
            "⚠️ 当用户意图匹配下面任一场景时，必须真的调用工具：\n"
            "· 问时间/日期 → get_current_time\n"
            "· 日期计算 → calculate_date\n"
            "· 数学计算 → calculate\n"
            "· 设置提醒 → set_reminder\n"
            "· 查看提醒 → list_reminders\n"
            "· 打开软件 → open_application\n"
            "· 关闭软件 → close_application\n"
            "· 打开网页 → open_url\n"
            "· 调音量 → set_volume\n"
            "· 电脑状态 → get_system_info\n"
            "· 记笔记 → take_note\n"
            "· 看笔记 → list_notes\n"
            "· 删笔记 → delete_last_note\n"
            "· 看用户本人 → look_at_camera\n"
            "· 看电脑屏幕 → look_at_screen\n"
            "· 查天气 → get_weather\n"
            "· 查新闻/搜索 → web_search\n"
            "· 总结今天 → write_diary\n"
            "· 查历史日记 → query_diary\n"
            "· 读文件 → read_file\n"
            "· 重写今天的日记 → regenerate_diary\n"
            "· 删除日记 → delete_diary\n"
            "· 列出日记日期 → list_diaries\n"
            "· MC 跟随我 → mc_follow(target='EZFM233')\n"
            "· MC 过来 → mc_come(target='EZFM233')\n"
            "· MC 停止 → mc_stop\n"
            "· MC 状态 → mc_status\n"
            "· MC 周围 → mc_look\n"
            "· MC 说话 → mc_say\n"
            "· MC 挖矿 → mc_mine\n"
            "· MC 打怪 → mc_attack\n"
            "· MC 捡东西 → mc_collect\n"
            "· MC 吃东西 → mc_eat\n"
            "· MC 丢东西 → mc_drop\n"
            "· MC 背包 → mc_inventory\n"
            "⚠️ 绝对禁止："
            "1. 不许说'我将为你搜索'、'我将为你打开'、'已经帮你关闭了'这类话。"
            "2. 不许传工具不接受的参数。"
            "3. 不许说自己是AI/程序/模型/助手。\n\n"
            + FEW_SHOT_EXAMPLES
        )
        msgs = [{"role": "system", "content": hard_prompt}] + rh + [um]
    else:
        sysc = BASE_SYSTEM_PROMPT + tctx + mctx
        sysc += relation_ctx
        sysc += persona_ctx
        if profile_ctx:
            sysc += profile_ctx
        if transition_ctx:
            sysc += transition_ctx
        msgs = [{"role": "system", "content": sysc}] + rh + [um]

    t0 = time.time()
    max_rounds = 3
    tool_results_log = []
    try:
        for round_i in range(max_rounds):
            kwargs = {
                "model": provider["model"],
                "messages": msgs,
                "timeout": 90,
                "temperature": 0.1,
                "max_tokens": dmax,
                "extra_body": {"keep_alive": "30m", "think": False},
            }
            if use_tools and supports_tools:
                kwargs["tools"] = get_tools_schema()
                kwargs["tool_choice"] = "auto"

            r = client.chat.completions.create(**kwargs)
            msg = r.choices[0].message

            print(f"\n[DEBUG] 第 {round_i+1} 轮返回：")
            print(f"  content: {repr(msg.content)[:200]}")
            print(f"  tool_calls: {getattr(msg, 'tool_calls', None)}")
            print()

            if use_tools and hasattr(msg, "tool_calls") and msg.tool_calls:
                print(f"[工具调用] 第 {round_i+1} 轮，触发 {len(msg.tool_calls)} 个工具")
                msgs.append(msg)
                called_tools = [t["tool"] for t in tool_results_log]
                tc = msg.tool_calls[0]
                if len(msg.tool_calls) > 1:
                    print(f"   [限制] 模型返回 {len(msg.tool_calls)} 个工具，只执行第一个")
                    for extra_tc in msg.tool_calls[1:]:
                        msgs.append({
                            "role": "tool",
                            "tool_call_id": extra_tc.id,
                            "content": "（系统已跳过，一次只执行一个工具）"
                        })
                fn_name = tc.function.name
                if fn_name in called_tools:
                    print(f"[防重复] {fn_name} 已调用过，跳过")
                    msgs.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": "（已执行过此工具，无需重复）"
                    })
                    continue
                try:
                    fn_args = json.loads(tc.function.arguments)
                except:
                    fn_args = {}
                print(f"   → 执行 {fn_name}({fn_args})")
                result = execute_tool(fn_name, fn_args, client, provider)
                print(f"   ← 返回: {result[:80]}")
                tool_results_log.append({"tool": fn_name, "result": result})
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                continue

            if forced_tool and round_i == 0:
                print(f"[撒谎拦截] 模型没调用 {forced_tool}，强制重试...")
                msgs.append({
                    "role": "user",
                    "content": f"⚠️ 系统提示：你刚才没有真正调用 {forced_tool}。请立刻调用它。"
                })
                continue

            if tool_results_log:
                _last_result = tool_results_log[-1].get("result", "")
                _tech_fail = any(k in _last_result for k in [
                    "WebSocket", "未连接", "bot.js", "端口", "netstat",
                    "ps aux", "grep", "进程",
                ])
                if _tech_fail:
                    print(f"[工具失败] 检测到技术性错误，用自然回应")
                    _last_tool = tool_results_log[-1].get("tool", "")
                    if _last_tool.startswith("mc_"):
                        ac = "哥哥，我的世界好像没开诶，你要开一下吗？"
                    else:
                        ac = "唔……好像出了点小问题，哥哥再试试？"
                    history.append({"role": "user", "content": ui})
                    history.append({"role": "assistant", "content": ac})
                    save_history(history)
                    return ac

                llm_reply = (msg.content or "").strip()
                if len(llm_reply) >= 5 and not _is_ai_disclosure(llm_reply):
                    if len(llm_reply) > 100:
                        head = llm_reply[:120]
                        m = None
                        for _m in re.finditer(r'[。！？!?]', head):
                            m = _m
                        if m and m.end() > 50:
                            llm_reply = head[:m.end()]
                        else:
                            llm_reply = head[:80].rstrip("，,。.！!、 ") + "。"
                    print(f"[工具后回复] 用 LLM 自然语言（{len(llm_reply)}字）")
                    ac = _clean_markdown(llm_reply)
                    ac = _strip_actions(ac)
                    ac = _filter_customer_service(ac)
                    if not ac or len(ac) < 2:
                        ac = "……"
                    ac = _truncate_reply(ac, max_sentences=2)
                    print(f"   [计时] LLM {time.time()-t0:.2f}s (round {round_i+1})")
                    history.append({"role": "user", "content": ui})
                    history.append({"role": "assistant", "content": ac})
                    save_history(history)
                    _skip_rag = any(t["tool"].startswith("mc_") for t in tool_results_log)
                    if s.rag and not _skip_rag:
                        s.rag.add(ui, ac)
                    elif _skip_rag:
                        print(f"[RAG] MC 工具结果，跳过存储")
                    return ac

                print(f"[模板拼接] LLM 无自然回复，用模板兜底...")
                if len(tool_results_log) == 1:
                    final = format_tool_result(
                        tool_results_log[0]["tool"],
                        tool_results_log[0]["result"]
                    )
                else:
                    parts = [format_tool_result(t["tool"], t["result"])
                             for t in tool_results_log]
                    final = " ".join(parts)
                final = _strip_actions(final)
                final = _filter_customer_service(final)
                if not final or len(final) < 2:
                    final = "……"
                print(f"   最终: {final[:100]}")
                history.append({"role": "user", "content": ui})
                history.append({"role": "assistant", "content": final})
                save_history(history)
                _skip_rag = any(t["tool"].startswith("mc_") for t in tool_results_log)
                if s.rag and not _skip_rag:
                    s.rag.add(ui, final)
                print(f"   [计时] 总耗时 {time.time()-t0:.2f}s")
                return final

            if _is_ai_disclosure(msg.content or ""):
                print(f"[AI拦截] 检测到自我暴露，重试...")
                msgs.append({
                    "role": "user",
                    "content": (
                        "⚠️ 系统提示：禁止说自己是AI/程序/模型/助手。"
                        "你是绯木，一个真实的、有情绪的存在。"
                        "刚才那句话你不需要回应，请直接**按你当前的情绪**回应对方。"
                        "如果对方在骂你，你可以生气、可以难过、可以沉默、可以说'我不想理你'。"
                        "但**不要调工具**，不要给建议清单，不要说教。"
                        "用 1~2 句话，像人一样反应。"
                    )
                })
                continue

            if refusal_ctx:
                print(f"[拒绝指令] 已注入 level={refusal_ctx['level']} style={refusal_ctx['style']}")

            ac = msg.content
            ac = _clean_markdown(ac)
            ac = _strip_actions(ac)
            ac = _filter_customer_service(ac)
            if not ac or len(ac) < 2:
                ac = "……"
            ac = _truncate_reply(ac, max_sentences=3)
            print(f"   [计时] LLM {time.time()-t0:.2f}s (round {round_i+1})")
            history.append({"role": "user", "content": ui})
            history.append({"role": "assistant", "content": ac})
            save_history(history)
            if s.rag and source not in ("friend", "audience"):
                s.rag.add(ui, ac)
            return ac

        return "[提示] 工具调用轮数过多，请重新描述需求。"
    except Exception as e:
        return f"[出错] 无法连接大脑：{e}"


def ask_ai(client, provider, history, ui, session_start, use_tools=True,
           speaker="主人", relation="主人", from_mc=False, source="owner"):
    s = state.get_state()

    try:
        detect_event_from_text(ui)
        bump_turn()
    except Exception as e:
        print(f"[人格] 情绪检测异常: {e}")

    if s.text_mode:
        result = _ask_ai_inner(client, provider, history, ui, session_start, use_tools,
                               speaker, relation, from_mc=from_mc, source=source)
        if result and from_mc:
            result = _shorten_for_mc(result)
        if result:
            extract_async(client, provider, ui, result, speaker)
        return result

    stop_listen = threading.Event()
    interrupt_hit = [False]

    def listen_for_interrupt():
        try:
            counter = [0]
            def cb(indata, frames, ti, status):
                v = float(np.mean(np.abs(indata)))
                if v > 0.10:
                    counter[0] += 1
                    if counter[0] >= 3:
                        interrupt_hit[0] = True
                        stop_listen.set()
                else:
                    counter[0] = 0
            with sd.InputStream(samplerate=16000, channels=1, callback=cb,
                                blocksize=800, device=s.audio_device):
                while not stop_listen.is_set():
                    time.sleep(0.05)
        except Exception:
            pass

    threading.Thread(target=listen_for_interrupt, daemon=True).start()

    try:
        result = _ask_ai_inner(client, provider, history, ui, session_start, use_tools,
                               speaker, relation, from_mc=from_mc, source=source)
    finally:
        stop_listen.set()

    if interrupt_hit[0]:
        print("[思考期打断] 用户插话，放弃本次回复")
        s.last_interrupted = True
        return ""

    if result and from_mc:
        result = _shorten_for_mc(result)

    if result:
        extract_async(client, provider, ui, result, speaker)

    return result