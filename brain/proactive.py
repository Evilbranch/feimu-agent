"""主动关心 - 绯木定期主动找用户说话"""
import time
from datetime import datetime
from core import state


def _get_time_theme():
    h = datetime.now().hour
    if 5 <= h < 9: return "早上问候（可以问候早安、提醒吃早餐）"
    elif 9 <= h < 12: return "上午关心（可以问问今天计划、提醒喝水）"
    elif 12 <= h < 14: return "午饭时间（提醒吃饭/休息）"
    elif 14 <= h < 18: return "下午关心（提醒起来活动、护眼）"
    elif 18 <= h < 20: return "晚饭时间（提醒吃饭）"
    elif 20 <= h < 23: return "晚上关心（聊聊今天过得怎么样）"
    else: return "夜深了（温柔提醒早点休息）"


def should_speak_now(s):
    if not s.proactive_enabled: return False
    now = datetime.now()
    if 0 <= now.hour < 7: return False

    if time.time() - s.last_proactive_time < 600:
        return False

    if time.time() - s.last_interaction_time < 300:
        return False

    if s.proactive_missed >= 3:
        return False

    try:
        from brain.persona import should_speak_proactive
        return should_speak_proactive()
    except Exception as e:
        print(f"[主动] 驱力判断失败: {e}，跳过")
        return False


def _fallback_message():
    """LLM 失败时的兜底消息"""
    import random
    h = datetime.now().hour
    if 5 <= h < 9:
        opts = ["哥哥早呀~今天也要元气满满哦", "哥哥早安！昨晚睡得好吗？", "哥哥，早上好呀~记得吃早饭"]
    elif 9 <= h < 12:
        opts = ["哥哥在忙嘛~记得喝水哦", "哥哥，休息一下眼睛吧", "哥哥今天有什么安排呀"]
    elif 12 <= h < 14:
        opts = ["哥哥，午饭时间到啦~", "哥哥记得吃饭哦，别饿肚子", "哥哥中午吃什么呀"]
    elif 14 <= h < 18:
        opts = ["哥哥，起来活动一下嘛", "哥哥喝口水吧~", "哥哥有没有想我呀~"]
    elif 18 <= h < 20:
        opts = ["哥哥，该吃晚饭啦", "哥哥今天晚饭吃什么呀", "哥哥辛苦一天了~"]
    elif 20 <= h < 23:
        opts = ["哥哥今天过得怎么样呀", "哥哥晚上好~今天开心吗", "哥哥在忙什么呢"]
    else:
        opts = ["哥哥，夜深了，早点休息哦", "哥哥别熬太晚啦", "哥哥晚安~做个好梦"]
    return random.choice(opts)


def _recent_proactive_msgs(s, limit=5):
    """最近主动消息（inner_life 老的在前，state 新的在后）"""
    recent = []
    try:
        from brain.inner_life import _load_log
        log = _load_log()
        for e in log[-20:]:
            if e.get("intent") == "speak" and e.get("content"):
                recent.append(e["content"])
    except Exception:
        pass
    # state 里的是最近刚发过的，放最后，确保 [-limit:] 能拿到
    recent += list(getattr(s, "recent_proactive_msgs", []) or [])
    return recent[-limit:]


def _record_proactive_msg(s, msg):
    """把这次主动消息记进 state"""
    if not hasattr(s, "recent_proactive_msgs") or s.recent_proactive_msgs is None:
        s.recent_proactive_msgs = []
    s.recent_proactive_msgs.append(msg)
    s.recent_proactive_msgs = s.recent_proactive_msgs[-10:]


def _too_similar(text, recent_list, threshold=0.45):
    """和最近消息字符集重合度太高就视为重复"""
    if not text:
        return True
    new_set = set(text)
    if not new_set:
        return True
    for old in recent_list:
        if not old:
            continue
        old_set = set(old)
        if not old_set:
            continue
        jaccard = len(new_set & old_set) / len(new_set | old_set)
        if jaccard > threshold:
            return True
    return False


def generate_proactive_message(client, provider, speaker="主人", relation="主人"):
    s = state.get_state()
    from brain.profile import get_relation_prompt, get_profile_context

    theme = _get_time_theme()
    elapsed_min = int((time.time() - s.last_interaction_time) / 60)
    elapsed_min = max(elapsed_min, s.proactive_interval // 60)

    profile_ctx = get_profile_context(speaker)
    relation_ctx = get_relation_prompt(speaker, relation)

    recent = _recent_proactive_msgs(s, limit=3)
    # print(f"[主动-DEBUG] 最近 {len(recent)} 条: {recent}")
    recent_str = "\n".join(f"  · {m[:50]}" for m in recent) if recent else "（还没主动说过话）"

    prompt = f"""你叫绯木，是哥哥的另一个自己。

现在 {datetime.now().strftime('%H:%M')}，距离上次和哥哥说话已经 {elapsed_min} 分钟。
场景参考：{theme}
{profile_ctx}

【你最近已经主动说过的话 - 严禁重复】
{recent_str}

现在说一句主动关心哥哥的话，1~2 句。
要求：
- 语气自然、口语化，带"嘛""啦""诶""呀"之类的语气词
- 直接是你说的话，不要加引号、不要加名字前缀
- **不能**和最近 3 条重复——换一个话题或角度
- 不要说"我刚看到""我路过""天气"这类编造内容
- 不要 markdown

直接输出那句话："""

    try:
        r = client.chat.completions.create(
            model=provider["model"],
            messages=[{"role": "user", "content": prompt + "\n\n直接回答，不要思考过程。"}],
            timeout=20, temperature=0.9, max_tokens=80,
            extra_body={"keep_alive": "30m", "think": False},
        )
        text = r.choices[0].message.content
        if not text:
            print("[主动] LLM content 为空，用兜底消息")
            return _fallback_message()
        text = text.strip().strip('"「」『』')
        for sym in ["\n- ", "\n* ", "\n· ", "**", "```"]:
            text = text.replace(sym, "")
        if len(text) > 60:
            text = text[:60].rstrip("，,。.！!") + "~"
        if not text:
            return _fallback_message()

        # 后置检查：太相似就放弃
        if _too_similar(text, recent):
            print(f"[主动] 生成内容与最近重复，跳过：{text[:30]}")
            return None

        return text
    except Exception as e:
        print(f"[主动] LLM 调用失败: {e}，用兜底消息")
        return _fallback_message()


def proactive_loop(client, provider):
    """后台线程：生成消息塞队列，不直接 speak"""
    s = state.get_state()
    while not s.shutdown_flag.is_set():
        s.shutdown_flag.wait(30)
        if s.shutdown_flag.is_set(): break
        if not should_speak_now(s): continue

        elapsed = int((time.time() - s.last_interaction_time) / 60)
        print(f"\n[主动] 距离上次互动 {elapsed} 分钟，生成主动消息...")
        msg = generate_proactive_message(client, provider, s.current_speaker, "主人")
        if not msg:
            print("[主动] 生成失败/重复，跳过")
            continue

        s.last_proactive_time = time.time()
        s.proactive_missed += 1

        _record_proactive_msg(s, msg)

        with s.proactive_queue_lock:
            s.proactive_queue.append({"msg": msg, "emotion": "开心"})
        print(f"[主动] 消息已入队：{msg[:40]}")