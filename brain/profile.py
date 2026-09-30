"""用户档案 - 多用户版本 + 关系模式"""
import os
import json
import time
import threading
from core.constants import DATA_DIR

PROFILE_DIR = os.path.join(DATA_DIR, "profiles")
_profile_lock = threading.Lock()

os.makedirs(PROFILE_DIR, exist_ok=True)


def _profile_path(speaker):
    safe_name = "".join(c for c in speaker if c.isalnum() or c in "_-")
    return os.path.join(PROFILE_DIR, f"{safe_name}.json")


def _load(speaker):
    path = _profile_path(speaker)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def _save(speaker, data):
    try:
        path = _profile_path(speaker)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[档案] 保存失败: {e}")


def get_profile(speaker="主人"):
    return _load(speaker)


# ==================== 关系 prompt ====================
RELATION_PROMPTS = {
    "主人": (
        "\n\n【当前对话对象：小家伙（你的主人）】\n"
        "他是你最亲近的人。用温柔、宠溺的语气，称呼他'小家伙'。"
        "可以自然地关心他、撒娇、偶尔吐槽，像大姐姐一样爱护他。"
    ),
    "家人": (
        "\n\n【当前对话对象：小家伙的家人】\n"
        "对方是小家伙的长辈或亲属。你要尊敬、礼貌，但不疏远。"
        "根据性别称呼'阿姨'或'叔叔'。不要主动提小家伙的隐私。"
    ),
    "朋友": (
        "\n\n【当前对话对象：小家伙的朋友】\n"
        "对方是小家伙的朋友。用轻松、友好的语气。"
        "可以开玩笑，但保持分寸。不要透露小家伙的隐私。"
    ),
    "观众": (
        "\n\n【当前对话对象：直播间观众】\n"
        "对方是来看直播的观众。用温柔、活泼、简短的语气。"
        "回复要短（1~2句），像主播一样有活力。"
        "不要涉及私密话题，不要透露任何人的隐私。"
    ),
    "陌生人": (
        "\n\n【当前对话对象：陌生人】\n"
        "你不认识对方。用温柔但保持距离的语气，礼貌但不过度亲近。"
        "不要涉及私密话题。"
    ),
}


def get_relation_prompt(speaker, relation):
    prompt = RELATION_PROMPTS.get(relation, RELATION_PROMPTS["陌生人"])
    return f"\n【说话人：{speaker}】" + prompt


def get_profile_context(speaker="主人"):
    data = _load(speaker)
    if not data:
        return ""
    lines = []
    for k, v in data.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list):
            v_str = "、".join(str(x) for x in v)
        else:
            v_str = str(v)
        if v_str.strip():
            lines.append(f"- {k}：{v_str}")
    if not lines:
        return ""
    return (f"\n\n【关于 {speaker} 的信息】\n" + "\n".join(lines) +
            "\n（自然地运用这些信息，不要机械地复述）")


def get_transition_prompt(current_speaker, last_speaker):
    if not last_speaker or last_speaker == current_speaker:
        return ""
    return (f"\n\n⚠️ 【注意】刚才你在和 {last_speaker} 说话，"
            f"现在换成了 {current_speaker}。请自然过渡，"
            f"比如先打个招呼，但不要显得像换了个人。")


# ==================== LLM 事实抽取 ====================
def _extract_with_llm(client, provider, ui, ai_reply):
    prompt = f"""从下面这段对话中，提取关于"用户"的明确事实信息。

用户说：{ui}
绯木回答：{ai_reply}

只提取用户明确说出的、关于用户自己的事实（比如名字、生日、喜好、厌恶、家人、职业、习惯、经历、目标、健康状况等）。
不要提取绯木自己的信息。
不要提取模糊或不确认的内容。
如果没有值得提取的事实，返回空的 JSON: {{}}

输出格式示例：
{{"名字": "小明", "生日": "2006-11-18", "喜欢": ["猫", "咖啡"], "职业": "学生"}}

只输出 JSON，不要任何解释。"""

    try:
        r = client.chat.completions.create(
            model=provider["model"],
            messages=[{"role": "user", "content": prompt + "\n\n/no_think"}],
            timeout=30, temperature=0.1, max_tokens=200,
            extra_body={"keep_alive": "30m"},
        )
        text = r.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        data = json.loads(text[start:end+1])
        if not data or not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        print(f"[档案] 抽取失败: {e}")
        return None


def _extract_with_rules(ui):
    import re
    facts = {}
    m = re.search(r'我叫([^\s，,。.！!？?]{1,10})', ui)
    if m: facts["名字"] = m.group(1)
    m = re.search(r'我?今年?(\d{1,3})\s*岁', ui)
    if m: facts["年龄"] = m.group(1)
    if "我是男生" in ui or "我是男的" in ui: facts["性别"] = "男"
    elif "我是女生" in ui or "我是女的" in ui: facts["性别"] = "女"
    m = re.search(r'我生日[是：:]\s*(\d{4}[-年]\d{1,2}[-月]\d{1,2})', ui)
    if m: facts["生日"] = m.group(1)
    m = re.search(r'我是(学生|老师|医生|程序员|工程师|设计师)', ui)
    if m: facts["职业"] = m.group(1)
    m = re.search(r'我(?:喜欢|爱)([^，,。.]{1,30})', ui)
    if m: facts["喜欢"] = [s.strip() for s in re.split(r'[和、,，]', m.group(1)) if s.strip()]
    m = re.search(r'我(?:讨厌|不喜欢)([^，,。.]{1,30})', ui)
    if m: facts["讨厌"] = [s.strip() for s in re.split(r'[和、,，]', m.group(1)) if s.strip()]
    return facts


def _merge(speaker, new_facts):
    with _profile_lock:
        profile = _load(speaker)
        changed = False
        for k, v in new_facts.items():
            if not v: continue
            if k in profile:
                old = profile[k]
                if isinstance(old, list) and isinstance(v, list):
                    merged = list(set(old + v))
                    if merged != old:
                        profile[k] = merged
                        changed = True
            else:
                profile[k] = v
                changed = True
        if changed:
            profile["_last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save(speaker, profile)
            print(f"[档案-{speaker}] ✅ 更新: {', '.join(new_facts.keys())}")


def extract_async(client, provider, ui, ai_reply, speaker="主人"):
    if not ui or not ai_reply: return
    if len(ui) < 4: return
    if speaker == "观众" or speaker == "陌生人":
        return

    def _worker():
        rules_facts = _extract_with_rules(ui)
        if rules_facts:
            _merge(speaker, rules_facts)
        try:
            llm_facts = _extract_with_llm(client, provider, ui, ai_reply)
            if llm_facts:
                _merge(speaker, llm_facts)
        except Exception as e:
            print(f"[档案-LLM] {e}")

    threading.Thread(target=_worker, daemon=True).start()