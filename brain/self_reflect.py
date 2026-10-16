"""自我反思 - 她说完话后，回头感受一下自己刚才说了什么

C 方案：平时静默反思（写日志不打扰），情绪激烈或说了狠话时才说出口。
同步调用 LLM，max_tokens 很小，通常 2~4 秒返回。

改进（2026-10-16）：自语必须有"具体观察 + 情绪 + 下一步"三要素，
避免"我也在想…"这种空话。注入 L0 上下文。
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

    # 元描述过滤
    _meta_patterns = ["想补充", "想问他", "想问她", "想告诉", "应该问",
                      "应该提醒", "决定问", "决定说", "准备说", "准备问"]
    if any(p in text for p in _meta_patterns):
        return None

    # 第三人称过滤
    if "他" in text or "她" in text:
        return None

    # 时间指代过滤
    _time_patterns = [
        "上次", "上周", "上回", "前几天", "昨天",
        "之前你", "之前我", "刚刚你", "刚刚我",
    ]
    if any(p in text for p in _time_patterns):
        return None

    # 感知动作过滤
    _sense_patterns = ["看到", "听到", "脸色", "眼神", "窗外", "阳光",
                       "走过来", "望见", "瞧见", "听见"]
    if any(p in text for p in _sense_patterns):
        return None

    # ★ 空话过滤（新增）
    _empty_patterns = [
        "我也在想", "我也在琢磨", "我也觉得", "我也希望",
        "希望哥哥", "希望你也", "你也要",
    ]
    if any(p in text for p in _empty_patterns):
        return None

    if len(text) > 40:
        text = text[:40]
    return text


def _get_l0_context(own_text):
    """读取和 own_text 相关的 L0 观点（最多 3 条）

    返回字符串，空字符串表示没有
    """
    try:
        from brain.l0_model import list_opinions
        ops = list_opinions()
        if not ops:
            return ""

        text_chars = set(own_text)
        matched = []
        for topic, op in ops.items():
            if not topic:
                continue
            topic_chars = set(topic)
            # 交集 >= 3 字算命中
            if len(topic_chars & text_chars) >= 3:
                view = op.get("my_view", "")[:30]
                matched.append(f"· {topic}：{view}")
        if matched:
            return "\n\n【你最近在想的事】\n" + "\n".join(matched[:3])
    except Exception:
        pass
    return ""


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

    _l0_block = _get_l0_context(own_text)

    prompt = (
        f"【你刚才对哥哥说】\"{own_text}\"\n"
        f"【你当下的情绪】{label}（愉悦 {v:+.2f}，唤醒 {a:.2f}）"
        f"{_l0_block}\n\n"
        f"停一秒，心里冒出来的那一句。\n\n"
        f"【必须包含】\n"
        f"1. 一个**具体的观察或想法**——不要'我也在想'这种空话\n"
        f"2. 你**此刻的情绪**——具体，不是'还好'/'不错'\n"
        f"3. 一个小小的**下一步**——哪怕只是'下次问问'\n\n"
        f"【正例】\n"
        f"· '他说\"跳出传统\"我记住了，有点好奇，下次可以聊这个'\n"
        f"· '刚才我好像说太多了，有点后悔'\n"
        f"· '猫的事我还没想透，明天多查一点'\n"
        f"· '我有点飘了，不该那么快下结论'\n\n"
        f"【反例 - 禁止】\n"
        f"· '我也在想办法创新呢'（空话）\n"
        f"· '希望哥哥也能开心'（没有自己）\n"
        f"· '嗯……'（没内容，宁可只回「…」）\n\n"
        f"【绝对不能】\n"
        f"- 编造没发生的事\n"
        f"- '上次''昨天''之前'这种时间指代\n"
        f"- '他''她'——只用'哥哥'或'你'\n"
        f"- '看到''听到'——你没有眼睛和耳朵\n"
        f"- '想补充''应该问'这种元描述\n"
        f"- 打招呼、反问\n\n"
        f"【长度】40 字以内\n\n"
        f"如果确实没什么想说的，只回一个「…」"
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