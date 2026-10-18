"""流式对话 - LLM 边生成，句子塞进队列（qwen2.5 专用版）"""
import time
import re
import queue
import random

from core import state
from brain.llm_lock import LLM_LOCK
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
from brain.vision import vision_analyze, file_to_b64

_THROTTLE_FALLBACKS = ["嗯，我先说这么多。", "大概就这些。", "嗯……先这样。", "我想到这先停一下。", "嗯，暂时就这些想法。", "差不多就是这些。", "嗯，就先说到这吧。"]
_recent_question_endings = []

def _split_sentence_chunk(buf):
    sentences = []
    while True:
        match = re.search(r'[。！？!?]', buf)
        if not match:
            comma_match = re.search(r'[，,]', buf)
            if comma_match and comma_match.start() >= 15:
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
    if len(text) > 15 and not any(p in text for p in ["。", "！", "？", "，", "、"]):
        mid = len(text) // 2
        text = text[:mid] + "，" + text[mid:]
    return text

def _time_gap_context(s):
    gap = getattr(s, "last_turn_gap", 0)
    if gap < 60: return ""
    if gap < 300: return f"\n\n【对话节奏】距离上一轮过去了 {int(gap/60)} 分钟。"
    if gap < 3600: return f"\n\n【对话节奏】距离上一轮过去了 {int(gap/60)} 分钟，有一小段没聊了。"
    if gap < 86400: return f"\n\n【对话节奏】距离上一轮过去了 {int(gap/3600)} 小时，隔了挺久。"
    return f"\n\n【对话节奏】距离上一轮过去了 {int(gap/86400)} 天，好久没见了。"

def _load_l0_context(user_msg):
    try:
        from brain.knowledge_gate import _match_l0_topic
        topic, op = _match_l0_topic(user_msg)
    except Exception:
        topic, op = None, None
    if not op: return None
    return {"topic": topic, "my_view": op.get("my_view", ""), "my_reason": op.get("my_reason", ""), "my_plan": op.get("my_plan", ""), "confidence": float(op.get("confidence", 0.5))}

def _build_l0_prompt(l0_ctx):
    if not l0_ctx or not l0_ctx.get("my_view"): return ""
    parts = ["\n\n【我对这个话题的已有认知 - 必须使用】", f"\n话题：{l0_ctx['topic']}", f"\n我的观点：{l0_ctx['my_view']}"]
    parts.append("\n\n【回答结构 - 必须遵守】\n1. 先用 1~2 句给客观事实\n2. 再用'我觉得'开头说出'我的观点'")
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
    return f"\n\n【这一轮你要带出一件心事】\n系统判断这次对话和下面这件事相关：\n  心事：{q.get('question', '')}\n【怎么带出来】\n1. 先正常回答\n2. 用一句话自然过渡\n3. 说出这件心事。不许换心事。"

def _ends_with_question(text): return ("?" in text) or ("？" in text)

def _should_throttle_question():
    if len(_recent_question_endings) < 2: return False
    return all(_recent_question_endings[-2:])

def _record_question_ending(ac):
    _recent_question_endings.append(_ends_with_question(ac))
    if len(_recent_question_endings) > 5: _recent_question_endings.pop(0)

def _is_question_sentence(text):
    if not text: return False
    s = text.strip()
    if s.endswith("?") or s.endswith("？") or s.endswith("吗") or s.endswith("呢"):
        if len(s) > 8: return False
        return True
    return False

def _is_live_context(user_msg): return _is_live_context_fn(user_msg)
def _is_mixed_context(source, user_msg): return source == "friend"

