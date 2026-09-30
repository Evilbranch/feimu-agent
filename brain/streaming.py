"""流式对话 - LLM 边生成，句子塞进队列（qwen2.5 专用版）

方案 A：半流式守卫
- 冷淡模式（v < -0.4）下走流式，但第一句送 TTS 前先检查
- 第一句不讨好 → 正常流式，首句 1~2s
- 第一句讨好 → 中断流，改走非流式 _ask_ai_inner（含守卫 + 重生成 + 兜底）

方案 1：末尾残留补日志
方案 2：冷淡模式空输出兜底

L2 情景记忆：检索结构化情景记忆注入 prompt
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

    # 方案二：Narrative-Experts 记忆锚点
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

    # ══════════════════════════════════════════════════════
    # L2 情景记忆检索
    # ══════════════════════════════════════════════════════
    try:
        from brain.episodic import retrieve_episodes, format_episodes_for_prompt
        episodes = retrieve_episodes(query=ui, top_k=3)
        if episodes:
            sysc += "\n\n" + format_episodes_for_prompt(episodes)
            print(f"[episodic] 检索到 {len(episodes)} 条情景记忆")
    except Exception as e:
        print(f"[episodic] 异常: {e}")

    # 方案四：Physics Scratchpad
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

    # L1：让她"看到"自己上一句
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