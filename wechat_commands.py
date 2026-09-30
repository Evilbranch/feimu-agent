"""微信端命令 - 识别 /status /recent /diary，请求电脑端接口并格式化

设计原则：
- 微信端不做 AI 处理，只做命令识别 + HTTP 请求 + 本地格式化
- 所有数据来自电脑端 /self/* 接口
- 识别不到命令就返回 None，交回给主流程转发给大脑
- 命令识别用精确匹配，避免误伤正常对话
"""
import json
import time
import urllib.request
import urllib.error

API_BASE = "http://127.0.0.1:8765"
TIMEOUT = 5


# ══════════════════════════════════════════════════════
# 命令识别
# ══════════════════════════════════════════════════════
_STATUS_KEYWORDS = [
    "你在干嘛", "你在做什么", "你在干什么",
    "你在干嘛呢", "你现在在干嘛", "你的状态",
    "你在哪", "你在哪里", "状态", "在干嘛",
]
_RECENT_KEYWORDS = [
    "最近在想什么", "你在想什么", "你在想啥",
    "最近做什么", "最近做了什么", "最近的活动",
    "你最近在干嘛", "你最近在做什么",
]
_DIARY_KEYWORDS = [
    "今天的日记", "今天的日记呢", "日记",
    "看看日记", "给我看日记",
]

HELP_TEXT = """我能查这些东西：
· /status 或 "你在干嘛" —— 我现在的状态
· /recent 或 "最近在想什么" —— 我最近在想什么
· /diary 或 "今天的日记" —— 我今天的日记
其他话直接说就行~"""


def try_handle(text):
    """如果是命令，返回 (reply_text, handled)；否则 (None, False)"""
    if not text:
        return None, False

    raw = text.strip()

    # ── 1. 严格 /xxx 命令 ──
    low = raw.lower()
    if low == "/status":
        return handle_status(), True
    if low == "/recent":
        return handle_recent(limit=5), True
    if low == "/diary":
        return handle_diary(), True
    if low in ("/help", "/帮助"):
        return HELP_TEXT, True

    # ── 2. 中文宽松匹配（去掉尾部标点后精确匹配）──
    stripped = raw.rstrip("。！!，,？? ")
    if stripped in _STATUS_KEYWORDS:
        return handle_status(), True
    if stripped in _RECENT_KEYWORDS:
        return handle_recent(limit=5), True
    if stripped in _DIARY_KEYWORDS:
        return handle_diary(), True

    return None, False


# ══════════════════════════════════════════════════════
# HTTP 请求
# ══════════════════════════════════════════════════════
def _get(path):
    """GET 请求电脑端，返回 dict 或 None"""
    url = f"{API_BASE}{path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.URLError as e:
        print(f"[命令] 请求失败 {path}: {e}")
        return None
    except Exception as e:
        print(f"[命令] 异常 {path}: {e}")
        return None


# ══════════════════════════════════════════════════════
# 命令处理
# ══════════════════════════════════════════════════════
def handle_status():
    data = _get("/self/status")
    if not data or not data.get("ok"):
        return "唔……我现在连不上大脑，等会儿再问我好不好？"

    intent = data.get("intent", "idle")
    reason = data.get("reason", "")
    emotion = data.get("emotion", {})
    label = emotion.get("label", "平静")
    drives = data.get("drives", {})
    conn = drives.get("connection", 0)
    next_wake_in = data.get("next_wake_in")
    last_min = data.get("last_interaction_min", 0)

    intent_cn = {
        "idle": "在发呆",
        "speak": "想说话",
        "explore": "在学习",
    }.get(intent, intent)

    lines = [f"我现在{intent_cn}，心情{label}"]

    if conn > 0.8:
        lines.append("（有点想你）")
    elif conn > 0.6:
        lines.append("（有点想聊天）")

    if reason:
        lines.append(f"理由：{reason}")

    if last_min < 1:
        lines.append("我们刚刚还在聊")
    elif last_min < 60:
        lines.append(f"距上次聊天：{last_min} 分钟前")
    else:
        lines.append(f"距上次聊天：{last_min // 60} 小时前")

    if next_wake_in and next_wake_in > 0:
        m = next_wake_in // 60
        if m < 1:
            lines.append(f"下次醒来：{next_wake_in} 秒后")
        elif m < 60:
            lines.append(f"下次醒来：{m} 分钟后")
        else:
            lines.append(f"下次醒来：{m // 60} 小时 {m % 60} 分钟后")

    return "\n".join(lines)


def handle_recent(limit=5):
    data = _get(f"/self/recent?limit={limit}")
    if not data or not data.get("ok"):
        return "唔……我现在连不上大脑，等会儿再问我好不好？"

    items = data.get("items", [])
    if not items:
        return "唔……我最近还什么都没想过呢。"

    lines = [f"最近 {len(items)} 条内在活动："]
    for e in items:
        ts = e.get("ts", 0)
        t = time.strftime("%H:%M", time.localtime(ts)) if ts else "?"
        intent = e.get("intent", "")
        content = (e.get("content") or "").strip()
        reason = (e.get("reason") or "").strip()

        intent_cn = {
            "idle": "发呆",
            "speak": "想说话",
            "explore": "学习",
        }.get(intent, intent)

        line = f"· [{t}] {intent_cn}"
        if content:
            line += f"：{content[:30]}"
        elif reason:
            line += f"：{reason[:30]}"
        lines.append(line)

    return "\n".join(lines)


def handle_diary():
    data = _get("/self/diary")
    if not data or not data.get("ok"):
        return "唔……我现在连不上大脑，等会儿再问我好不好？"

    date = data.get("date", "")
    exists = data.get("exists", False)
    content = data.get("content", "")

    if not exists:
        return f"{date} 的日记还没写呢。"

    if isinstance(content, (dict, list)):
        content = json.dumps(content, ensure_ascii=False, indent=2)

    content_str = str(content)
    if len(content_str) > 500:
        content_str = content_str[:500] + "..."

    return f"{date} 的日记：\n\n{content_str}"