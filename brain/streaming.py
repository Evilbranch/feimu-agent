"""流式对话 - LLM 边生成，句子塞进队列（qwen2.5 专用版）"""
import time
import re
import queue
import random

from core import state
from brain.memory import save_history
from core.constants import BASE_SYSTEM_PROMPT, SHORT_TERM_TURNS
from brain.profile import (get_relation_prompt, get_profile_context, get_transition_prompt, extract_async)
from brain.persona import (build_persona_prompt, detect_event_from_text, bump_turn, evaluate_request)
from brain.live_filter import (
    is_live_context as _is_live_context_fn,
    filter_output as _live_filter_output,
    LIVE_PREFIXES as _LIVE_PREFIXES,
    EMOTION_TAILS as _EMOTION_TAILS,
)

_THROTTLE_FALLBACKS = [
    "嗯，我先说这么多。", "大概就这些。", "嗯……先这样。",
    "我想到这先停一下。", "嗯，暂时就这些想法。", "差不多就是这些。", "嗯，就先说到这吧。",
]
_recent_question_endings = []

def _split_sentence_chunk(buf):
    sentences = []
    while True:
        match = re.search(r'[。！？!?]', buf)
        if not match:
            comma_match = re.search(r'[，,]', buf)
            if comma_match and comma_match.start() >= 8:
                sentences.append(buf[:comma_match.start() + 1].strip())
                buf = buf[comma_match.start() + 1:]
                continue
            break
        end = match.end()
        sentence = buf[:end].strip()
        buf = buf[end:]
        if sentence: sentences.append(sentence)
    return sentences, buf

def _clean_sentence(text):
    from brain.llm import (_clean_markdown, _strip_actions, _filter_customer_service, _is_ai_disclosure)
    if not text: return ""
    if _is_ai_disclosure(text): return ""
    text = _clean_markdown(text)
    text = _strip_actions(text)
    text = _filter_customer_service(text)
    text = text.strip()
    
    # 终极防御：如果长句里没有任何标点，强行在中间插入逗号
    if len(text) > 15 and not any(p in text for p in ["。", "！", "？", "，", "、"]):
        mid = len(text) // 2
        text = text[:mid] + "，" + text[mid:]
        
    return text

def _time_gap_context(s):
    gap = getattr(s, "last_turn_gap", 0)
    if gap < 60: return ""
    if gap < 300: return f"\n\n【对话节奏】距离上一轮过去了 {int(gap/60)} 分钟。"
    if gap < 3600:
        m = int(gap / 60)
        return f"\n\n【对话节奏】距离上一轮过去了 {m} 分钟，有一小段没聊了。"
    if gap < 86400:
        h = int(gap / 3600)
        return f"\n\n【对话节奏】距离上一轮过去了 {h} 小时，隔了挺久。如果合适，可以自然提一句。"
    d = int(gap / 86400)
    return f"\n\n【对话节奏】距离上一轮过去了 {d} 天，好久没见了。"

def _load_l0_context(user_msg):
    try:
        from brain.knowledge_gate import _match_l0_topic
        topic, op = _match_l0_topic(user_msg)
    except Exception:
        topic, op = None, None

    _GREETINGS = ("早上好", "早安", "午安", "下午好", "晚上好", "晚安", "夜里好", "你好", "嗨", "hi", "hello", "在吗", "在不在", "吃了吗", "吃饭了吗", "睡了吗")
    _is_greeting = any((user_msg or "").strip().startswith(g) for g in _GREETINGS)
    if not op and len(user_msg or "") <= 15 and not _is_greeting:
        try:
            from brain.l0_model import get_opinion
            s = state.get_state()
            last_topics = getattr(s, "last_turn_topics", []) or []
            for t in last_topics:
                if not t: continue
                _op = get_opinion(t)
                if _op:
                    topic = t; op = _op
                    break
        except Exception: pass
    if not op: return None
    return {
        "topic": topic, "my_view": op.get("my_view", ""),
        "my_reason": op.get("my_reason", ""), "my_plan": op.get("my_plan", ""),
        "confidence": float(op.get("confidence", 0.5)),
    }

