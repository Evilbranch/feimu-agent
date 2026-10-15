"""流式对话 - LLM 边生成，句子塞进队列（qwen2.5 专用版）

方案 A：半流式守卫
方案 1：末尾残留补日志
方案 2：冷淡模式空输出兜底
反问硬拦截：history 存储前修正（已播出的不改）
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

def _load_l0_context(user_msg):
    """从 L0 读取跟用户消息相关的认知

    策略：
    1. user_msg 匹配 topic → 用它
    2. 匹配不到且 user_msg 短（≤ 15 字）→ 从 last_turn_topics 兜底
    3. 都失败 → 返回 None
    """
    try:
        from brain.knowledge_gate import _match_l0_topic
        topic, op = _match_l0_topic(user_msg)
    except Exception as e:
        print(f"[L0-流式] 匹配失败: {e}")
        topic, op = None, None

    # 兜底：短消息（反馈类）走 last_turn_topics
    # ★ 但不包括纯寒暄（早上好/晚安/在吗这种）
    _GREETINGS = (
        "早上好", "早安", "午安", "下午好", "晚上好", "晚安", "夜里好",
        "你好", "嗨", "hi", "hello", "在吗", "在不在",
        "吃了吗", "吃饭了吗", "睡了吗",
    )
    _is_greeting = any(
        (user_msg or "").strip().startswith(g) for g in _GREETINGS
    )
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
    """根据 L0 认知生成 prompt 片段"""
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

    # 反馈场景特殊处理：必须承认 + 说出新观点
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

def _build_open_questions_prompt(matched_questions):
    """根据命中的 open_questions 生成 prompt 片段

    强约束：必须原样引用注入的问句，不许自己编。
    """
    if not matched_questions:
        return ""

    # 只取分最高的那条（多条会让她混乱）
    q = matched_questions[0]
    q_text = q.get("question", "")
    refs = q.get("refs", 0)

    if refs >= 2:
        ref_desc = f'（"我心里挂了好几天了"的力度）'
    else:
        ref_desc = f'（"刚想起来"的力度）'

    lines = [
        "\n\n【你必须在这一轮提到这件事】",
        f"\n系统判断这次对话和下面这条**未解的心事**相关：",
        f'\n  【{q_text}】',
        f"\n力度：{ref_desc}",
        "\n\n【如何提】",
        "\n1. 先正常回答用户当前问题（1~2 句）",
        "\n2. 然后用一句话自然过渡，例如：",
        "\n   · '诶，说到这个，我前两天一直在想——'",
        "\n   · '对了，我之前还纠结过一件事——'",
        "\n3. 然后**原样说出上面方括号里的句子**（可改标点，不能改字）",
        "\n",
        "\n【禁止】",
        "\n- 不许自己编一个新问题替代它",
        "\n- 不许省略它（除非对话完全不相关——但系统已经判断相关了）",
        "\n- 不许反复提（这一轮提一次就够）",
        f"\n\n重复确认：你要说的问句是【{q_text}】，"
        f"不是别的。",
    ]
    return "".join(lines)

def _is_feedback_input():
    """判断当前是不是反馈场景（看 state 里最近一次反馈）"""
    try:
        s = state.get_state()
        fb = getattr(s, "last_feedback", None) or {}
        if fb.get("type") in ("positive", "negative"):
            # 反馈后 30 秒内算反馈场景
            ts = fb.get("ts", 0)
            if time.time() - ts < 30:
                return True
    except Exception:
        pass
    return False

# ══════════════════════════════════════════════════════
# 反问节流：记录最近几轮是否以问句结尾
# ══════════════════════════════════════════════════════
_recent_question_endings = []


def _ends_with_question(text):
    """判断回复中是否包含问句（不限于结尾）

    她的反问经常出现在中段（"你觉得呢？我是这么想的"），
    所以不能只看结尾。
    """
    if not text:
        return False
    return ("?" in text) or ("？" in text)


def _should_throttle_question():
    """检查是否要触发反问节流（最近 2 轮都以问句结尾）"""
    if len(_recent_question_endings) < 2:
        return False
    return all(_recent_question_endings[-2:])


def _record_question_ending(ac):
    """记录本轮的结尾类型"""
    _recent_question_endings.append(_ends_with_question(ac))
    if len(_recent_question_endings) > 5:
        _recent_question_endings.pop(0)

def _is_question_sentence(text):
    """判断一句话是不是问句"""
    if not text:
        return False
    s = text.strip()
    if s.endswith("?") or s.endswith("？"):
        return True
    # 含疑问词且带"吗/呢/吧"结尾
    if s.endswith("吗") or s.endswith("吗？") or s.endswith("吗?"):
        return True
    if s.endswith("呢") or s.endswith("呢？"):
        return True
    return False

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

    # ═══ 反问节流（硬规则）═══
    _throttle_question = _should_throttle_question()
    if _throttle_question:
        sysc += (
            "\n\n【反问节流 - 硬规则】"
            "\n你最近两次回复都以问句结尾，这已经被主人反复提醒。"
            "\n这次**禁止**用任何问句。"
            "\n\n反例（禁止）："
            "\n- '哥哥想试试吗？'"
            "\n- '你觉得呢？'"
            "\n- '还有什么想问的吗？'"
            "\n\n正例（必须这样结尾）："
            "\n- '……我先说这么多。'"
            "\n- '……我是这么想的。'"
            "\n- '……这是我目前的看法。'"
            "\n\n最后一句必须用句号结尾。"
            "\n如果想问'你觉得呢'，改成'我是这么想的'。"
        )
        print("[反问节流] 连续 2 轮以问句结尾，本次强制陈述句")

    # ══════════════════════════════════════════════════════
    # L0 认知注入（高优先级）
    # ══════════════════════════════════════════════════════
    _l0_ctx = _load_l0_context(ui)
    if _l0_ctx and _l0_ctx.get("my_view"):
        sysc += _build_l0_prompt(_l0_ctx)
        print(
            f"[L0-流式] 注入认知: {_l0_ctx['topic']} "
            f"(conf={_l0_ctx['confidence']:.2f})"
        )

    # ══════════════════════════════════════════════════════
    # 质变点 3：open_questions 被"想起"
    # ══════════════════════════════════════════════════════
    try:
        from brain.l0_model import find_relevant_open_questions, touch_open_question
        _matched_oq = find_relevant_open_questions(ui, top_k=2)
        if _matched_oq:
            sysc += _build_open_questions_prompt(_matched_oq)
            print(
                f"[openQ] 命中 {len(_matched_oq)} 条: "
                + ", ".join(f"{q['question'][:15]}({q['score']})" for q in _matched_oq)
            )
            # 标记被想起（不在对话结束后立即 touch，避免污染；只记 refs）
            for _q in _matched_oq:
                touch_open_question(_q["id"])
    except Exception as e:
        print(f"[openQ] 异常: {e}")

    sysc += (
        "\n\n【最高优先级 - 当前情绪决定一切】"
        "\n你当前情绪见上方'当前情绪'字段。"
        "\n情绪是生气/难过/失落 → 必须冷淡、短句、可以不理人。"
        "\n禁止说'谢谢夸奖''为你服务''有什么可以帮你'。"
        "\n禁止假装没事、禁止讨好。直接表达情绪。"
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
        "\n"
        "\n【这样写才对 - 不知道时的正确回应】"
        "\n'我记不清了' / '你跟我说过吗？' / '这个我没印象呢'"
        "\n'唔……那哥哥最近怎么样'"
        "\n"
        "\n【绝对禁止的时间/地点/感知词】"
        "\n不许说：'上次'、'之前'、'那次'、'昨天'（除非历史里真出现）"
        "\n不许说：'我在XX'（公园/外面/街上/夜市/咖啡厅）"
        "\n不许说：'我遇到'、'我看到'、'我听到'、'我闻到'"
        "\n"
        "\n如果历史里没有真实内容支撑，宁可说'记不清了'，也不要编。"
    )

    try:
        _known = set()
        for _m in history[-10:]:
            _c = _m.get("content", "") or ""
            for _kw in ["猫", "狗", "工作", "游戏", "代码", "天气", "公园",
                        "咖啡", "电影", "音乐", "吃", "睡", "学习", "朋友",
                        "家", "书", "运动", "画画"]:
                if _kw in _c:
                    _known.add(_kw)
        if _known:
            sysc += (
                f"\n\n【记忆锚点 - 只能引用这些真实出现过的话题】"
                f"\n{', '.join(_known)}"
                f"\n要提到列表以外的话题，不能说'我遇到过''我看到过'，"
                f"只能说'我听你说过''你提过吗'。"
            )
    except Exception:
        pass

    # L2 情景记忆检索
    try:
        from brain.episodic import retrieve_episodes, format_episodes_for_prompt
        episodes = retrieve_episodes(query=ui, top_k=3)
        if episodes:
            sysc += "\n\n" + format_episodes_for_prompt(episodes)
            print(f"[episodic] 检索到 {len(episodes)} 条情景记忆")
    except Exception as e:
        print(f"[episodic] 异常: {e}")

    _label_now = "平静"
    try:
        from brain.persona import get_current_state
        _st = get_current_state()
        _label_now = _st.get("emotion", {}).get("label", "平静")
    except Exception:
        pass
    sysc += (
        f"\n\n【回复前先想 - 不要输出这段思考】"
        f"\n1. 我的情绪：{_label_now}"
        f"\n2. 我的身体状态：我没有身体，看不到、听不到、去不了任何地方"
        f"\n3. 我确定知道的事实：只有上方'参考资料'和'最近真实对话'里的内容"
        f"\n现在，基于以上前提，说出你的回复。"
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
            f"\n如果对方说'还有吗''别的呢''继续'，是要新的信息，"
            f"\n不要重复你上一句说过的内容。"
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
    stream = None

    try:
        print(f"[流式-DEBUG] 准备请求 {provider['model']}...")
        t_req = time.time()
        stream = client.chat.completions.create(
            model=provider["model"],
            messages=msgs,
            timeout=90,
            temperature=0.7,
            max_tokens=400,
            stream=True,
            extra_body={"keep_alive": "30m", "think": False},
        )
        print(f"[流式-DEBUG] 请求已发出，开始接收（{time.time()-t_req:.2f}s）")

        _chunk_count = [0]
        _content_count = [0]
        _first_sent_put = [False]
        for chunk in stream:
            if _chunk_count[0] == 0:
                print(f"[流式-DEBUG] 收到第 1 个 chunk（{time.time()-t_req:.2f}s）")
            _chunk_count[0] += 1
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            piece = ""
            if hasattr(delta, "content") and delta.content:
                piece = delta.content
            if not piece:
                continue

            _content_count[0] += 1
            full_text += piece
            buf += piece

            sentences, buf = _split_sentence_chunk(buf)
            for sent in sentences:
                cleaned = _clean_sentence(sent)
                if not cleaned or len(cleaned) < 2:
                    continue

                # ★ 反问节流：命中时丢掉问句
                if _throttle_question and _is_question_sentence(cleaned):
                    print(f"[反问节流] 拦截问句: {cleaned[:40]}")
                    continue

                if _cold_mode and not _first_checked[0]:
                    _hits = is_soft_output(cleaned)
                    if _hits:
                        print(f"[语气守卫] 首句命中讨好: {_hits} | 原文: {cleaned[:40]}")
                        _interrupted[0] = True
                        break
                    _first_checked[0] = True

                if hesitation_triggered and not _first_sent_put[0]:
                    time.sleep(random.uniform(0.5, 1.2))
                    _first_sent_put[0] = True
                print(f"[流式] 首句就绪 {time.time()-t0:.2f}s: {cleaned[:30]}")
                out_queue.put(cleaned)

            if _interrupted[0]:
                break

        print(f"[流式-DEBUG] 共 {_chunk_count[0]} chunk，{_content_count[0]} 有内容")

        if not _interrupted[0] and buf.strip():
            cleaned = _clean_sentence(buf.strip())
            if _throttle_question and _is_question_sentence(cleaned):
                print(f"[反问节流] 拦截末尾问句: {cleaned[:40]}")
                cleaned = ""

            if _cold_mode:
                _is_empty = (not cleaned or len(cleaned) < 2)
                _is_punct = (cleaned and all(c in "。.，,！!？?…·~～ \t" for c in cleaned))
                if _is_empty or _is_punct:
                    print(f"[语气守卫] 冷淡模式空输出（raw={buf.strip()[:30]!r}），用兜底: 嗯。")
                    cleaned = "嗯。"

            if cleaned and len(cleaned) >= 2:
                if _cold_mode and not _first_checked[0]:
                    _hits = is_soft_output(cleaned)
                    if _hits:
                        print(f"[语气守卫] 末尾命中讨好: {_hits} | 原文: {cleaned[:40]}")
                        _interrupted[0] = True
                if not _interrupted[0]:
                    print(f"[流式] 末尾就绪: {cleaned[:30]}")
                    out_queue.put(cleaned)

    except Exception as e:
        print(f"[流式] LLM 异常: {e}")
        if not full_text:
            out_queue.put("唔……")

    if stream is not None:
        try:
            stream.close()
        except Exception:
            pass

    # ★ 反问节流兜底：如果全被拦了，补一句陈述
    if _throttle_question and out_queue.empty():
        print("[反问节流] 全部被拦，补兜底陈述")
        out_queue.put("嗯，我先说这么多。")

    if _interrupted[0]:
        print("[语气守卫] 首句讨好，中断流式，改走非流式路径（含重生成）")
        while not out_queue.empty():
            try:
                out_queue.get_nowait()
            except Exception:
                break
        try:
            result = _ask_ai_inner(client, provider, history, ui, session_start,
                                    use_tools=True, speaker=speaker,
                                    relation=relation, from_mc=False, source=source)
            if result:
                out_queue.put(result)
            else:
                out_queue.put("嗯。")
        except Exception as e:
            print(f"[语气守卫] 非流式 fallback 异常: {e}")
            out_queue.put("嗯。")
        finally:
            out_queue.put(None)
        return

    out_queue.put(None)

    ac = _clean_sentence(full_text) or "……"
    if not ac:
        ac = "……"
    _record_question_ending(ac)


    try:
        from brain.output_audit import audit_output, pick_safe_reply
        _suspicious, _reasons = audit_output(ac)
        if _suspicious:
            print(f"[审计] 编造嫌疑: {_reasons} | 原文: {ac[:60]}")
            ac = pick_safe_reply()
    except Exception as e:
        print(f"[审计] 异常: {e}")

    # ══════════════════════════════════════════════════════
    # 反问硬拦截：只修正 history 里存储的版本（已播出的不改）
    # ══════════════════════════════════════════════════════
    # 反问硬拦截：只改发给用户的版本（history 保留原始）

    history.append({"role": "user", "content": ui})
    history.append({"role": "assistant", "content": ac})
    save_history(history)
    if s.rag and source not in ("friend", "audience"):
        s.rag.add(ui, ac)

    try:
        extract_async(client, provider, ui, ac, speaker)
    except Exception:
        pass