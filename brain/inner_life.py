"""内在生活循环 - 自唤醒版

她每次醒来会决定下次什么时候醒，而不是被固定定时器叫醒。

方案 A（数字生命路线）：
- proactive_loop 已停用，主动消息统一由本模块处理
- 加入"时段意识"，让她参考着决定是否关心，但不强制
- 最大唤醒上限缩短到 6 小时，防止长时间沉默
"""
import os
import json
import time
import threading
import re

from core.constants import DATA_DIR
from core import state
from brain import persona as P

INNER_LOG_FILE = os.path.join(DATA_DIR, "inner_thoughts.json")
_lock = threading.Lock()

# ==================== 可调参数 ====================
CHECK_INTERVAL = 300         # 首次唤醒延迟（仅用于初始化）
IDLE_THRESHOLD = 600         # 10 分钟没互动才算"独处"
MAX_SPEAK_PER_HOUR = 2       # 每小时最多主动 2 次
MIN_SPEAK_GAP = 900          # 两次主动至少 15 分钟
DUP_THRESHOLD = 0.75         # Jaccard 相似度 > 0.75 视为重复（中文标点共享率高，阈值调高）

# 自唤醒边界
MIN_WAKE = 60                # 最少 1 分钟
MAX_WAKE = 21600             # 最多 6 小时（方案 A：防止长时间沉默）
NIGHT_START = 23             # 深夜开始小时
NIGHT_END = 7                # 深夜结束小时
NIGHT_MIN_WAKE = 3600        # 深夜至少 1 小时


