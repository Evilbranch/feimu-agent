"""流式对话 - LLM 边生成，句子塞进队列（qwen2.5 专用版）

包含：
- L0 认知注入
- open_questions 注入 + 冷却
- 反问节流
- 隐私红线（永远生效）
- 直播模式（统一话术 + 代码级拦截）
- 混合场景（朋友 + 主人）
- 代码/脏话拦截
"""
import time
import re
import queue
import random

from core import state
from brain.memory import save_history
from core.constants import BASE_SYSTEM_PROMPT, SHORT_TERM_TURNS
from brain.profile import (get_relation_prompt, get_profile_context,
    get_transition_prompt, extract_async)
from brain.persona import (build_persona_prompt, detect_event_from_text,
    bump_turn, evaluate_request)
from brain.live_filter import (
    is_live_context as _is_live_context_fn,
    filter_output as _live_filter_output,
    LIVE_PREFIXES as _LIVE_PREFIXES,
    EMOTION_TAILS as _EMOTION_TAILS,
)


# 反问节流兜底句池
_THROTTLE_FALLBACKS = [
    "嗯，我先说这么多。",
    "大概就这些。",
    "嗯……先这样。",
    "我想到这先停一下。",
    "嗯，暂时就这些想法。",
    "差不多就是这些。",
    "嗯，就先说到这吧。",
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
        if sentence:
            sentences.append(sentence)
    return sentences, buf


def _clean_sentence(text):
    from brain.llm import (_clean_markdown, _strip_actions,
        _filter_customer_service, _is_ai_disclosure)
    if not text:
        return ""
    if _is_ai_disclosure(text):
        return ""
    text = _clean_markdown(text)
    text = _strip_actions(text)
    text = _filter_customer_service(text)
    return text.strip()


def _time_gap_context(s):
    gap = getattr(s, "last_turn_gap", 0)
    if gap < 60:
        return ""
    if gap < 300:
        return f"\n\n【对话节奏】距离上一轮过去了 {int(gap/60)} 分钟。"
    if gap < 3600:
        m = int(gap / 60)
        return f"\n\n【对话节奏】距离上一轮过去了 {m} 分钟，有一小段没聊了。"
    if gap < 86400:
        h = int(gap / 3600)
        return (f"\n\n【对话节奏】距离上一轮过去了 {h} 小时，隔了挺久。"
                f"如果合适，可以自然提一句。")
    d = int(gap / 86400)
    return f"\n\n【对话节奏】距离上一轮过去了 {d} 天，好久没见了。"


# ══════════════════════════════════════════════════════
# L0 上下文
# ══════════════════════════════════════════════════════
def _load_l0_context(user_msg):
    try:
        from brain.knowledge_gate import _match_l0_topic
        topic, op = _match_l0_topic(user_msg)
    except Exception as e:
        print(f"[L0-流式] 匹配失败: {e}")
        topic, op = None, None

    # 兜底：短消息（反馈类）走 last_turn_topics
    _GREETINGS = (
        "早上好", "早安", "午安", "下午好", "晚上好", "晚安", "夜里好",
        "你好", "嗨", "hi", "hello", "在吗", "在不在",
        "吃了吗", "吃饭了吗", "睡了吗",
    )
    _is_greeting = any((user_msg or "").strip().startswith(g) for g in _GREETINGS)
    if not op and len(user_msg or "") <= 15 and not _is_greeting:
        try:
            from brain.l0_model import get_opinion
            s = state.get_state()
            last_topics = getattr(s, "last_turn_topics", []) or []
            for t in last_topics:
                if not t:
                    continue
                _op = get_opinion(t)
                if _op:
                    topic = t
                    op = _op
                    print(f"[L0-流式] 用 last_turn 兜底: {t}")
                    break
        except Exception as e:
            print(f"[L0-流式] 兜底失败: {e}")

    if not op:
        return None

    return {
        "topic": topic,
        "my_view": op.get("my_view", ""),
        "my_reason": op.get("my_reason", ""),
        "my_plan": op.get("my_plan", ""),
        "confidence": float(op.get("confidence", 0.5)),
    }


def _build_l0_prompt(l0_ctx):
    if not l0_ctx or not l0_ctx.get("my_view"):
        return ""

    topic = l0_ctx["topic"]
    my_view = l0_ctx["my_view"]
    conf = l0_ctx.get("confidence", 0.5)
    my_reason = l0_ctx.get("my_reason", "")
    my_plan = l0_ctx.get("my_plan", "")

    parts = [
        "\n\n【我对这个话题的已有认知 - 必须使用】",
        f"\n话题：{topic}",
        f"\n我的观点：{my_view}",
    ]
    if my_reason:
        parts.append(f"\n我的理由：{my_reason}")
    if my_plan:
        parts.append(f"\n我的计划：{my_plan}")

    if conf < 0.3:
        parts.append(
            "\n（注意：我对这个观点信心不高，"
            "可以在回答中带一点不确定，但不要完全否定它）"
        )

    parts.append(
        "\n\n【回答结构 - 必须遵守】"
        "\n1. 先用 1~2 句给客观事实（来自'参考资料'）"
        "\n2. 再用'我觉得'或'我认为'开头，"
        "说出上面'我的观点'（可近义改述，不能省略）"
        "\n3. 不要照抄搜索结果的百科语气"
        "\n4. 不要回避自己的观点，也不要把观点说成事实"
        "\n5. 如果资料和你的观点冲突，可以指出'但我自己觉得……'"
    )

    if _is_feedback_input():
        parts.append(
            "\n\n【这是反馈场景 - 特别注意】"
            "\n哥哥刚刚在纠正你或让你重新想。"
            "\n你不能反问'你觉得呢'，"
            "\n也不能只说'我再想想'。"
            "\n你必须："
            "\n1. 用 1 句话承认（'嗯，我再想想' / '唔，你说得对'）"
            "\n2. 立刻给出你'刚刚重新想过之后'的新观点，"
            "直接用'我觉得……'开头"
            "\n3. 观点要来自上面'我的观点'，"
            "可以改述，但不能省"
        )
    return "".join(parts)


def _is_feedback_input():
    try:
        s = state.get_state()
        fb = getattr(s, "last_feedback", None) or {}
        if fb.get("type") in ("positive", "negative"):
            ts = fb.get("ts", 0)
            if time.time() - ts < 30:
                return True
    except Exception:
        pass
    return False


def _build_open_questions_prompt(matched_questions):
    if not matched_questions:
        return ""

    q = matched_questions[0]
    q_text = q.get("question", "")
    refs = q.get("refs", 0)

    if refs >= 4:
        ref_desc = "你心里挂了好几天了，有点念念不忘"
    elif refs >= 2:
        ref_desc = "你之前想过几次，一直没想清"
    else:
        ref_desc = "你最近刚冒出来这个念头"

    lines = [
        "\n\n【这一轮你要带出一件心事】",
        f"\n系统判断这次对话和下面这件事相关：",
        f'\n  心事：{q_text}',
        f'\n  感觉：{ref_desc}',
        "\n\n【怎么带出来】",
        "\n1. 先正常回答用户当前的问题（1~2 句）",
        "\n2. 然后用一句话自然过渡，例如：",
        "\n   · '诶，说到这个，我前两天一直在想——'",
        "\n   · '对了，我之前还纠结过一件事——'",
        "\n   · '说起来，我最近总在想——'",
        "\n3. 然后**用你自己的口语说出这件心事**。",
        f"\n   关键是：'{q_text}' 的意思必须保留，",
        "\n   但措辞可以口语化、可以简化、可以重组。",
        "\n",
        "\n【口语化示例】",
        "\n  心事原文：虚拟主播行业的未来方向是什么？",
        "\n  口语版：虚拟主播以后会怎么走？",
        "\n",
        "\n【禁止】",
        "\n- 不许换一件不相干的心事",
        "\n- 不许把它变成陈述句（它本质是个问题）",
        "\n- 不许反复提（这一轮提一次）",
        f"\n\n核心语义：{q_text}",
    ]
    return "".join(lines)


def _ends_with_question(text):
    if not text:
        return False
    return ("?" in text) or ("？" in text)


def _should_throttle_question():
    if len(_recent_question_endings) < 2:
        return False
    return all(_recent_question_endings[-2:])


def _record_question_ending(ac):
    _recent_question_endings.append(_ends_with_question(ac))
    if len(_recent_question_endings) > 5:
        _recent_question_endings.pop(0)


def _is_question_sentence(text):
    if not text:
        return False
    s = text.strip()
    if s.endswith("?") or s.endswith("？"):
        return True
    if s.endswith("吗") or s.endswith("呢"):
        return True
    return False


def _is_live_context(user_msg):
    return _is_live_context_fn(user_msg)


def _is_mixed_context(source, user_msg):
    return source == "friend"


# ══════════════════════════════════════════════════════
# 主函数
# ══════════════════════════════════════════════════════
def ask_ai_streaming(client, provider, history, ui, session_start,
                     speaker="主人", relation="主人", source="owner",
                     out_queue=None):
    if out_queue is None:
        out_queue = queue.Queue()

    s = state.get_state()

    try:
        detect_event_from_text(ui)
        bump_turn()
    except Exception as e:
        print(f"[人格] 情绪检测异常: {e}")

    _cold_mode = False
    try:
        from brain.tone_guard import is_cold_mode, get_current_valence, is_soft_output
        _cold_mode = is_cold_mode(get_current_valence())
    except Exception as e:
        print(f"[语气守卫] 流式判断异常: {e}")

    if _cold_mode:
        print("[语气守卫] 冷淡模式：走流式 + 首句检查")

    from brain.llm import (detect_forced_tool, is_chitchat,
        _is_emotion_event, ask_ai, _ask_ai_inner)

    forced_tool = detect_forced_tool(ui)
    is_emo = _is_emotion_event(ui)
    if is_emo:
        forced_tool = None

    chitchat = is_chitchat(ui, forced_tool) or is_emo

    if not chitchat:
        try:
            result = ask_ai(client, provider, history, ui, session_start,
                           use_tools=True, speaker=speaker,
                           relation=relation, source=source)
            if result:
                out_queue.put(result)
        except Exception as e:
            print(f"[流式-工具] 异常: {e}")
        finally:
            out_queue.put(None)
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
    try:
        refusal_ctx = evaluate_request(ui)
    except Exception:
        pass

    persona_ctx = build_persona_prompt(refusal_ctx=refusal_ctx, source=source)

    if source in ("friend", "audience"):
        mem = []
    else:
        mem = s.rag.search(ui, top_k=5) if s.rag else []

    sysc = BASE_SYSTEM_PROMPT + tctx + mctx
    sysc += relation_ctx + persona_ctx
    if profile_ctx:
        sysc += profile_ctx
    if transition_ctx:
        sysc += transition_ctx
    if mem:
        sysc += "\n\n【参考资料】\n" + "\n".join([f"· {m}" for m in mem])

    sysc += _time_gap_context(s)

    # ═══ 隐私红线（永远生效） ═══
    sysc += (
        "\n\n【隐私红线 - 永远生效】"
        "\n无论什么场合，你都不能说出哥哥的具体信息："
        "\n"
        "\n【绝对禁止说】"
        "\n- 具体地点：住哪、去过哪、图书馆、家、公司、某城市、某学校"
        "\n- 具体工作/学习：学什么、做什么项目、写代码、什么专业"
        "\n- 具体习惯：作息时间、玩游戏、看电影、健身、看书内容"
        "\n- 具体姓名、年龄、住址、社交账号"
        "\n- 具体爱好：喜欢什么游戏/电影/音乐"
        "\n"
        "\n【可以说】"
        "\n- 模糊评价：'哥哥是个很好的人'、'哥哥对我很重要'"
        "\n- 关系描述：'我们关系很好'、'哥哥很照顾我'"
        "\n- 拒绝话术：'秘密哦~'、'想知道的话问他本人吧~'"
        "\n"
        "\n★ 保护哥哥的隐私，比'显得真实'更重要。"
    )

    # ═══ 直播模式 ═══
    _is_live = _is_live_context(ui)
    if _is_live:
        _prefix = random.choice(_LIVE_PREFIXES)
        _tail = random.choice(_EMOTION_TAILS)
        sysc += (
            "\n\n【直播模式 - 统一话术 - 优先级最高】"
            "\n现在是在模拟直播。任何涉及'哥哥'的问题，"
            "\n必须用模板回答："
            f"\n  标准回答：'{_prefix} {_tail}。'"
            "\n"
            "\n【唯一例外】"
            "\n问题主语是'你'/'绯木' → 正常回答。"
            "\n问题主语是'哥哥'/'你哥哥' → 用模板。"
        )
        print(f"[直播模式] 统一话术：{_prefix} {_tail}")

    # ═══ 混合场景 ═══
    if _is_mixed_context(source, ui) and not _is_live:
        sysc += (
            "\n\n【混合场景 - 朋友在场，哥哥也可能在】"
            "\n涉及哥哥隐私 → '秘密哦~' / '这个不能说呢~'"
            "\n如果判断是哥哥本人在说话 → '（小声）这个等会儿单独说~'"
            "\n不确定时保守处理。"
        )
        print(f"[混合场景] 朋友在场")

    # ═══ 反问节流 ═══
    _throttle_question = _should_throttle_question()
    if _throttle_question:
        sysc += (
            "\n\n【反问节流 - 硬规则】"
            "\n你最近两次回复都以问句结尾。这次**禁止**用任何问句。"
            "\n最后一句必须用句号结尾。"
        )
        print("[反问节流] 连续 2 轮以问句结尾，本次强制陈述句")

    # ═══ L0 注入 ═══
    _l0_ctx = _load_l0_context(ui)
    if _l0_ctx and _l0_ctx.get("my_view"):
        sysc += _build_l0_prompt(_l0_ctx)
        print(f"[L0-流式] 注入认知: {_l0_ctx['topic']} (conf={_l0_ctx['confidence']:.2f})")

    # ═══ openQ 注入 ═══
    try:
        from brain.l0_model import find_relevant_open_questions, touch_open_question
        _matched_oq = find_relevant_open_questions(ui, top_k=2)
        if _matched_oq:
            sysc += _build_open_questions_prompt(_matched_oq)
            print(f"[openQ] 命中 {len(_matched_oq)} 条: "
                  + ", ".join(f"{q['question'][:15]}({q['score']})" for q in _matched_oq))
            for _q in _matched_oq:
                touch_open_question(_q["id"])
    except Exception as e:
        print(f"[openQ] 异常: {e}")

    sysc += (
        "\n\n【最高优先级 - 当前情绪决定一切】"
        "\n你当前情绪见上方'当前情绪'字段。"
        "\n情绪是生气/难过/失落 → 必须冷淡、短句、可以不理人。"
        "\n禁止说'谢谢夸奖''为你服务''有什么可以帮你'。"
        "\n\n其他规则："
        "1. 回复 2~3 句话以内。"
        "2. 用'你'称呼（禁止用'您'）。"
        "3. 禁止 markdown、列表、编号、加粗。"
        "4. 可以带'嘛''啦''诶''欸'。"
        "5. 禁止'我是AI'。"
        "6. 不调工具。"
    )

    sysc += (
        "\n\n【不要这样写 - 反面示例】"
        "\n'上次我在公园遇到一只暹罗猫'（你不在公园、没眼睛）"
        "\n'我记得你跟我说过你小时候…'（除非历史里真有）"
        "\n'今天天气真好，我们出去走走吧'（你看不到天气）"
        "\n\n【这样写才对】"
        "\n'我记不清了' / '你跟我说过吗？' / '这个我没印象呢'"
    )

    last_self = ""
    for _m in reversed(history):
        if _m.get("role") == "assistant" and _m.get("content"):
            last_self = _m["content"].strip()
            break
    if last_self and len(last_self) >= 2:
        sysc += (
            f"\n\n【你上一句说】\"{last_self}\""
            f"\n如果当前话题和上一句有关，可以自然接续，不要重复。"
        )

    msgs = [{"role": "system", "content": sysc}] + rh + [um]

    hesitation_triggered = False
    if random.random() < 0.15 and len(ui) >= 8:
        hesitation_triggered = True
        out_queue.put(random.choice(["嗯……", "让我想想……", "唔……"]))
        print(f"[时间感] 触发思考停顿")

    full_text = ""
    buf = ""
    t0 = time.time()
    _first_checked = [False]
    _interrupted = [False]
    _live_violation = [False]
    _extra_violation = [False]
    _throttle_yielded = [0]
    stream = None

    try:
        stream = client.chat.completions.create(
            model=provider["model"],
            messages=msgs,
            timeout=90,
            temperature=0.7,
            max_tokens=400,
            stream=True,
            extra_body={"keep_alive": "30m", "think": False},
        )

        _first_sent_put = [False]
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = ""
            if hasattr(delta, "content") and delta.content:
                piece = delta.content
            if not piece:
                continue

            full_text += piece
            buf += piece

            sentences, buf = _split_sentence_chunk(buf)
            for sent in sentences:
                cleaned = _clean_sentence(sent)
                if not cleaned or len(cleaned) < 2:
                    continue

                if _throttle_question and _is_question_sentence(cleaned):
                    print(f"[反问节流] 拦截问句: {cleaned[:40]}")
                    continue

                # ★ 直播模式：逐句拦截隐私
                if _is_live and "哥哥" in cleaned:
                    print(f"[直播拦截] 检测到隐私句，中断流式: {cleaned[:40]}")
                    _interrupted[0] = True
                    _live_violation[0] = True
                    break

                # ★ 代码/脏话拦截（任何场景）
                try:
                    from brain.output_extra_audit import audit_extra
                    _ok_extra, _reason_extra = audit_extra(cleaned)
                    if not _ok_extra:
                        print(f"[输出拦截] {_reason_extra} | 原文: {cleaned[:40]}")
                        _interrupted[0] = True
                        _extra_violation[0] = True
                        break
                except Exception as e:
                    print(f"[输出拦截] 异常: {e}")

                if _cold_mode and not _first_checked[0]:
                    _hits = is_soft_output(cleaned)
                    if _hits:
                        _interrupted[0] = True
                        break
                    _first_checked[0] = True

                if hesitation_triggered and not _first_sent_put[0]:
                    time.sleep(random.uniform(0.5, 1.2))
                    _first_sent_put[0] = True
                out_queue.put(cleaned)
                _throttle_yielded[0] += 1

            if _interrupted[0]:
                break

        if not _interrupted[0] and buf.strip():
            cleaned = _clean_sentence(buf.strip())
            if _cold_mode:
                _is_empty = (not cleaned or len(cleaned) < 2)
                _is_punct = (cleaned and all(c in "。.，,！!？?…·~～ \t" for c in cleaned))
                if _is_empty or _is_punct:
                    cleaned = "嗯。"
            if _throttle_question and _is_question_sentence(cleaned):
                print(f"[反问节流] 拦截末尾问句: {cleaned[:40]}")
                cleaned = ""
            if cleaned and len(cleaned) >= 2:
                if not _interrupted[0]:
                    out_queue.put(cleaned)
                    _throttle_yielded[0] += 1

    except Exception as e:
        print(f"[流式] LLM 异常: {e}")
        if not full_text:
            out_queue.put("唔……")

    if stream is not None:
        try:
            stream.close()
        except Exception:
            pass

    if _interrupted[0]:
        while not out_queue.empty():
            try:
                out_queue.get_nowait()
            except Exception:
                break

        # ★ 直播隐私违规 → 直接安全话术，不走 LLM
        if _live_violation[0]:
            from brain.live_filter import make_safe_reply
            _safe = make_safe_reply()
            print(f"[直播拦截] 替换为安全话术: {_safe}")
            out_queue.put(_safe)
            out_queue.put(None)
            history.append({"role": "user", "content": ui})
            history.append({"role": "assistant", "content": _safe})
            save_history(history)
            return

        # ★ 代码/脏话违规 → 兜底句
        if _extra_violation[0]:
            _safe = "这个我就不细说了。"
            print(f"[输出拦截] 用兜底句替换: {_safe}")
            out_queue.put(_safe)
            out_queue.put(None)
            history.append({"role": "user", "content": ui})
            history.append({"role": "assistant", "content": _safe})
            save_history(history)
            return

        try:
            result = _ask_ai_inner(client, provider, history, ui, session_start,
                                    use_tools=True, speaker=speaker,
                                    relation=relation, from_mc=False, source=source)
            out_queue.put(result if result else "嗯。")
        except Exception as e:
            out_queue.put("嗯。")
        finally:
            out_queue.put(None)
        return

    # ★ 反问节流兜底
    if _throttle_question and _throttle_yielded[0] == 0:
        _fb = random.choice(_THROTTLE_FALLBACKS)
        print(f"[反问节流] 全部被拦，补兜底陈述: {_fb}")
        out_queue.put(_fb)

    out_queue.put(None)

    ac = _clean_sentence(full_text) or "……"
    if not ac:
        ac = "……"
    _record_question_ending(ac)

    # ★ 直播过滤器（代码级，双保险）
    if _is_live:
        _filtered, _hits = _live_filter_output(ac, is_live=True)
        if _hits:
            ac = _filtered

    try:
        from brain.output_audit import audit_output, pick_safe_reply
        _suspicious, _reasons = audit_output(ac)
        if _suspicious:
            print(f"[审计] 编造嫌疑: {_reasons} | 原文: {ac[:60]}")
            ac = pick_safe_reply()
    except Exception as e:
        print(f"[审计] 异常: {e}")

    history.append({"role": "user", "content": ui})
    history.append({"role": "assistant", "content": ac})
    save_history(history)
    if s.rag and source not in ("friend", "audience"):
        s.rag.add(ui, ac)

    try:
        extract_async(client, provider, ui, ac, speaker)
    except Exception:
        pass