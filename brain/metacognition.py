"""元认知观测器 - 她观察自己最近的模式，输出调整建议"""
import os
import json
import time
import re
import threading

from core.constants import DATA_DIR
from core import state
from brain import persona as P
from brain import inner_life as IL

META_LOG_FILE = os.path.join(DATA_DIR, "meta_observations.json")
META_HINT_FILE = os.path.join(DATA_DIR, "meta_hint.json")
META_LAST_ADJ_FILE = os.path.join(DATA_DIR, "meta_last_adjustment.json")
_lock = threading.Lock()

META_INTERVAL = 1800        # 每 30 分钟观测一次
MIN_LOG_COUNT = 5           # 至少 5 条日志才分析
REVERSE_COOLDOWN = 3600     # 1 小时内不允许反转方向

# 相反方向映射（用于防横跳）
_OPPOSITES = {
    "speak_more": "speak_less",
    "speak_less": "speak_more",
    "slow_down": "speed_up",
    "speed_up": "slow_down",
}


def _load_meta_log():
    if not os.path.exists(META_LOG_FILE):
        return []
    try:
        with open(META_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def _save_meta_log(log):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(META_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except:
        pass


def get_meta_log(limit=10):
    return _load_meta_log()[-limit:]


def _save_hint(hint):
    """把调整建议写入文件，供 inner_life 读取"""
    try:
        with open(META_HINT_FILE, "w", encoding="utf-8") as f:
            json.dump(hint, f, ensure_ascii=False, indent=2)
    except:
        pass


def load_hint():
    """inner_life 每次醒来时读这个"""
    if not os.path.exists(META_HINT_FILE):
        return None
    try:
        with open(META_HINT_FILE, "r", encoding="utf-8") as f:
            hint = json.load(f)
        # 超过 1 小时的 hint 失效
        if time.time() - hint.get("ts", 0) > 3600:
            return None
        return hint
    except:
        return None


# ══════════════════════════════════════════════════════════
# 防横跳
# ══════════════════════════════════════════════════════════
def _load_last_adj():
    if not os.path.exists(META_LAST_ADJ_FILE):
        return {}
    try:
        with open(META_LAST_ADJ_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def _save_last_adj(adj, ts):
    try:
        with open(META_LAST_ADJ_FILE, "w", encoding="utf-8") as f:
            json.dump({"adjustment": adj, "ts": ts}, f, ensure_ascii=False, indent=2)
    except:
        pass


def _resolve_adjustment(new_adj):
    """防横跳：
    - 同向 → 保持，刷新时间戳
    - 反向且 1 小时内 → 拦成 none
    - 反向且超过 1 小时 → 允许切换
    """
    if new_adj == "none":
        return "none"

    last = _load_last_adj()
    last_adj = last.get("adjustment", "none")
    last_ts = last.get("ts", 0)
    now = time.time()

    # 同向：保持
    if new_adj == last_adj:
        _save_last_adj(new_adj, now)
        return new_adj

    # 反向：检查冷却
    is_reverse = _OPPOSITES.get(new_adj) == last_adj
    if is_reverse and (now - last_ts < REVERSE_COOLDOWN):
        remain = int((REVERSE_COOLDOWN - (now - last_ts)) / 60)
        print(f"[元认知-防横跳] {last_adj} → {new_adj} 被拦截（{remain} 分钟后才允许反转）")
        return "none"

    # 允许切换（反向但超时，或无关方向）
    _save_last_adj(new_adj, now)
    return new_adj


# ══════════════════════════════════════════════════════════
# 特征提取
# ══════════════════════════════════════════════════════════
def _extract_features():
    log = IL._load_log()
    if len(log) < MIN_LOG_COUNT:
        return None

    recent = log[-30:]
    now = time.time()

    idle_count = sum(1 for e in recent if e.get("intent") == "idle")
    speak_count = sum(1 for e in recent if e.get("intent") == "speak")
    explore_count = sum(1 for e in recent if e.get("intent") == "explore")

    speak_times = [e["ts"] for e in recent if e.get("intent") == "speak"]
    intervals = []
    for i in range(1, len(speak_times)):
        intervals.append(speak_times[i] - speak_times[i-1])

    avg_interval = sum(intervals) / len(intervals) if intervals else 0

    speak_contents = [e.get("content", "") for e in recent
                      if e.get("intent") == "speak" and e.get("content")]

    last_speak_min = None
    for e in reversed(log):
        if e.get("intent") == "speak":
            last_speak_min = int((now - e["ts"]) / 60)
            break

    s = state.get_state()
    idle_min = int((now - getattr(s, "last_interaction_time", 0)) / 60)

    return {
        "total": len(recent),
        "idle_count": idle_count,
        "speak_count": speak_count,
        "explore_count": explore_count,
        "avg_speak_interval_min": round(avg_interval / 60, 1) if avg_interval else None,
        "last_speak_min": last_speak_min,
        "idle_min": idle_min,
        "recent_speaks": speak_contents[-5:],
    }


# ══════════════════════════════════════════════════════════
# LLM 分析
# ══════════════════════════════════════════════════════════
META_SYSTEM = """你是绯木。现在你要**回顾自己最近的状态**，看看有没有需要调整的地方。

⚠️ 直接输出 JSON，不要思考、不要解释、不要前后缀。必须从 { 开始，到 } 结束。

{
  "assessment": "一句话评估我最近的状态",
  "hint": "给我自己下一次决策的提示（1句话，影响我下次醒来时的判断）",
  "force_next_speak": false,
  "adjustment": "none | speak_more | speak_less | slow_down | speed_up"
}

【分析要点】
- 如果最近 speak 太少（间隔 > 3 小时或完全没有）→ 建议主动一点
- 如果最近 speak 太多（间隔 < 20 分钟）→ 建议慢下来
- 如果最近内容重复 → 提示换话题
- 如果一切正常 → adjustment 填 none

【hint 的作用】
你写的 hint 会被注入到你下一次醒来时的上下文里，作为"自我提醒"。

⚠️ **hint 绝对不能引导你"分享经历"**——因为你没有真实的视觉、听觉、经历。
如果你提示自己去"分享今天看到的事"，你会编造不存在的事，这是错的。

✅ 可以引导的方向：
- "最近我太沉默了，看到哥哥可以主动打个招呼"
- "我在重复说自己想他，试试换种表达方式"
- "保持这个节奏就很好"

🚫 禁止引导的方向：
- "分享今天看到的事物" ← 你会编造
- "聊聊今天的经历" ← 你会编造
- "说说遇到的人" ← 你会编造

【adjustment 含义】
- none：不改
- speak_more：下次更容易主动说话
- speak_less：下次更倾向 idle
- slow_down：下次 next_wake 拉长
- speed_up：下次 next_wake 缩短

只输出 JSON。"""


def _ask_meta(client, provider, features):
    s = state.get_state()
    st = P.get_current_state()
    d = st["drives"]
    e = st["emotion"]

    recent_str = "\n".join([f"  · {c[:50]}" for c in features["recent_speaks"]]) or "  （无）"

    ctx = f"""【我现在的时间】{time.strftime('%Y年%m月%d日 %H:%M', time.localtime())}

【最近 30 条内在日志统计】
- 总计：{features['total']} 条
- idle：{features['idle_count']} 次
- speak：{features['speak_count']} 次
- explore：{features['explore_count']} 次
- 主动说话平均间隔：{features['avg_speak_interval_min']} 分钟
- 距离上次主动：{features['last_speak_min']} 分钟前
- 距离上次和哥哥互动：{features['idle_min']} 分钟前

【我最近的主动内容】
{recent_str}

【我的当前状态】
- 连接欲：{d['connection']:.2f}
- 安全感：{d['security']:.2f}
- 情绪：{e['label']}

请回顾一下，我最近的状态怎么样？"""

    msgs = [
        {"role": "system", "content": META_SYSTEM},
        {"role": "user", "content": ctx},
    ]

    for attempt in range(2):
        try:
            r = client.chat.completions.create(
                model=provider["model"],
                messages=msgs,
                timeout=120,
                temperature=0.5,
                max_tokens=400,
                extra_body={"keep_alive": "30m", "think": False},
            )
            msg = r.choices[0].message
            text = (msg.content or "").strip()
            if not text:
                reasoning = getattr(msg, "reasoning_content", None)
                if reasoning:
                    text = reasoning.strip()
        except Exception as e:
            print(f"[元认知] LLM 调用失败: {e}")
            continue

        if not text:
            continue

        text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            continue

        try:
            return json.loads(text[start:end + 1])
        except:
            continue

    return None


# ══════════════════════════════════════════════════════════
# 主循环
# ══════════════════════════════════════════════════════════
def _do_observation(client, provider):
    features = _extract_features()
    if not features:
        return

    result = _ask_meta(client, provider, features)
    if not result:
        print("[元认知] 分析失败")
        return

    assessment = (result.get("assessment") or "").strip()
    hint = (result.get("hint") or "").strip()
    force_speak = bool(result.get("force_next_speak", False))
    adjustment = (result.get("adjustment") or "none").strip()

    # 防横跳
    adjustment = _resolve_adjustment(adjustment)

    print(f"[元认知] {assessment}")
    if hint:
        print(f"[元认知] 自我提醒：{hint}")
    print(f"[元认知] 调整：{adjustment}")

    # 记录
    with _lock:
        log = _load_meta_log()
        log.append({
            "ts": time.time(),
            "assessment": assessment,
            "hint": hint,
            "force_next_speak": force_speak,
            "adjustment": adjustment,
            "features": features,
        })
        log = log[-100:]
        _save_meta_log(log)

    # 写入 hint（供 inner_life 读取）
    _save_hint({
        "ts": time.time(),
        "hint": hint,
        "force_next_speak": force_speak,
        "adjustment": adjustment,
    })


def metacognition_loop(client, provider):
    """后台线程：每 30 分钟观测一次自己"""
    s = state.get_state()
    print(f"[元认知] 观测器已启动，每 {META_INTERVAL // 60} 分钟运行一次")

    # 首次延迟 10 分钟
    s.shutdown_flag.wait(600)
    if s.shutdown_flag.is_set():
        return

    while not s.shutdown_flag.is_set():
        try:
            if not getattr(s, "inner_life_enabled", True):
                s.shutdown_flag.wait(300)
                continue

            _do_observation(client, provider)
        except Exception as e:
            print(f"[元认知] 异常: {e}")

        s.shutdown_flag.wait(META_INTERVAL)