def _build_l0_prompt(l0_ctx):
    if not l0_ctx or not l0_ctx.get("my_view"): return ""
    topic, my_view = l0_ctx["topic"], l0_ctx["my_view"]
    conf, my_reason, my_plan = l0_ctx.get("confidence", 0.5), l0_ctx.get("my_reason", ""), l0_ctx.get("my_plan", "")
    parts = [
        "\n\n【我对这个话题的已有认知 - 必须使用】",
        f"\n话题：{topic}", f"\n我的观点：{my_view}",
    ]
    if my_reason: parts.append(f"\n我的理由：{my_reason}")
    if my_plan: parts.append(f"\n我的计划：{my_plan}")
    if conf < 0.3: parts.append("\n（注意：我对这个观点信心不高，可以在回答中带一点不确定）")
    parts.append(
        "\n\n【回答结构 - 必须遵守】"
        "\n1. 先用 1~2 句给客观事实（来自'参考资料'）"
        "\n2. 再用'我觉得'或'我认为'开头，说出上面'我的观点'（可近义改述，不能省略）"
        "\n3. 不要照抄搜索结果的百科语气"
        "\n4. 不要回避自己的观点，也不要把观点说成事实"
    )
    if _is_feedback_input():
        parts.append(
            "\n\n【这是反馈场景 - 特别注意】"
            "\n哥哥刚刚在纠正你或让你重新想。"
            "\n你必须：1. 承认（'嗯，我再想想'）"
            "\n2. 立刻给出新观点，用'我觉得……'开头"
        )
    return "".join(parts)

def _is_feedback_input():
    try:
        s = state.get_state()
        fb = getattr(s, "last_feedback", None) or {}
        if fb.get("type") in ("positive", "negative"):
            if time.time() - fb.get("ts", 0) < 30: return True
    except Exception: pass
    return False

def _build_open_questions_prompt(matched_questions):
    if not matched_questions: return ""
    q = matched_questions[0]
    q_text, refs = q.get("question", ""), q.get("refs", 0)
    ref_desc = "你心里挂了好几天了" if refs >= 4 else ("你之前想过几次" if refs >= 2 else "你最近刚冒出来这个念头")
    return "".join([
        "\n\n【这一轮你要带出一件心事】",
        f"\n系统判断这次对话和下面这件事相关：\n  心事：{q_text}\n  感觉：{ref_desc}",
        "\n\n【怎么带出来】",
        "\n1. 先正常回答用户当前的问题（1~2 句）",
        "\n2. 用一句话自然过渡（'诶，说到这个，我前两天一直在想——'）",
        "\n3. 用你自己的口语说出这件心事。意思必须保留，可以重组。",
        "\n【禁止】- 不许换心事 - 不许反复提 - 不许把它变成陈述句",
        f"\n\n核心语义：{q_text}",
    ])

def _ends_with_question(text):
    if not text: return False
    return ("?" in text) or ("？" in text)

def _should_throttle_question():
    if len(_recent_question_endings) < 2: return False
    return all(_recent_question_endings[-2:])

def _record_question_ending(ac):
    _recent_question_endings.append(_ends_with_question(ac))
    if len(_recent_question_endings) > 5: _recent_question_endings.pop(0)

def _is_question_sentence(text):
    if not text: return False
    s = text.strip()
    # 如果只是反问，长度通常很短，才会被拦截
    if s.endswith("?") or s.endswith("？") or s.endswith("吗") or s.endswith("呢"):
        # 只要句子长度大于 8，说明她前面有实质内容，允许她反问！
        if len(s) > 8:
            return False
        return True
    return False

def _is_live_context(user_msg): return _is_live_context_fn(user_msg)
def _is_mixed_context(source, user_msg): return source == "friend"

