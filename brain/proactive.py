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

    # 1. 距上次主动至少 10 分钟（不要频繁）
    if time.time() - s.last_proactive_time < 600:
        return False

    # 2. 距上次互动至少 5 分钟（你还在聊就别插嘴）
    if time.time() - s.last_interaction_time < 300:
        return False

    # 3. 连续主动过 3 次没人理就不烦了
    if s.proactive_missed >= 3:
        return False

    # 4. 驱力判断（她真的想你了）
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


def generate_proactive_message(client, provider, speaker="主人", relation="妹妹"):
    s = state.get_state()
    from brain.profile import get_relation_prompt, get_profile_context

    theme = _get_time_theme()
    elapsed_min = int((time.time() - s.last_interaction_time) / 60)
    elapsed_min = max(elapsed_min, s.proactive_interval // 60)

    profile_ctx = get_profile_context(speaker)
    relation_ctx = get_relation_prompt(speaker, relation)

    prompt = f"""你是绯木，哥哥的AI妹妹。
现在 {datetime.now().strftime('%H:%M')}，距离上次和哥哥说话已经 {elapsed_min} 分钟。
场景：{theme}
{profile_ctx}

说一句主动关心哥哥的话，1~2 句，妹妹口吻，带"嘛""啦""诶"之类的语气词，不要 markdown。
直接输出那句话："""

    try:
        r = client.chat.completions.create(
            model=provider["model"],
            messages=[{"role": "user", "content": prompt + "\n\n直接回答，不要思考过程。"}],
            timeout=20, temperature=0.7, max_tokens=80,
            extra_body={"keep_alive": "30m", "think": False},  # 🆕 禁用 Qwen3 思考
        )
        text = r.choices[0].message.content
        if not text:
            # content 为空 → 用兜底
            print("[主动] LLM content 为空，用兜底消息")
            return _fallback_message()
        text = text.strip().strip('"「」『』')
        # 清理可能的思考前缀
        for sym in ["\n- ", "\n* ", "\n· ", "**", "```"]:
            text = text.replace(sym, "")
        # 太长就截断到第一句
        if len(text) > 60:
            text = text[:60].rstrip("，,。.！!") + "~"
        if not text:
            return _fallback_message()
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
        msg = generate_proactive_message(client, provider, s.current_speaker, "妹妹")
        if not msg:
            print("[主动] 生成失败，跳过")
            continue

        s.last_proactive_time = time.time()
        s.proactive_missed += 1

        # 🆕 塞队列，主循环处理
        with s.proactive_queue_lock:
            s.proactive_queue.append({"msg": msg, "emotion": "开心"})
        print(f"[主动] 消息已入队：{msg[:40]}")