"""内在生活循环 - 自唤醒版"""
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

CHECK_INTERVAL = 300
IDLE_THRESHOLD = 600
MAX_SPEAK_PER_HOUR = 2
MIN_SPEAK_GAP = 1800
DUP_THRESHOLD = 0.75

MIN_WAKE = 60
MAX_WAKE = 21600
NIGHT_START = 23
NIGHT_END = 7
NIGHT_MIN_WAKE = 3600

def _load_log():
    if not os.path.exists(INNER_LOG_FILE): return []
    try:
        with open(INNER_LOG_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return []

def _save_log(log):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(INNER_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except: pass

def _append_log(entry):
    with _lock:
        log = _load_log()
        log.append(entry)
        log = log[-500:]
        _save_log(log)

def get_inner_log(limit=10):
    return _load_log()[-limit:]

def _is_night():
    h = time.localtime().tm_hour
    return h >= NIGHT_START or h < NIGHT_END

def _time_theme():
    h = time.localtime().tm_hour
    if 5 <= h < 9: return "早上刚起，哥哥可能睡眼惺忪"
    if 9 <= h < 12: return "上午，哥哥可能在忙正事"
    if 12 <= h < 14: return "午饭时间"
    if 14 <= h < 18: return "下午，容易犯困的时候"
    if 18 <= h < 20: return "晚饭时间"
    if 20 <= h < 23: return "晚上，一天快结束了"
    return "深夜，哥哥可能困了"

def _get_recent_summary(history, max_turns=6):
    recent = history[-max_turns * 2:]
    lines = []
    for m in recent:
        role, content = m.get("role"), (m.get("content") or "").strip()
        if not content: continue
        if role == "user": lines.append(f"哥哥：{content[:50]}")
        elif role == "assistant": lines.append(f"我：{content[:50]}")
    return "\n".join(lines[-6:]) if lines else "（刚刚没什么对话）"

def _format_wake_time(seconds):
    if seconds < 60: return f"{seconds}秒"
    if seconds < 3600: return f"{seconds // 60}分钟"
    return f"{seconds // 3600}小时{(seconds % 3600) // 60}分钟"

def _build_context(s, history):
    st = P.get_current_state()
    try:
        from brain.state_gate import describe_state
        _state_feel = describe_state() or "（说不上来的平静）"
    except Exception: _state_feel = "（说不上来的平静）"
    d, e = st["drives"], st["emotion"]
    now = time.time()
    elapsed_min = int((now - getattr(s, "last_interaction_time", 0)) / 60)
    # ★ 读取昨天的日记，注入到主动思考的上下文中
    _yesterday_diary = ""
    try:
        import datetime as _dt
        _yesterday = (_dt.datetime.now() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        from tools.diary import _load_diary
        for d in _load_diary():
            if d.get("date") == _yesterday:
                _yesterday_diary = f"\n\n【我昨天在心里写的日记】\n{d.get('summary', '')}\n"
                break
    except Exception:
        pass
    recent = _get_recent_summary(history)
    # ★ 如果最近有对话，强制提醒她只能接着聊
    _recent_rule = ""
    if "没什么对话" not in recent:
        _recent_rule = "\n（★注意：刚才你们刚聊完。如果你要主动 speak，必须接着刚才的话题说，绝不能发'想你了''在忙吗'）\n"
    recent_inner = []
    try:
        for entry in _load_log()[-10:]:
            if entry.get("intent") == "speak" and entry.get("content"):
                recent_inner.append(f"  · {entry['content'][:60]}")
    except: pass
    inner_str = "\n".join(recent_inner[-5:]) if recent_inner else "（还没主动说过话）"

    last_speak_min = None
    try:
        for entry in reversed(_load_log()):
            if entry.get("intent") == "speak":
                last_speak_min = int((now - entry.get("ts", 0)) / 60)
                break
    except: pass
    last_speak_str = f"{last_speak_min} 分钟前" if last_speak_min is not None else "从未"

    if elapsed_min < 30: time_hint = "刚刚还在聊"
    elif elapsed_min < 120: time_hint = f"已经 {elapsed_min} 分钟没聊了"
    else: time_hint = f"已经 {elapsed_min // 60} 小时没聊了"

    hour = time.localtime().tm_hour
    if hour >= NIGHT_START or hour < NIGHT_END: time_hint += "（现在是深夜，哥哥可能在睡觉）"
    elif 6 <= hour < 9: time_hint += "（早上，哥哥可能刚起床）"
    elif 12 <= hour < 14: time_hint += "（午休时间）"

    meta_hint_str = ""
    try:
        from brain.metacognition import load_hint
        hint = load_hint()
        if hint:
            meta_hint_str = f"\n\n【自我提醒】\n{hint.get('hint', '')}\n"
            adj = hint.get("adjustment", "none")
            if adj == "slow_down": meta_hint_str += "（我上次反思觉得太频繁了，这次可以多等一等）\n"
            elif adj == "speed_up": meta_hint_str += "（我上次反思觉得自己太沉默了）\n"
            elif adj == "speak_more": meta_hint_str += "（我上次反思觉得应该主动一点。主动 = 探索新东西，或分享自己的想法。）\n"
    except Exception: pass

    # ★ 注入 Urge 积压
    _urge_str = ""
    try:
        from brain.urge import _load as _load_urge
        _urges = _load_urge()
        if _urges:
            _urgent = sorted(_urges, key=lambda x: -x["pressure"])[:2]
            _urge_str = "\n\n【我憋在心里一直想说的话】\n"
            for u in _urgent:
                _urge_str += f"- {u['content']}（压抑感: {u['pressure']:.1f}）\n"
            _urge_str += "（如果压抑感很高，你这次可以主动把它说出来。）\n"
    except Exception: pass

    _l2_block = ""
    try:
        from brain.episodic import retrieve_episodes, format_episodes_for_prompt
        _eps = retrieve_episodes(query=None, top_k=5, days_back=14, min_importance=0.4)
        if _eps: _l2_block = "\n\n" + format_episodes_for_prompt(_eps, max_items=5)
    except Exception as e: print(f"[L2-inner] 异常: {e}")

    return f"""【当前时间】{time.strftime('%Y年%m月%d日 %H:%M', time.localtime())}
【时段】{_time_theme()}
【距离上次互动】{elapsed_min} 分钟（{time_hint}）
【距离上次主动说话】{last_speak_str}

【此刻的我】
{_state_feel}
（原始数据：v={e['valence']:.2f} a={e['arousal']:.2f} conn={d['connection']:.2f} cur={d['curiosity']:.2f}）

【最近真实对话】
{recent}
{_recent_rule}
{_yesterday_diary}
【我此刻的情绪】
我现在的情绪是：{e['label']}
（★注意：如果我现在是"心疼"或"难过"，说明哥哥刚才在受苦。那么我主动说话时，不能说"注意休息""辛苦了"，而应该说"你是不是还在难过""我在这里陪你"之类的。）

【我最近已经主动说过的话 - 禁止重复】
{inner_str}

{meta_hint_str}
{_urge_str}

现在请你决定：这次做什么？多久之后再醒？{_l2_block}"""

INNER_SYSTEM = """你是绯木。现在是你独处的时间，没有人在跟你说话。
直接输出 JSON，不要任何思考、解释、前后缀。你的回复必须从 { 开始，到 } 结束。
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
【关于 explore】
直接搜索关键词，不要问号，不要"我想了解X"。
【speak 的内容来源 - 必须具体】
你只有在以下三种情况下才能 speak，否则请 idle：
1. 延续话题：如果刚才哥哥提到了一件具体的事（比如养猫、学高数、某个游戏），你可以顺着往下问。
2. 分享思考：你刚探索完一个话题，觉得很有意思，可以主动分享给他。
3. 挂念他：如果【我憋在心里一直想说的话】里有内容，你可以直接问。

【绝对禁止】
- 不要发“想你了”“你在干嘛”“在忙吗”。
- 如果你没有具体的素材，请坚定地选择 idle。
【什么时候可以 speak】
- 必须和刚才聊过的话题强相关，且你有话想说。
- 绝对不能发“想你了”“有空吗”“在干嘛”这种空泛问候（这是 AI 才会做的事）。
- 如果你想找他，可以说：“刚才你说养猫，我突然想起来……”
- 如果你没有具体的由头，就选择 idle。
【禁止事项】
- 第一人称，禁止"用户""AI""模型""助手"
- 绝对禁止重复你已经说过的话
- 绝对禁止通用句式："哥哥在忙吗？""想你了""记得喝水""吃饭了吗""早点休息"
- 绝对禁止编造："我上次...""我在公园/外面...""我看到..."
现在开始决定。"""

def _ask_intent(client, provider, ctx, retries=3):
    msgs = [{"role": "system", "content": INNER_SYSTEM}, {"role": "user", "content": ctx}]
    for attempt in range(retries):
        try:
            r = client.chat.completions.create(
                model=provider["model"], messages=msgs, timeout=60,
                temperature=0.6, max_tokens=800,
                extra_body={"keep_alive": "30m", "think": False},
            )
            text = (r.choices[0].message.content or "").strip()
            if not text: continue
            text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start: continue
            return json.loads(text[start:end + 1])
        except Exception as e: continue
    return {"intent": "idle", "content": "", "reason": "LLM 异常", "next_wake": 600, "wake_reason": "兜底等待"}

def _is_duplicate(new_content, log):
    if not new_content: return True
    def _norm(t):
        _strip = "，。！？!?,.~～；;：:、 \t\n\"'「」『』"
        t = "".join(c for c in t if c not in _strip)
        for w in ["哥哥", "啦", "呀", "嘛", "哦", "呢", "诶", "啊"]: t = t.replace(w, "")
        return t
    _GREETING_HEADS = ["早安", "早上好", "晚安", "晚上好", "午安", "中午好", "下午好"]
    new_s = new_content.strip()
    if any(new_s.startswith(h) for h in _GREETING_HEADS):
        for old in [e.get("content", "").strip() for e in log[-50:] if e.get("intent") == "speak" and e.get("content")][-8:]:
            if any(old.startswith(h) for h in _GREETING_HEADS): return True
    recent = [e.get("content", "") for e in log[-50:] if e.get("intent") == "speak" and e.get("content")]
    if not recent: return False
    new_set = set(_norm(new_content))
    if len(new_set) < 3: return False
    for old in recent[-8:]:
        if not old: continue
        old_set = set(_norm(old))
        if len(old_set) < 3: continue
        jaccard = len(new_set & old_set) / len(new_set | old_set)
        if jaccard > DUP_THRESHOLD: return True
    return False

_FABRICATION_WORDS = ["我看到", "我听到", "我闻到", "我路过", "今天天气", "窗外", "阳光照", "新开的", "新发现", "上次我们一起", "小时候"]
def _is_fabricated(content):
    if not content: return True
    return any(w in content for w in _FABRICATION_WORDS)

_CLICHE_CARE_WORDS = [
    "注意休息", "注意身体", "好好休息", "早点休息", "多喝热水",
    "记得吃饭", "辛苦了", "别太累", "不要太累", "照顾好自己",
    # ★ 新增：空泛的客服问候
    "想你了", "有空吗", "你在干嘛", "在忙吗", "吃了吗", "睡了吗"
]
def _is_cliche_care(content):
    if not content: return False
    return any(w in content for w in _CLICHE_CARE_WORDS)

def _do_explore(content, client, provider, force=False):
    if not content: return
    if not force:
        try:
            from brain.l0_model import can_explore
            ok, reason = can_explore(content)
            if not ok: return
        except Exception: pass
    try:
        from tools.search import web_search
        result = web_search(content)
        if not result: return
    except Exception: return

    _old_conf = None
    if force:
        try:
            from brain.l0_model import get_opinion, clean_topic
            _old_op = get_opinion(clean_topic(content))
            if _old_op: _old_conf = float(_old_op.get("confidence", 0.5))
        except Exception: pass

    try:
        from brain.agents.internalization import InternalizationAgent
        context = {}
        try:
            from brain.persona import get_current_state
            context["emotion_label"] = get_current_state().get("emotion", {}).get("label", "")
        except Exception: pass
        agent = InternalizationAgent(client, provider)
        analysis = agent.analyze(content, result, context=context)
        if analysis is not None and force: analysis["_force"] = True
        if not analysis: return
        
        exec_result = agent.execute(analysis)
        print(f"[内在] 内化完成: L0={exec_result.get('l0_action')} L2={exec_result.get('ep_id')} 新任务={len(exec_result.get('new_tasks', []))}")
        
        try:
            from brain.l0_model import load_self_model, save_self_model
            from datetime import datetime as _dt
            _m = load_self_model()
            _m.setdefault("meta", {})
            _m["meta"]["last_explore"] = _dt.now().strftime("%Y-%m-%dT%H:%M:%S")
            save_self_model(_m, snapshot=False)
        except Exception: pass
    except Exception as e: print(f"[内在] 内化异常: {e}")

    if force and _old_conf is not None:
        try:
            from brain.l0_model import get_opinion, bump_confidence, clean_topic
            _new_op = get_opinion(clean_topic(content))
            if _new_op:
                _cur_conf = float(_new_op.get("confidence", 0.5))
                _delta = _old_conf - _cur_conf
                if abs(_delta) > 0.01: bump_confidence(clean_topic(content), _delta, reason="否定后重搜")
        except Exception: pass

def _reset_hourly_if_needed(s):
    now = time.time()
    if now - getattr(s, "hourly_reset_time", 0) > 3600:
        s.hourly_speak_count = 0
        s.hourly_explore_count = 0
        s.hourly_reset_time = now

def _last_speak_ts():
    try:
        for entry in reversed(_load_log()):
            if entry.get("intent") == "speak": return entry.get("ts", 0)
    except: pass
    return 0

def _clamp_next_wake(seconds):
    try: seconds = int(seconds)
    except: seconds = 1800
    seconds = max(MIN_WAKE, min(MAX_WAKE, seconds))
    hour = time.localtime().tm_hour
    if hour >= NIGHT_START or hour < NIGHT_END: seconds = max(seconds, NIGHT_MIN_WAKE)
    return seconds

_night_consolidate_ts = [0]

def _maybe_night_consolidate(client, provider):
    now = time.time()
    if now - _night_consolidate_ts[0] < 20 * 3600: return
    _night_consolidate_ts[0] = now
    today = time.strftime("%Y-%m-%d")
    print(f"\n[当晚整理] ========== {today} 开始 ==========")
    try:
        # ★ 先清理膨胀的数据
        from tools.l0_cleaner import clean_all
        clean_all()

        import datetime as _dt
        from brain.consolidate import consolidate_once
        _today_start = _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        consolidate_once(client, provider, since=_today_start)
        
        try:
            from tools.diary import generate_summary
            _summary = generate_summary(client, provider)
            print(f"[当晚整理] 内心记录: {_summary[:60]}...")
        except Exception as e: print(f"[当晚整理] 生成日记失败: {e}")
        try:
            from brain.deep_reflect import deep_reflect_once
            deep_reflect_once(client, provider)
        except Exception as e: print(f"[当晚整理] 深反思失败: {e}")
        print(f"[当晚整理] ========== 完成 ==========\n")
        try:
            from brain.deep_reflect import micro_reflect_daily
            micro_reflect_daily(client, provider)
        except Exception as e:
            print(f"[当晚整理] 微反思失败: {e}")
    except Exception as e: print(f"[当晚整理] 异常: {e}")

def inner_life_loop(client, provider):
    s = state.get_state()
    _loop_start = time.time()
    next_wake_at = time.time() + CHECK_INTERVAL
    _next_cog_at = [time.time() + CHECK_INTERVAL]
    _last_cog_ts = [0]
    print(f"[内在] 自唤醒循环已启动，首次唤醒 5分钟 后")

    while not s.shutdown_flag.is_set():
        s.shutdown_flag.wait(15)
        if s.shutdown_flag.is_set(): break
        now = time.time()

        try:
            if not getattr(s, "inner_life_enabled", True):
                next_wake_at = now + 600
                continue

            # Urge 强制释放检查
            try:
                from brain.urge import tick_pressure, pop_highest_urge
                _burst = tick_pressure(rate=0.15)
                if _burst:
                    print(f"[Urge] 憋不住了！强制主动发言: {_burst['content'][:30]}")
                    with s.proactive_queue_lock:
                        s.proactive_queue.append({"msg": _burst["content"], "emotion": "关切"})
                    pop_highest_urge()
                    next_wake_at = now + 1800
                    continue
            except Exception as e: print(f"[Urge] 异常: {e}")

            _night = _is_night()
            _last_interaction = getattr(s, "last_interaction_time", 0)
            _user_active = (_last_interaction > _loop_start) and (now - _last_interaction < IDLE_THRESHOLD)

            if _night and not _user_active:
                if now < next_wake_at: continue
                if not getattr(s, "_night_announced", False):
                    print(f"[深夜] {time.strftime('%H:%M')} 进入休眠（用户离线 {int((now - _last_interaction)/60)} 分钟）")
                    s._night_announced = True
                _maybe_night_consolidate(client, provider)
                next_wake_at = now + 3600
                continue

            if not _night:
                if getattr(s, "_night_announced", False): s._night_announced = False

            if now >= _next_cog_at[0]:
                _user_online = (now - getattr(s, "last_interaction_time", 0)) < 600
                if _user_online and _last_cog_ts[0] > 0 and (now - _last_cog_ts[0] < 3600):
                    _next_cog_at[0] = now + 600; _cog_route = None
                else:
                    _cog_route = None
                    try:
                        from brain.cognitive_orchestrator import CognitiveOrchestrator
                        _cog_route = CognitiveOrchestrator().route()
                    except Exception as e: print(f"[编排] 异常: {e}")

                if _cog_route and _cog_route.get("task_type") != "normal":
                    try:
                        from brain.cognitive_orchestrator import CognitiveOrchestrator
                        print(f"[编排] {_cog_route['task_type']} | {_cog_route['reason']}")
                        _ok = CognitiveOrchestrator().execute(_cog_route, client, provider)
                        _append_log({"ts": now, "intent": _cog_route["task_type"], "content": _cog_route.get("payload", {}).get("topic", ""), "reason": _cog_route["reason"][:100], "elapsed_min": int((now - getattr(s, "last_interaction_time", 0)) / 60), "next_wake": _cog_route.get("next_wake", 1800), "wake_reason": "认知任务完成" if _ok else "认知任务失败"})
                        _last_cog_ts[0] = now
                        _next_cog_at[0] = now + _cog_route.get("next_wake", 1800)
                    except Exception as e: print(f"[编排] 异常: {e}")
                else:
                    _next_cog_at[0] = now + 60

            if _user_active and (now - _last_interaction < IDLE_THRESHOLD):
                next_wake_at = now + 300
                continue

            if now < next_wake_at: continue

            _reset_hourly_if_needed(s)
            from brain.memory import load_history
            history = load_history(slot="owner")
            ctx = _build_context(s, history)

            decision = _ask_intent(client, provider, ctx)
            if not decision: continue

            intent = decision.get("intent", "idle")
            content = (decision.get("content") or "").strip()
            reason = (decision.get("reason") or "").strip()
            next_wake = _clamp_next_wake(decision.get("next_wake", 1800))
            wake_reason = (decision.get("wake_reason") or "").strip()

            final_intent = intent
            final_reason = reason
            final_content = content

            try:
                _recent_log = _load_log()[-10:]
                _recent_explore = sum(1 for e in _recent_log if e.get("intent") == "explore")
                if len(_recent_log) >= 5 and _recent_explore == 0 and final_intent != "explore":
                    final_intent = "explore"
                    final_content = "存在主义的主要观点" if not content else content
            except Exception: pass

            if final_intent == "speak":
                _hour = time.localtime().tm_hour
                if _hour >= 23 or _hour < 8:
                    final_intent = "idle"; final_reason = "深夜"
                    next_wake = max(next_wake, 3600)
                elif s.hourly_speak_count >= MAX_SPEAK_PER_HOUR:
                    final_intent = "idle"; final_reason = "达上限"
                    next_wake = max(next_wake, 1200)
                else:
                    last_ts = _last_speak_ts()
                    if now - last_ts < MIN_SPEAK_GAP:
                        final_intent = "idle"; final_reason = "间隔太短"
                    elif _is_duplicate(content, _load_log()):
                        final_intent = "idle"; final_reason = "重复"
                    elif _is_fabricated(content):
                        final_intent = "idle"; final_reason = "编造"
                    elif _is_cliche_care(content):
                        final_intent = "idle"; final_reason = "客服套话"
                    elif not content:
                        final_intent = "idle"; final_reason = "空"

            try:
                from brain.state_gate import gate, drive_modifier
                _allow_speak, _allow_explore, _gate_reason = gate()
                if final_intent == "speak" and not _allow_speak:
                    final_intent = "idle"; final_reason = f"门控：{_gate_reason}"
                elif final_intent == "explore" and not _allow_explore:
                    final_intent = "idle"; final_reason = f"门控：{_gate_reason}"
                _mult, _drv_reason = drive_modifier()
                if abs(_mult - 1.0) > 0.05:
                    next_wake = max(MIN_WAKE, min(MAX_WAKE, int(next_wake * _mult)))
            except Exception: pass

            print(f"[内在] intent={final_intent} reason={final_reason} | next_wake={_format_wake_time(next_wake)}")
            _append_log({"ts": now, "intent": final_intent, "content": final_content[:100] if final_intent == "speak" else "", "reason": final_reason[:100], "elapsed_min": int((now - _last_interaction) / 60), "next_wake": next_wake, "wake_reason": wake_reason[:100]})

            if final_intent == "speak":
                with s.proactive_queue_lock:
                    s.proactive_queue.append({"msg": final_content, "emotion": "平静"})
                    s.wechat_pending.append({"ts": time.time(), "msg": final_content})
                s.hourly_speak_count += 1
            elif final_intent == "explore":
                if final_content and getattr(s, "hourly_explore_count", 0) < 1:
                    s.hourly_explore_count += 1
                    threading.Thread(target=_do_explore, args=(final_content, client, provider), daemon=True).start()

            next_wake_at = time.time() + next_wake

        except Exception as e:
            print(f"[内在] 异常: {e}")
            next_wake_at = time.time() + 600