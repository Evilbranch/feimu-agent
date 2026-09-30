"""自我反思 - 她说完话后，回头感受一下自己刚才说了什么

C 方案：平时静默反思（写日志不打扰），情绪激烈或说了狠话时才说出口。
同步调用 LLM，max_tokens 很小，通常 2~4 秒返回。
"""
import os
import json
import time
import random

from core.constants import SCRIPT_DIR

REFLECT_LOG_PATH = os.path.join(SCRIPT_DIR, "data", "self_reflections.json")
MAX_LOG = 200


def _should_reflect(own_text):
    """根据情绪和内容决定是否触发反思。返回概率 [0, 1]"""
    v = 0.0
    a = 0.5
    try:
        from brain.persona import get_current_state
        st = get_current_state()
        emo = st.get("emotion", {}) or {}
        v = float(emo.get("valence", 0.0) or 0.0)
        a = float(emo.get("arousal", 0.5) or 0.5)
    except Exception:
        pass

    if abs(v) > 0.5 or a > 0.8:
        return 0.75
    if any(w in own_text for w in ["烦", "别", "不想", "算了", "随便", "懒得"]):
        return 0.6
    return 0.25


def _load_logs():
    if not os.path.exists(REFLECT_LOG_PATH):
        return []
    try:
        with open(REFLECT_LOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _append_log(entry):
    try:
        logs = _load_logs()
        logs.append(entry)
        logs = logs[-MAX_LOG:]
        os.makedirs(os.path.dirname(REFLECT_LOG_PATH), exist_ok=True)
        with open(REFLECT_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _clean_reflection(content):
    """清洗 LLM 返回的反思文本，None 表示没有要说的话"""
    if not content:
        return None
    text = content.strip()
    for ch in ['"', "'", "「", "」", "《", "》"]:
        text = text.strip(ch)
    text = text.strip()
    if not text:
        return None
    if text in ("…", "...", "。。。", "。", "嗯", "算了", "没了", "没什么"):
        return None

    # 元描述过滤：这些不是真自语，是旁白
    _meta_patterns = ["想补充", "想问他", "想问她", "想告诉", "应该问",
                      "应该提醒", "决定问", "决定说", "准备说", "准备问"]
    if any(p in text for p in _meta_patterns):
        return None

    # 第三人称过滤：她只用"哥哥"或"你"，不用"他""她"
    if "他" in text or "她" in text:
        return None

    # 时间指代过滤：编造记忆的重灾区
    _time_patterns = [
        "上次", "上周", "上回", "前几天", "昨天",
        "之前你", "之前我", "刚刚你", "刚刚我",
    ]
    if any(p in text for p in _time_patterns):
        return None

    # 感知动作过滤：她没有眼睛和耳朵
    _sense_patterns = ["看到", "听到", "脸色", "眼神", "窗外", "阳光",
                       "走过来", "望见", "瞧见", "听见"]
    if any(p in text for p in _sense_patterns):
        return None

    if len(text) > 40:
        text = text[:40]
    return text


def reflect_on_own_speech(client, provider, own_text, source="owner"):
    """她说完话后回头反省。返回要说的自语文本，或 None"""
    if not own_text or len(own_text) < 4:
        return None

    p = _should_reflect(own_text)
    if random.random() > p:
        return None

    v = 0.0
    a = 0.5
    label = "平静"
    try:
        from brain.persona import get_current_state
        st = get_current_state()
        emo = st.get("emotion", {}) or {}
        v = float(emo.get("valence", 0.0) or 0.0)
        a = float(emo.get("arousal", 0.5) or 0.5)
        label = emo.get("label", "平静") or "平静"
    except Exception:
        pass

    prompt = (
        f"【你刚才对哥哥说】\"{own_text}\"\n"
        f"【你当下的情绪】{label}（愉悦 {v:+.2f}，唤醒 {a:.2f}）\n\n"
        f"停一秒，心里冒出来的那一句。规则：\n"
        f"- 没什么感觉，就只回一个「…」\n"
        f"- 直接用第一人称说出来，30 字以内。像小声嘀咕，不是写报告\n"
        f"- 不能编造：你不知道天气、新闻、哥哥在做什么。只能说你确定的事\n"
        f"- **禁止**说'上次''上周''之前''刚刚''昨天'这种时间指代\n"
        f"- **禁止**说'他''她'——只用'哥哥'或'你'\n"
        f"- **禁止**说'看到''听到''笑''哭'——你没有眼睛和耳朵\n"
        f"- 不要写'想补充''想问他''应该'这种描述自己意图的话\n"
        f"- 不要打招呼，不要反问"
    )

    try:
        r = client.chat.completions.create(
            model=provider["model"],
            messages=[
                {"role": "system", "content": "你正在自言自语，没人听见。"},
                {"role": "user", "content": prompt},
            ],
            timeout=20,
            temperature=0.85,
            max_tokens=60,
            extra_body={"keep_alive": "30m", "think": False},
        )
        content = (r.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[自我反思] LLM 异常: {e}")
        return None

    cleaned = _clean_reflection(content)

    entry = {
        "ts": time.time(),
        "own": own_text,
        "reflect": cleaned,
        "v": round(v, 3),
        "a": round(a, 3),
        "silent": cleaned is None,
    }
    _append_log(entry)

    return cleaned