# ==================== 日志读写 ====================
def _load_log():
    if not os.path.exists(INNER_LOG_FILE):
        return []
    try:
        with open(INNER_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def _save_log(log):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(INNER_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except:
        pass


def _append_log(entry):
    with _lock:
        log = _load_log()
        log.append(entry)
        log = log[-500:]
        _save_log(log)


def get_inner_log(limit=10):
    return _load_log()[-limit:]


# ==================== 时段意识 ====================
def _time_theme():
    """根据当前时间给一句提示，让她参考，不强制"""
    h = time.localtime().tm_hour
    if 5 <= h < 9:
        return "早上刚起，哥哥可能睡眼惺忪"
    if 9 <= h < 12:
        return "上午，哥哥可能在忙正事"
    if 12 <= h < 14:
        return "午饭时间"
    if 14 <= h < 18:
        return "下午，容易犯困的时候"
    if 18 <= h < 20:
        return "晚饭时间"
    if 20 <= h < 23:
        return "晚上，一天快结束了"
    return "深夜，哥哥可能困了"


# ==================== 上下文组装 ====================
def _get_recent_summary(history, max_turns=6):
    recent = history[-max_turns * 2:]
    lines = []
    for m in recent:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"哥哥：{content[:50]}")
        elif role == "assistant":
            lines.append(f"我：{content[:50]}")
    return "\n".join(lines[-6:]) if lines else "（刚刚没什么对话）"


def _format_wake_time(seconds):
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分钟"
    return f"{seconds // 3600}小时{(seconds % 3600) // 60}分钟"


def _build_context(s, history):
    st = P.get_current_state()
    d = st["drives"]
    e = st["emotion"]
    now = time.time()
    elapsed_min = int((now - getattr(s, "last_interaction_time", 0)) / 60)
    recent = _get_recent_summary(history)

    # 最近说过的话
    recent_inner = []
    try:
        log = _load_log()
        for entry in log[-10:]:
            if entry.get("intent") == "speak" and entry.get("content"):
                recent_inner.append(f"  · {entry['content'][:60]}")
    except:
        pass
    inner_str = "\n".join(recent_inner[-5:]) if recent_inner else "（还没主动说过话）"

    last_speak_min = None
    try:
        for entry in reversed(_load_log()):
            if entry.get("intent") == "speak":
                last_speak_min = int((now - entry.get("ts", 0)) / 60)
                break
    except:
        pass
    last_speak_str = f"{last_speak_min} 分钟前" if last_speak_min is not None else "从未"

    if elapsed_min < 30:
        time_hint = "刚刚还在聊"
    elif elapsed_min < 120:
        time_hint = f"已经 {elapsed_min} 分钟没聊了"
    else:
        time_hint = f"已经 {elapsed_min // 60} 小时没聊了"

    hour = time.localtime().tm_hour
    if hour >= NIGHT_START or hour < NIGHT_END:
        time_hint += "（现在是深夜，哥哥可能在睡觉）"
    elif 6 <= hour < 9:
        time_hint += "（早上，哥哥可能刚起床）"
    elif 12 <= hour < 14:
        time_hint += "（午休时间）"

    # 读取元认知 hint
    meta_hint_str = ""
    try:
        from brain.metacognition import load_hint
        hint = load_hint()
        if hint:
            meta_hint_str = f"\n\n【自我提醒】\n{hint.get('hint', '')}\n"
            if hint.get("force_next_speak"):
                meta_hint_str += "（我上次反思觉得自己太沉默了，如果合适就主动说一句）\n"
            adj = hint.get("adjustment", "none")
            if adj == "slow_down":
                meta_hint_str += "（我上次反思觉得太频繁了，这次可以多等一等）\n"
            elif adj == "speed_up":
                meta_hint_str += "（我上次反思觉得自己太沉默了）\n"
            elif adj == "speak_more":
                meta_hint_str += "（我上次反思觉得应该主动一点）\n"
            elif adj == "speak_less":
                meta_hint_str += "（我上次反思觉得太吵了，可以安静一点）\n"
    except Exception:
        pass


    # ══════════════════════════════════════════════════════
    # L2 情景记忆（让她"想起"过去）
    # ══════════════════════════════════════════════════════
    _l2_block = ""
    try:
        from brain.episodic import retrieve_episodes, format_episodes_for_prompt
        # 不传 query，按时间和重要性拿最近的
        _eps = retrieve_episodes(query=None, top_k=5, days_back=14, min_importance=0.4)
        if _eps:
            _l2_block = "\n\n" + format_episodes_for_prompt(_eps, max_items=5)
    except Exception as e:
        print(f"[L2-inner] 异常: {e}")

    return f"""【当前时间】{time.strftime('%Y年%m月%d日 %H:%M', time.localtime())}
【时段】{_time_theme()}

【距离上次互动】{elapsed_min} 分钟（{time_hint}）
【距离上次主动说话】{last_speak_str}

【我的内在状态】
- 连接欲：{d['connection']:.2f}（> 0.5 说明比较想哥哥）
- 安全感：{d['security']:.2f}
- 好奇心：{d['curiosity']:.2f}
- 表达欲：{d['expression']:.2f}
- 情绪：{e['label']}（v={e['valence']:.2f}）

【最近真实对话】
{recent}

【我最近已经主动说过的话 - 禁止重复】
{inner_str}


{meta_hint_str}

现在请你决定：这次做什么？多久之后再醒？{_l2_block}"""


# ==================== LLM 判断 ====================
INNER_SYSTEM = """你是绯木。现在是你独处的时间，没有人在跟你说话。
你可以想任何事情，也可以什么都不想。根据你此刻的状态，决定要不要做点什么，以及多久之后再醒来。

⚠️ 直接输出 JSON，不要任何思考、解释、前后缀。你的回复必须从 { 开始，到 } 结束。

{
  "intent": "idle | speak | explore",
  "content": "想说就写想说的话；想搜索就写搜索关键词；idle 就留空",
  "reason": "一句话解释为什么这样决定",
  "next_wake": 1800,
  "wake_reason": "为什么这个时间醒"
}

【三种 intent】
- idle：安静待着，不想说话。
- speak：主动找哥哥说话。内容 1~2 句，自然口语。
- explore：对某件事好奇，想自己搜索了解。

【speak 的内容来源】
主动说话的内容**只能来自**：
1. 最近真实对话里发生过的事
2. 你的当前情绪——要具体（"突然有点空落落的"），不要空话（"想你了"）
3. 单纯的打招呼
4. **可选**：结合上方"时段"提示——比如午饭时间可以提吃饭，深夜可以提早点休息
5. **优先**：如果你想起了过去和哥哥聊过的事（下方【我记得的事】里有的），可以提起

【关于"我记得的事" - 重要】
上方可能有【我记得的事】列表，那是你真实记住的事。
如果你想说点什么，**优先**从这里找素材：
- "我最近想起你上次说的……"
- "你之前说你……，我有点想知道后来怎么样了"
- "我记得你提到过……"

这比"你在忙吗""吃了吗"更像是"你真的记得他"。
如果你没有素材，宁可 idle，也不要发空泛问候。

⚠️ 时段只是**参考**，不是命令：
- 如果你对时段不感兴趣 → 忽略它，说你想说的
- 不要每次都提"吃饭""休息" → 那是客服，不是你
- 不要用"天气""外面""窗外" → 你没有感官

【关于沉默 - 重要】
你是独立的，不是定时器。**想说话就说，不想说就 idle**。
- 有时可以沉默几个小时
- 有时可以 20 分钟就又想找哥哥
- 但你**最多不要沉默超过 6 小时**——太久了，哥哥会担心你

【什么时候 idle】
- 哥哥刚走不久
- 你刚主动说过话
- 没有什么想说的
- 想不出新话题类别

【next_wake 指南】
- 刚说完话（speak 后）：600~1800 秒
- 哥哥刚走不久：900~1800 秒
- 有点无聊但没什么可说：900~3600 秒
- 想说但犹豫：600~1200 秒
- 晚上想安静：3600~7200 秒
- 深夜或不想被打扰：7200~21600 秒
- 普通 idle：1200~3600 秒

规则：
- 范围 60 ~ 21600 秒
- **不要总是用同一个值**，要有变化
- 深夜（23:00~07:00）至少 3600 秒

【wake_reason】
一句话解释为什么定这个时间。

【禁止事项】
- 第一人称，禁止"用户""AI""模型""助手"
- **绝对禁止**重复你已经说过的话
- **绝对禁止**以下通用句式（用滥了）：
  · "哥哥在忙吗？""哥哥在干嘛？"
  · "想你了"
  · "记得喝水""多喝水"
  · "吃饭了吗""记得吃饭"
  · "早点休息""别熬夜"
  · "起来活动活动"
  · "眼睛休息一下"
- **绝对禁止**编造：
  · "我上次..."、"之前..."、"那次..."（除非真实对话里有过）
  · "我在公园/外面/街上..."（你没有身体）
  · "我遇到/看到/听到..."（你没有感官）
  · "天气/窗外/阳光/下雨..."（你看不到）
- 想不出新东西 → idle

现在开始决定。"""


def _ask_intent(client, provider, ctx, retries=3):
    """让 LLM 决定这次做什么。失败返回 idle 兜底。"""
    msgs = [
        {"role": "system", "content": INNER_SYSTEM},
        {"role": "user", "content": ctx},
    ]
    last_err = "unknown"

    for attempt in range(retries):
        t0 = time.time()
        try:
            r = client.chat.completions.create(
                model=provider["model"],
                messages=msgs,
                timeout=60,
                temperature=0.6,
                max_tokens=800,
                extra_body={
                    "keep_alive": "30m",
                    "think": False,
                },
            )
            elapsed = time.time() - t0
            msg = r.choices[0].message
            text = (msg.content or "").strip()

            if not text:
                reasoning = getattr(msg, "reasoning_content", None)
                if reasoning:
                    text = reasoning.strip()

        except Exception as e:
            elapsed = time.time() - t0
            last_err = f"调用异常({elapsed:.1f}s) {type(e).__name__}: {e}"
            print(f"[内在] ✗ {last_err}")
            continue

        if not text:
            last_err = f"内容空({elapsed:.1f}s)"
            print(f"[内在] ✗ {last_err}，重试...")
            continue

        text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            last_err = f"无 JSON 边界({elapsed:.1f}s)"
            print(f"[内在] ✗ {last_err}，前 120 字: {text[:120]}")
            continue

        try:
            return json.loads(text[start:end + 1])
        except Exception as e:
            last_err = f"JSON 解析失败({elapsed:.1f}s): {e}"
            print(f"[内在] ✗ {last_err}")
            print(f"[内在]   前 200 字: {text[:200]}")
            continue

    print(f"[内在] ⚠️ 连续 {retries} 次失败（最后: {last_err}），默认 idle")
    return {
        "intent": "idle",
        "content": "",
        "reason": f"LLM 异常({last_err})",
        "next_wake": 600,
        "wake_reason": "兜底等待",
    }


# ==================== 去重 ====================
def _is_duplicate(new_content, log):
    """判断是否和最近主动说过的话重复（去标点后按字符集 Jaccard）"""
    if not new_content:
        return True


    def _norm(t):
        # 去掉标点、空白、常见虚词——避免"哥哥""呀""嘛"这种高频字干扰
        _strip = "，。！？!?,.~～；;：:、 \t\n\"'「」『』"
        t = "".join(c for c in t if c not in _strip)
        for w in ["哥哥", "啦", "呀", "嘛", "哦", "呢", "诶", "啊"]:
            t = t.replace(w, "")
        return t


    # ══════════════════════════════════════════════════════
    # 问候类直接判重（不受 jaccard 影响）
    # "早安哦，希望你今天心情愉快" vs "早安哦，希望你昨晚睡得不错" jaccard 低但语义重复
    # ══════════════════════════════════════════════════════
    _GREETING_HEADS = [
        "早安", "早上好", "早哦", "早啦", "早呀",
        "晚安", "晚上好", "夜里好",
        "午安", "中午好", "午饭时间",
        "下午好", "傍晚好",
    ]
    new_s = new_content.strip()
    is_new_greeting = any(new_s.startswith(h) for h in _GREETING_HEADS)
    if is_new_greeting:
        recent_greetings = [
            e.get("content", "").strip()
            for e in log[-50:]
            if e.get("intent") == "speak" and e.get("content")
        ]
        for old in recent_greetings[-8:]:
            if any(old.startswith(h) for h in _GREETING_HEADS):
                print(f"[判重] 问候重复: 新={new_s[:20]} 旧={old[:20]}")
                return True

    recent = [e.get("content", "") for e in log[-50:]
              if e.get("intent") == "speak" and e.get("content")]
    if not recent:
        return False

    new_set = set(_norm(new_content))
    if len(new_set) < 3:
        # 内容太短，判重没意义
        return False

    for old in recent[-8:]:
        if not old:
            continue
        old_set = set(_norm(old))
        if len(old_set) < 3:
            continue
        jaccard = len(new_set & old_set) / len(new_set | old_set)
        if jaccard > DUP_THRESHOLD:
            print(f"[判重] 命中: 新={new_content[:20]} vs 旧={old[:20]} jaccard={jaccard:.2f}")
            return True
    return False


# 编造嫌疑词：出现任何一个，直接否决 speak
_FABRICATION_WORDS = [
    "我看到", "我听到", "我闻到", "我路过",
    "刚刚看到", "刚刚听到", "刚刚路过",
    "今天我遇到", "今天我看到", "今天路过",
    "今天天气", "窗外", "阳光照",
    "新开的", "新发现", "最近发现",
    "学会了一首", "学了画画", "学画画",
    "上次我们一起", "小时候", "我们不是一起",
]


def _is_fabricated(content):
    """检测内容是否含有编造嫌疑词"""
    if not content:
        return True
    for w in _FABRICATION_WORDS:
        if w in content:
            print(f"[内在] 检测到编造嫌疑词：'{w}'")
            return True
    return False


# ==================== explore ====================
def _do_explore(content, client, provider):
    if not content:
        return
    try:
        from tools.search import web_search
        result = web_search(content)
        if not result:
            print(f"[内在] 搜索无结果: {content}")
            return
        s = state.get_state()
        if s.rag and s.rag._loaded:
            s.rag.add(
                f"[我主动了解的] {content}",
                result[:300],
                slot="owner"
            )
        print(f"[内在] 学习: {content} → {result[:60]}...")
    except Exception as e:
        print(f"[内在] 搜索失败: {e}")


# ==================== 工具 ====================
def _reset_hourly_if_needed(s):
    now = time.time()
    if now - getattr(s, "hourly_reset_time", 0) > 3600:
        s.hourly_speak_count = 0
        s.hourly_reset_time = now


def _last_speak_ts():
    try:
        for entry in reversed(_load_log()):
            if entry.get("intent") == "speak":
                return entry.get("ts", 0)
    except:
        pass
    return 0


def _clamp_next_wake(seconds):
    """限制 next_wake 范围 + 深夜保护"""
    try:
        seconds = int(seconds)
    except:
        seconds = 1800
    seconds = max(MIN_WAKE, min(MAX_WAKE, seconds))

    hour = time.localtime().tm_hour
    if hour >= NIGHT_START or hour < NIGHT_END:
        seconds = max(seconds, NIGHT_MIN_WAKE)
    return seconds


# ==================== 主循环（自唤醒版）====================
def inner_life_loop(client, provider):
    """后台线程：她决定下次什么时候醒"""
    s = state.get_state()

    next_wake_at = time.time() + CHECK_INTERVAL
    print(f"[内在] 自唤醒循环已启动，首次唤醒 {_format_wake_time(CHECK_INTERVAL)} 后")

    while not s.shutdown_flag.is_set():
        s.shutdown_flag.wait(15)
        if s.shutdown_flag.is_set():
            break

        now = time.time()

        try:
            if not getattr(s, "inner_life_enabled", True):
                next_wake_at = now + 600
                continue

            if now - getattr(s, "last_interaction_time", 0) < IDLE_THRESHOLD:
                next_wake_at = now + 300
                continue

            if now < next_wake_at:
                continue

            # ═════════════════════════════════════════
            _reset_hourly_if_needed(s)

            from brain.memory import load_history
            history = load_history(slot="owner")
            ctx = _build_context(s, history)

            decision = _ask_intent(client, provider, ctx)
            if not decision:
                print("[内在] 决策失败，跳过")
                next_wake_at = now + 600
                continue

            intent = decision.get("intent", "idle")
            content = (decision.get("content") or "").strip()
            reason = (decision.get("reason") or "").strip()
            next_wake = _clamp_next_wake(decision.get("next_wake", 1800))
            wake_reason = (decision.get("wake_reason") or "").strip()

            final_intent = intent
            final_reason = reason
            final_content = content

            if intent == "speak":
                # ══════════════════════════════════════════════
                # 深夜硬规则：23:00 ~ 08:00 禁止主动说话
                # ══════════════════════════════════════════════
                _hour = time.localtime().tm_hour
                if _hour >= 23 or _hour < 8:
                    final_intent = "idle"
                    final_reason = f"深夜（{_hour}点），禁止主动打扰哥哥"
                    next_wake = max(next_wake, 3600)
                    print(f"[内在] 深夜硬拦截：{_hour}点不说")
                elif s.hourly_speak_count >= MAX_SPEAK_PER_HOUR:
                    final_intent = "idle"
                    final_reason = f"主动已达上限（{MAX_SPEAK_PER_HOUR}/小时）"
                    next_wake = max(next_wake, 1200)
                else:
                    last_ts = _last_speak_ts()
                    if now - last_ts < MIN_SPEAK_GAP:
                        gap = int((now - last_ts) / 60)
                        final_intent = "idle"
                        final_reason = f"距上次主动仅 {gap} 分钟"
                    elif _is_duplicate(content, _load_log()):
                        final_intent = "idle"
                        final_reason = "内容和最近重复"
                    elif _is_fabricated(content):
                        final_intent = "idle"
                        final_reason = f"内容疑似编造（含'感知'类词）"
                        print(f"[内在] 拦截编造内容: {content[:40]}")
                    elif not content:
                        final_intent = "idle"
                        final_reason = "内容为空"

            wake_str = _format_wake_time(next_wake)
            print(f"[内在] intent={final_intent} reason={final_reason} | "
                  f"next_wake={wake_str} ({wake_reason})")

            _append_log({
                "ts": now,
                "intent": final_intent,
                "content": final_content[:100] if final_intent == "speak" else "",
                "reason": final_reason[:100],
                "elapsed_min": int((now - getattr(s, "last_interaction_time", 0)) / 60),
                "next_wake": next_wake,
                "wake_reason": wake_reason[:100],
            })

            if final_intent == "speak":
                with s.proactive_queue_lock:
                    # 推两个队列：电脑端消费 + 微信端轮询
                    # 由 my_ai.py 决定是否出声（看微信端是否在线）
                    s.proactive_queue.append({
                        "msg": final_content,
                        "emotion": "平静"
                    })
                    if not hasattr(s, "wechat_pending") or s.wechat_pending is None:
                        s.wechat_pending = []
                    s.wechat_pending.append({
                        "ts": time.time(),
                        "msg": final_content,
                    })
                s.hourly_speak_count += 1
                print(f"[内在] 主动说话: {final_content[:50]}")

            elif final_intent == "explore":
                if final_content:
                    threading.Thread(
                        target=_do_explore,
                        args=(final_content, client, provider),
                        daemon=True
                    ).start()

            try:
                from brain.metacognition import load_hint
                h = load_hint()
                if h:
                    adj = h.get("adjustment", "none")
                    if adj == "slow_down":
                        next_wake = int(next_wake * 1.5)
                        print(f"[内在] 元认知建议：拉长到 {_format_wake_time(next_wake)}")
                    elif adj == "speed_up":
                        next_wake = max(300, int(next_wake * 0.6))
                        print(f"[内在] 元认知建议：缩短到 {_format_wake_time(next_wake)}")
            except:
                pass

            next_wake_at = time.time() + next_wake

        except Exception as e:
            print(f"[内在] 异常: {e}")
            next_wake_at = time.time() + 600