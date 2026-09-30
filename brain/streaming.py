"""流式对话 - LLM 边生成，句子塞进队列（qwen2.5 专用版）"""
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
    """从缓冲区提取完整句子。返回 (句子列表, 剩余缓冲区)"""
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
    """清理单句"""
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
    """把"距离上次对话多久"转成一句提示给 LLM"""
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
    """流式对话。闲聊走流式，工具走 ask_ai。"""
    if out_queue is None:
        out_queue = queue.Queue()

    s = state.get_state()

    try:
        detect_event_from_text(ui)
        bump_turn()
    except Exception as e:
        print(f"[人格] 情绪检测异常: {e}")

    from brain.llm import (detect_forced_tool, is_chitchat,
        _is_emotion_event, ask_ai)

    forced_tool = detect_forced_tool(ui)
    is_emo = _is_emotion_event(ui)
    if is_emo:
        forced_tool = None

    chitchat = is_chitchat(ui, forced_tool) or is_emo

    # 工具路径
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

    # 闲聊路径 - 流式
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

    # 时间感：距离上一轮多久
    sysc += _time_gap_context(s)

    sysc += (
        "\n\n⚠️【最高优先级 - 当前情绪决定一切】"
        "\n你当前情绪见上方'当前情绪'字段。"
        "\n情绪是生气/难过/失落 → 必须冷淡、短句、可以不理人。"
        "\n禁止说'谢谢夸奖''为你服务''有什么可以帮你'。"
        "\n禁止假装没事、禁止讨好。直接表达情绪。"
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
            f"不要重复你上一句说过的内容。"
        )

    msgs = [{"role": "system", "content": sysc}] + rh + [um]

    # 时间感 B：偶尔"想一下"再答
    hesitation_triggered = False
    if random.random() < 0.15 and len(ui) >= 8:
        hesitation_triggered = True
        out_queue.put(random.choice(["嗯……", "让我想想……", "唔……"]))
        print(f"[时间感] 触发思考停顿")

    full_text = ""
    buf = ""
    t0 = time.time()

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
                if cleaned and len(cleaned) >= 2:
                    if hesitation_triggered and not _first_sent_put[0]:
                        time.sleep(random.uniform(0.5, 1.2))
                        _first_sent_put[0] = True
                    print(f"[流式] 首句就绪 {time.time()-t0:.2f}s: {cleaned[:30]}")
                    out_queue.put(cleaned)

        print(f"[流式-DEBUG] 共 {_chunk_count[0]} chunk，{_content_count[0]} 有内容")

        if buf.strip():
            cleaned = _clean_sentence(buf.strip())
            if cleaned and len(cleaned) >= 2:
                out_queue.put(cleaned)

    except Exception as e:
        print(f"[流式] LLM 异常: {e}")
        if not full_text:
            out_queue.put("唔……")
    finally:
        out_queue.put(None)

    ac = _clean_sentence(full_text) or "……"
    if not ac:
        ac = "……"
    history.append({"role": "user", "content": ui})
    history.append({"role": "assistant", "content": ac})
    save_history(history)
    if s.rag and source not in ("friend", "audience"):
        s.rag.add(ui, ac)

    try:
        extract_async(client, provider, ui, ac, speaker)
    except Exception:
        pass