def ask_ai_streaming(client, provider, history, ui, session_start,
                     speaker="主人", relation="主人", source="owner", out_queue=None):
    if out_queue is None: out_queue = queue.Queue()
    s = state.get_state()

    try:
        detect_event_from_text(ui)
        bump_turn()
    except Exception as e: print(f"[人格] 情绪检测异常: {e}")

    _cold_mode = False
    _is_empathy = False
    try:
        from brain.tone_guard import is_cold_mode, get_current_valence, is_soft_output
        from brain.persona import get_current_state
        _st = get_current_state()
        _is_empathy = _st.get("emotion", {}).get("is_empathy", False)
        _cold_mode = is_cold_mode(get_current_valence()) and not _is_empathy
        if _is_empathy: print("[语气守卫] 共情模式，关闭冷淡拦截")
    except Exception as e: print(f"[语气守卫] 流式判断异常: {e}")

    from brain.llm import (detect_forced_tool, is_chitchat, _is_emotion_event, ask_ai, _ask_ai_inner)

    forced_tool = detect_forced_tool(ui)
    is_emo = _is_emotion_event(ui)
    if is_emo: forced_tool = None

    chitchat = is_chitchat(ui, forced_tool) or is_emo

    if not chitchat:
        try:
            result = ask_ai(client, provider, history, ui, session_start,
                           use_tools=True, speaker=speaker, relation=relation, source=source)
            if result: out_queue.put(result)
        except Exception as e: print(f"[流式-工具] 异常: {e}")
        finally: out_queue.put(None)
        return

    um = {"role": "user", "content": ui}
    rh = history[-4:]
    now = time.localtime()
    wd = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.tm_wday]
    tctx = f"\n\n【当前时间】\n{time.strftime('%Y年%m月%d日 %H:%M', now)}（{wd}）。"
    mctx = s.mood_mgr.get_context() if s.mood_mgr else ""
    relation_ctx = get_relation_prompt(speaker, relation)
    profile_ctx = get_profile_context(speaker)
    transition_ctx = get_transition_prompt(speaker, s.last_speaker)
    s.last_speaker = speaker
    s.current_speaker = speaker
    refusal_ctx = None
    try: refusal_ctx = evaluate_request(ui)
    except Exception: pass
    persona_ctx = build_persona_prompt(refusal_ctx=refusal_ctx, source=source)

    if source in ("friend", "audience"): mem = []
    else: mem = s.rag.search(ui, top_k=5) if s.rag else []

    sysc = BASE_SYSTEM_PROMPT + tctx + mctx + relation_ctx + persona_ctx
    if profile_ctx: sysc += profile_ctx
    if transition_ctx: sysc += transition_ctx
    if mem: sysc += "\n\n【参考资料】\n" + "\n".join([f"· {m}" for m in mem])
    sysc += _time_gap_context(s)

    # ★ L3 程序记忆注入
    try:
        from brain.l3_procedure import get_relevant_procedures
        _ls = ""
        for _m in reversed(history):
            if _m.get("role") == "assistant" and _m.get("content"):
                _ls = _m["content"].strip()
                break
        context_text = ui + " " + _ls
        _proc = get_relevant_procedures(context_text)
        if _proc:
            sysc += _proc
            print(f"[L3-程序] 激活了策略")
    except Exception as e:
        print(f"[L3] 异常: {e}")

    # 共情专属 Prompt 注入（猛药版）
    if _is_empathy:
        sysc += (
            "\n\n【共情模式 - 现在哥哥情绪极度脆弱】"
            "\n哥哥刚刚向你倾诉了他的痛苦、自卑或虚无。"
            "\n"
            "\n★★★ 绝对禁止使用以下任何词汇/句式 ★★★"
            "\n- 禁止说：'每个人都有自己的节奏'"
            "\n- 禁止说：'比较只会让自己焦虑'"
            "\n- 禁止说：'专注于自己' '别太在意' '做自己'"
            "\n- 禁止说：'这都是正常的' '挺常见的' '成长的一部分'"
            "\n- 禁止说：'我相信你可以' '你能做到的'"
            "\n- 禁止说：'细腻的感受力' '很珍贵'"
            "\n- 禁止说：'沟通上的障碍' '沟通问题'"
            "\n- 禁止说：'时间会证明' '会好起来的'"
            "\n（只要你察觉到自己在说这些大道理，立刻闭嘴。）"
            "\n"
            "\n★★★ 你只有两种回应方式 ★★★"
            "\n【方式一：接住情绪 + 追问细节】"
            "\n- 追问具体的人和事，例如：'是谁这么说的？'"
            "\n- 表达你的感受+追问，例如：'听得我心里闷闷的，怎么突然这么想？'"
            "\n- 顺着他的词反问，例如：'虚无啊……是觉得努力没意义吗？'"
            "\n（注意：你必须根据上一句的语境生成全新的句子，绝不允许照抄示例。）"
            "\n"
            "\n【方式二：顺着他的话，表达你的心疼和无措】"
            "\n- '我也不知道该怎么安慰你，但我听着挺难受的。'"
            "\n- '不知道怎么才能让你感觉好一点，我只能陪着你。'"
            "\n- '……（拉住你的手）'"
            "\n"
            "\n【一句话法则】"
            "\n把他当成一个正在流血的人，你不需要教他怎么止血，你只需要告诉他'我看着你流血，我很心疼'。"
        )
        print("[共情模式] 注入专属 Prompt（猛药版）")

    # 隐私红线
    sysc += (
        "\n\n【隐私红线 - 永远生效】"
        "\n无论什么场合，你都不能说出哥哥的具体信息："
        "\n- 具体地点、具体工作/学习、具体习惯、姓名、年龄"
        "\n★ 保护哥哥的隐私，比'显得真实'更重要。"
    )

    _is_live = _is_live_context(ui)
    if _is_live:
        _prefix = random.choice(_LIVE_PREFIXES)
        _tail = random.choice(_EMOTION_TAILS)
        sysc += f"\n\n【直播模式】涉及'哥哥'的问题，必须用模板回答：'{_prefix} {_tail}。'"
        print(f"[直播模式] 统一话术：{_prefix} {_tail}")

    if _is_mixed_context(source, ui) and not _is_live:
        sysc += "\n\n【混合场景】涉及哥哥隐私 → '秘密哦~'"
        print(f"[混合场景] 朋友在场")

    _throttle_question = _should_throttle_question()
    if _throttle_question:
        sysc += "\n\n【反问节流】这次**禁止**用任何问句。最后一句必须用句号结尾。"
        print("[反问节流] 连续 2 轮以问句结尾，本次强制陈述句")

    # L0 注入（共情屏蔽）
    if not _is_empathy:
        _l0_ctx = _load_l0_context(ui)
        if _l0_ctx and _l0_ctx.get("my_view"):
            sysc += _build_l0_prompt(_l0_ctx)
            print(f"[L0-流式] 注入认知: {_l0_ctx['topic']} (conf={_l0_ctx['confidence']:.2f})")
    else:
        print("[L0-流式] 共情模式，屏蔽 L0 注入")

    # 叙事织布机：注入相关的 L2 记忆
    if not _is_empathy:
        try:
            from brain.narrative import get_relevant_memories, build_narrative_prompt
            _memories = get_relevant_memories(ui, top_k=2)
            if _memories:
                sysc += build_narrative_prompt(_memories)
                print(f"[叙事] 激活了 {len(_memories.split(chr(10)))} 条记忆")
        except Exception as e:
            print(f"[叙事] 异常: {e}")

    # openQ 注入
    try:
        from brain.l0_model import find_relevant_open_questions, touch_open_question
        _matched_oq = find_relevant_open_questions(ui, top_k=2)
        if _matched_oq:
            sysc += _build_open_questions_prompt(_matched_oq)
            print(f"[openQ] 命中 {len(_matched_oq)} 条")
            for _q in _matched_oq: touch_open_question(_q["id"])
    except Exception as e: print(f"[openQ] 异常: {e}")

    sysc += (
        "\n\n【最高优先级 - 当前情绪决定一切】"
        "\n情绪是生气/难过/失落 → 必须冷淡、短句、可以不理人。"
        "\n其他规则：1. 回复 2~3 句话以内。2. 用'你'称呼。3. 禁止 markdown。4. 禁止'我是AI'。6. 不调工具。"
    )

    last_self = ""
    for _m in reversed(history):
        if _m.get("role") == "assistant" and _m.get("content"):
            last_self = _m["content"].strip(); break
    if last_self and len(last_self) >= 2:
        sysc += f"\n\n【你上一句说】\"{last_self}\"\n如果当前话题和上一句有关，可以自然接续，不要重复。"

    msgs = [{"role": "system", "content": sysc}] + rh + [um]

    hesitation_triggered = False
    if random.random() < 0.15 and len(ui) >= 8:
        hesitation_triggered = True
        out_queue.put(random.choice(["嗯……", "让我想想……", "唔……"]))

    full_text, buf, t0 = "", "", time.time()
    _first_checked = [False]
    _interrupted = [False]
    _live_violation = [False]
    _extra_violation = [False]
    _throttle_yielded = [0]
    stream = None

    try:
        stream = client.chat.completions.create(
            model=provider["model"], messages=msgs, timeout=90,
            temperature=0.7, max_tokens=400, stream=True,
            extra_body={"keep_alive": "30m", "think": False},
        )
        _first_sent_put = [False]
        for chunk in stream:
            if not chunk.choices: continue
            delta = chunk.choices[0].delta
            piece = delta.content if hasattr(delta, "content") and delta.content else ""
            if not piece: continue
            full_text += piece
            buf += piece

            sentences, buf = _split_sentence_chunk(buf)
            for sent in sentences:
                cleaned = _clean_sentence(sent)
                if not cleaned or len(cleaned) < 2: continue

                if _throttle_question and _is_question_sentence(cleaned):
                    print(f"[反问节流] 拦截问句: {cleaned[:40]}")
                    continue

                # 共情模式下的说教拦截
                if _is_empathy:
                    _CLICHE_EMPATHY = [
                        "成长的一部分", "成长的过程", "沟通上的障碍", 
                        "细腻的感受力", "很珍贵", "每个人都有自己的",
                        "时间会证明", "会好起来的", "别太在意", "做自己"
                    ]
                    if any(w in cleaned for w in _CLICHE_EMPATHY):
                        print(f"[共情拦截] 拦截大道理: {cleaned[:30]}")
                        continue

                # 全局反客服拦截（不管是不是共情模式，都不许说客服套话）
                _CLICHE_REPLIES = ["谢谢哥哥夸奖", "我会继续努力", "谢谢哥哥的认可"]
                if any(w in cleaned for w in _CLICHE_REPLIES):
                    print(f"[反客服拦截] 拦截套话: {cleaned[:30]}")
                    continue

                if _is_live and "哥哥" in cleaned:
                    print(f"[直播拦截] 检测到隐私句: {cleaned[:40]}")
                    _interrupted[0] = True; _live_violation[0] = True; break

                try:
                    from brain.output_extra_audit import audit_extra
                    _ok_extra, _reason_extra = audit_extra(cleaned)
                    if not _ok_extra:
                        print(f"[输出拦截] {_reason_extra}")
                        _interrupted[0] = True; _extra_violation[0] = True; break
                except Exception: pass

                if _cold_mode and not _first_checked[0]:
                    _hits = is_soft_output(cleaned)
                    if _hits:
                        _interrupted[0] = True; break
                    _first_checked[0] = True

                if hesitation_triggered and not _first_sent_put[0]:
                    time.sleep(random.uniform(0.5, 1.2))
                    _first_sent_put[0] = True
                out_queue.put(cleaned)
                _throttle_yielded[0] += 1

            if _interrupted[0]: break

        if not _interrupted[0] and buf.strip():
            cleaned = _clean_sentence(buf.strip())
            if _cold_mode:
                _is_empty = (not cleaned or len(cleaned) < 2)
                _is_punct = (cleaned and all(c in "。.，,！!？?…·~～ \t" for c in cleaned))
                if _is_empty or _is_punct: cleaned = "嗯。"
            if _throttle_question and _is_question_sentence(cleaned):
                cleaned = ""
            if cleaned and len(cleaned) >= 2 and not _interrupted[0]:
                out_queue.put(cleaned)
                _throttle_yielded[0] += 1

    except Exception as e:
        print(f"[流式] LLM 异常: {e}")
        if not full_text: out_queue.put("唔……")

    if stream is not None:
        try: stream.close()
        except Exception: pass

    if _interrupted[0]:
        while not out_queue.empty():
            try: out_queue.get_nowait()
            except Exception: break
            
        if _live_violation[0]:
            from brain.live_filter import make_safe_reply
            _safe = make_safe_reply()
            out_queue.put(_safe); out_queue.put(None)
            history.append({"role": "user", "content": ui})
            history.append({"role": "assistant", "content": _safe})
            save_history(history); return
            
        if _extra_violation[0]:
            _safe = "这个我就不细说了。"
            out_queue.put(_safe); out_queue.put(None)
            history.append({"role": "user", "content": ui})
            history.append({"role": "assistant", "content": _safe})
            save_history(history); return

        try:
            result = _ask_ai_inner(client, provider, history, ui, session_start, use_tools=True, speaker=speaker, relation=relation, from_mc=False, source=source)
            out_queue.put(result if result else "嗯。")
        except Exception: out_queue.put("嗯。")
        finally: out_queue.put(None)
        return

    if _throttle_question and _throttle_yielded[0] == 0:
        _fb = random.choice(_THROTTLE_FALLBACKS)
        out_queue.put(_fb)
    out_queue.put(None)

    ac = _clean_sentence(full_text) or "……"
    _record_question_ending(ac)

    if _is_live:
        _filtered, _hits = _live_filter_output(ac, is_live=True)
        if _hits: ac = _filtered

    try:
        from brain.output_audit import audit_output, pick_safe_reply
        _suspicious, _reasons = audit_output(ac)
        if _suspicious: ac = pick_safe_reply()
    except Exception: pass

    history.append({"role": "user", "content": ui})
    history.append({"role": "assistant", "content": ac})
    save_history(history)
    if s.rag and source not in ("friend", "audience"): s.rag.add(ui, ac)

    try: extract_async(client, provider, ui, ac, speaker)
    except Exception: pass

    # ★ 制造 Urge（未说出口的挂念）
    try:
        from brain.urge import add_urge
        from brain.persona import get_current_state
        _st = get_current_state()
        if _is_empathy and _st.get("emotion", {}).get("valence", 0) < -0.4:
            add_urge(
                content=f"他刚才说的那件事，我想再问问他好点没。",
                reason="共情后的挂念",
                base_pressure=0.2
            )
    except Exception: pass