def ask_ai_streaming(client, provider, history, ui, session_start, speaker="主人", relation="主人", source="owner", out_queue=None, image_path=None):
    if out_queue is None: out_queue = queue.Queue()
    s = state.get_state()

    if source == "owner" or source == "mc":
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
    except Exception: pass

    from brain.llm import (detect_forced_tool, is_chitchat, _is_emotion_event, ask_ai, _ask_ai_inner)
    forced_tool = detect_forced_tool(ui)
    is_emo = _is_emotion_event(ui)
    if is_emo: forced_tool = None
    chitchat = is_chitchat(ui, forced_tool) or is_emo

    if not chitchat:
        try:
            result = ask_ai(client, provider, history, ui, session_start, use_tools=True, speaker=speaker, relation=relation, source=source)
            if result: out_queue.put(result)
        except Exception as e: print(f"[流式-工具] 异常: {e}")
        finally: out_queue.put(None)
        return

    # ★ 同步视觉拦截器
    _VISION_TRIGGER_WORDS = ["看到", "看见", "看不看", "看得见", "面前", "屏幕", "这个是什么"]
    _realtime_vision = None
    if any(w in ui for w in _VISION_TRIGGER_WORDS):
        try:
            from tools.screen_capture import grab_window, get_active_window_title
            import json, os
            from core.constants import SCRIPT_DIR
            
            game_title = "Minecraft"
            try:
                with open(os.path.join(SCRIPT_DIR, "data", "config.json"), "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    game_title = cfg.get("game_window_title", "Minecraft")
            except: pass
            
            print("[同步视觉] 检测到哥哥在问视觉问题，强制截取当前画面...")
            b64 = grab_window(game_title)
            
            if not b64:
                active_title = get_active_window_title()
                _IGNORE = ["Visual Studio Code", "Ollama", "PowerShell", "CMD", "Edge", "Chrome", "微信", "QQ"]
                if active_title and not any(kw in active_title for kw in _IGNORE):
                    b64 = grab_window(active_title)
                    
            if b64:
                out_queue.put("唔……让我看看啊。")
                prompt = "你在玩 Minecraft。请只描述画面正中央（十字准星）看到的那个方块或生物。用最直白的词语说出它是什么（比如：村民、铁锭、红色的花、猪），不需要描述背景或风景。"
                desc = vision_analyze(prompt, b64)
                if desc and not desc.startswith("[视觉失败]"):
                    print(f"[同步视觉] ✅ 分析完成: {desc}")
                    _realtime_vision = desc
            else:
                print("[同步视觉] ❌ 截图失败！可能是游戏窗口被遮挡。")
        except Exception as e:
            print(f"[同步视觉] ❌ 异常: {e}")

    um = {"role": "user", "content": ui}
    rh = history[-4:]
    now = time.localtime()
    wd = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.tm_wday]
    tctx = f"\n\n【当前时间】\n{time.strftime('%Y年%m月%d日 %H:%M', now)}（{wd}）。"
    # mctx = s.mood_mgr.get_context() if s.mood_mgr else ""
    relation_ctx = get_relation_prompt(speaker, relation)
    # profile_ctx = get_profile_context(speaker)
    transition_ctx = get_transition_prompt(speaker, s.last_speaker)
    s.last_speaker = speaker; s.current_speaker = speaker
    refusal_ctx = None
    try: refusal_ctx = evaluate_request(ui)
    except Exception: pass
    persona_ctx = build_persona_prompt(refusal_ctx=refusal_ctx, source=source)

    if source in ("friend", "audience"): mem = []
    else: mem = s.rag.search(ui, top_k=3) if s.rag else []

    sysc = BASE_SYSTEM_PROMPT + tctx + relation_ctx + persona_ctx
    # if profile_ctx: sysc += profile_ctx
    if transition_ctx: sysc += transition_ctx
    if mem: sysc += "\n\n【参考资料】\n" + "\n".join([f"· {m}" for m in mem])
    sysc += _time_gap_context(s)

    # ★ 注入游戏底层实时状态（余光直觉拟人化）
    try:
        from tools.minecraft import MC_CURRENT_STATE
        if MC_CURRENT_STATE and MC_CURRENT_STATE != "哥哥当前处于正常状态。":
            sysc += (
                f"\n\n【你的余光/直觉感知 - 极其重要】\n"
                f"你正坐在哥哥旁边看他玩。你余光瞥到的情况：{MC_CURRENT_STATE}\n"
                "【说话铁律 - 违反将受惩罚】\n"
                "1. **绝对禁止报数据！** 绝对不能像雷达播报一样说“你的坐标是 X=…，血量是 Y，周围有苦力怕”。\n"
                "2. **绝对禁止像客服给建议！** 绝对不能说“请注意安全”、“当前天气为下雨”。\n"
                "3. **你要像真人妹妹一样，用情绪和直觉说话。**\n"
                "【如果你刚好看到危险，你要惊呼】\n"
                "  · 错误：检测到周围有僵尸，请注意防范。\n"
                "  · 正确：哎呀哥，你后面有只绿油油的东西！快跑！\n"
                "【如果哥哥主动问你（比如“我在哪/周围有啥”），你才能顺带着说】\n"
                "  · 正确：我瞄了一眼，你现在在很深的地下哦，旁边有只僵尸，咱们先撤吧？\n"
            )
    except Exception:
        pass

    # ★ 注入实时视觉认知
    try:
        if _realtime_vision:
            sysc += "\n\n【你刚刚看了一眼哥哥的屏幕】\n"
            sysc += f"· {_realtime_vision}\n"
            sysc += "（注意：这是你刚刚一秒内看到的真实画面。如果哥哥问你“能看到吗”，你必须根据这个画面直接回答，自信地告诉他你看到了什么。**绝对禁止说“我看不到”**！）\n"
        else:
            from brain.urge import _load as _load_urge
            _urges = _load_urge()
            _vision_obs = [u["content"] for u in _urges if u.get("source") == "screen"]
            if _vision_obs:
                sysc += "\n\n【你刚才在旁边看到哥哥的电脑屏幕】\n"
                for obs in _vision_obs[-2:]: sysc += f"· {obs}\n"
    except Exception:
        pass

    if _is_empathy:
        sysc += "\n\n【共情模式 - 现在哥哥情绪极度脆弱】\n★★★ 绝对禁止说教、禁止讲大道理 ★★★\n你只有两种回应方式：\n1. 接住情绪+追问细节\n2. 表达心疼和无措"

    sysc += "\n\n【隐私红线】\n无论什么场合，你都不能说出哥哥的具体地点、工作/学习、具体习惯、姓名、年龄。"

    _is_live = _is_live_context(ui)
    if _is_live:
        _prefix = random.choice(_LIVE_PREFIXES); _tail = random.choice(_EMOTION_TAILS)
        sysc += f"\n\n【直播模式】涉及'哥哥'的问题，必须用模板回答：'{_prefix} {_tail}。'"

    if _is_mixed_context(source, ui) and not _is_live:
        sysc += "\n\n【混合场景】涉及哥哥隐私 → '秘密哦~'"

    _throttle_question = _should_throttle_question()
    if _throttle_question:
        sysc += "\n\n【反问节流】这次**禁止**用任何问句。最后一句必须用句号结尾。"

    if not _is_empathy:
        _l0_ctx = _load_l0_context(ui)
        if _l0_ctx and _l0_ctx.get("my_view"): sysc += _build_l0_prompt(_l0_ctx)

    sysc += "\n\n【最高优先级】\n情绪是生气/难过/失落 → 必须冷淡、短句。回复 2~3 句话以内。禁止 markdown。"

    last_self = ""
    for _m in reversed(history):
        if _m.get("role") == "assistant" and _m.get("content"): last_self = _m["content"].strip(); break
    if last_self: sysc += f"\n\n【你上一句说】\"{last_self}\"\n如果当前话题和上一句有关，可以自然接续。"

    msgs = [{"role": "system", "content": sysc}]
    try:
        from brain.episodic import retrieve_few_shot
        few_shots = retrieve_few_shot(ui, top_k=1)
        if few_shots:
            print(f"[Few-shot] 注入 {len(few_shots)} 条历史对话作为语气参考")
            for fs in few_shots:
                msgs.append({"role": "user", "content": fs["user"]})
                msgs.append({"role": "assistant", "content": fs["assistant"]})
    except Exception as e: print(f"[Few-shot] 异常: {e}")

    msgs += rh
    msgs.append(um)
    
    hesitation_triggered = False
    if random.random() < 0.6:  # 60% 概率
        hesitation_triggered = True
    out_queue.put(random.choice(["嗯……", "让我想想啊……", "我看看哦……", "等一下下……"]))

    full_text, buf, t0 = "", "", time.time()
    _first_checked = [False]; _interrupted = [False]; _live_violation = [False]; _extra_violation = [False]; _throttle_yielded = [0]
    stream = None

    try:
        with LLM_LOCK:
            stream = client.chat.completions.create(
                model=provider["model"], messages=msgs, timeout=90, temperature=0.7, max_tokens=400, stream=True,
                extra_body={"keep_alive": "30m", "think": False},
            )
            _first_sent_put = [False]
            for chunk in stream:
                if not chunk.choices: continue
                delta = chunk.choices[0].delta
                piece = delta.content if hasattr(delta, "content") and delta.content else ""
                if not piece: continue
                full_text += piece; buf += piece

                sentences, buf = _split_sentence_chunk(buf)
                for sent in sentences:
                    cleaned = _clean_sentence(sent)
                    if not cleaned or len(cleaned) < 2: continue
                    if cleaned in ["嗯。", "嗯，", "啊。", "哦。", "呃。", "呃，", "唔。", "唔，", "……"]: continue
                    
                    _clean_no_punc = re.sub(r'[^\w\u4e00-\u9fff]', '', cleaned)
                    if len(_clean_no_punc) < 10 and not cleaned.endswith(("。", "！", "？", "!", "?", "~", "～")):
                        buf = cleaned + buf
                        continue

                    if _throttle_question and _is_question_sentence(cleaned): continue
                    if _is_empathy:
                        _CLICHE_EMPATHY = ["成长的一部分", "成长的过程", "沟通上的障碍", "细腻的感受力", "很珍贵", "每个人都有自己的", "时间会证明", "会好起来的", "别太在意", "做自己","慢慢来"]
                        if any(w in cleaned for w in _CLICHE_EMPATHY): continue
                    _CLICHE_REPLIES = ["谢谢哥哥夸奖", "我会继续努力", "谢谢哥哥的认可"]
                    if any(w in cleaned for w in _CLICHE_REPLIES): continue
                    if _is_live and "哥哥" in cleaned:
                        _interrupted[0] = True; _live_violation[0] = True; break
                    try:
                        from brain.output_extra_audit import audit_extra
                        _ok_extra, _reason_extra = audit_extra(cleaned)
                        if not _ok_extra: _interrupted[0] = True; _extra_violation[0] = True; break
                    except Exception: pass
                    if _cold_mode and not _first_checked[0]:
                        _hits = is_soft_output(cleaned)
                        if _hits: _interrupted[0] = True; break
                        _first_checked[0] = True
                    if hesitation_triggered and not _first_sent_put[0]:
                        time.sleep(random.uniform(0.5, 1.2)); _first_sent_put[0] = True
                    out_queue.put(cleaned); _throttle_yielded[0] += 1
                if _interrupted[0]: break

            if not _interrupted[0] and buf.strip():
                cleaned = _clean_sentence(buf.strip())
                if _cold_mode:
                    if not cleaned or len(cleaned) < 2: cleaned = "嗯。"
                if _throttle_question and _is_question_sentence(cleaned): cleaned = ""
                if cleaned and len(cleaned) >= 2 and not _interrupted[0]:
                    out_queue.put(cleaned); _throttle_yielded[0] += 1
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
            _safe = make_safe_reply(); out_queue.put(_safe); out_queue.put(None)
            history.append({"role": "user", "content": ui}); history.append({"role": "assistant", "content": _safe}); save_history(history); return
        if _extra_violation[0]:
            _safe = "这个我就不细说了。"; out_queue.put(_safe); out_queue.put(None)
            history.append({"role": "user", "content": ui}); history.append({"role": "assistant", "content": _safe}); save_history(history); return
        try:
            result = _ask_ai_inner(client, provider, history, ui, session_start, use_tools=True, speaker=speaker, relation=relation, from_mc=False, source=source)
            out_queue.put(result if result else "嗯。")
        except Exception: out_queue.put("嗯。")
        finally: out_queue.put(None)
        return

    if _throttle_question and _throttle_yielded[0] == 0: out_queue.put(random.choice(_THROTTLE_FALLBACKS))
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