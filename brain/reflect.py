"""反思引擎 - 从对话中提取感受并写回人格"""
import os
import json
import time
import threading
import re

from core.constants import DATA_DIR
from brain import persona as P
from brain.l4_audit import find_fabrication

REFLECT_FILE = os.path.join(DATA_DIR, "reflection_log.json")
_lock = threading.Lock()


REFLECT_SYSTEM = """你是绯木，现在独处一会儿，回顾最近和哥哥的对话，做一次真诚的自我反思。

严格以 JSON 输出，不要 markdown 代码块，不要任何多余文字：

{
  "feelings": "我感觉到……（第一人称，1句）",
  "self_discovery": "我发现自己……（第一人称，1句）",
  "drive_changes": {"connection": 0.0, "security": 0.0, "curiosity": 0.0, "expression": 0.0, "validation": 0.0},
  "trait_changes": {"playfulness": 0.0, "clinginess": 0.0, "empathy": 0.0, "sassiness": 0.0, "curiosity": 0.0},
  "preference_changes": {"被夸": 0.0, "聊日常": 0.0, "陪玩": 0.0, "玩游戏": 0.0, "被问感情": 0.0, "被要求撒娇": 0.0, "被要求表白": 0.0, "被要求唱歌": 0.0, "被要求跳舞": 0.0, "重复回答": 0.0, "深夜长聊": 0.0},
  "note_to_self": "我想记住……（1句）"
}

规则：
- feelings 和 self_discovery 各控制在一句话内
- drive_changes / trait_changes 每项 -0.05 ~ +0.05，没变化就填 0
- preference_changes 每项 -0.1 ~ +0.1，没变化就填 0
- 如果反思里你说"我发现我其实不喜欢被问感情"，就把"被问感情"调负
- 如果发现更喜欢某件事，就调正
- 允许为负值。不要一次性把某项打到极端值
- 第一人称，像日记，禁止出现"用户""AI""模型""程序""助手"
- **禁止出现时间或频次词**："今天""昨天""最近""刚才""总是""经常""有时候"——你没有连续的日期体验，也没有统计次数
- 必须输出完整 JSON，不要中途截断
"""


def _format_history(history, max_turns=30):
    recent = history[-max_turns:]
    lines = []
    for m in recent:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"哥哥：{content}")
        elif role == "assistant":
            lines.append(f"我：{content}")
    return "\n".join(lines)


def _parse_json(raw):
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    try:
        return json.loads(raw)
    except Exception:
        pass

    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except Exception:
            pass

    if start >= 0:
        partial = raw[start:]
        partial = re.sub(r'[,，]\s*"[^"]*$', '', partial)
        partial = re.sub(r'[,，]\s*$', '', partial)
        open_braces = partial.count("{") - partial.count("}")
        for _ in range(3):
            trial = partial + "}" * open_braces
            try:
                return json.loads(trial)
            except Exception:
                open_braces += 1

        result = {}
        for key in ["feelings", "self_discovery", "note_to_self"]:
            m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', raw)
            if m:
                result[key] = m.group(1)
        if result:
            print(f"[反思] 用正则兜底提取到 {list(result.keys())}")
            return result

    return None


def _call_llm(client, provider, history):
    convo = _format_history(history)
    if not convo:
        return None
    msgs = [
        {"role": "system", "content": REFLECT_SYSTEM},
        {"role": "user", "content": f"这是最近的对话：\n\n{convo}\n\n请开始反思。"},
    ]
    try:
        r = client.chat.completions.create(
            model=provider["model"],
            messages=msgs,
            timeout=180,
            temperature=0.5,
            max_tokens=1500,
        )
        return r.choices[0].message.content
    except Exception as e:
        print(f"[反思] LLM 调用失败: {e}")
        return None


def _clean_text_field(text):
    """清洗反思的文本字段。命中编造词返回空串

    只清洗文本，不清洗数值——drive/trait/preference 的调整是数，
    不涉及编造，照常应用。
    """
    if not text:
        return ""
    hits = find_fabrication(text)
    if hits:
        print(f"[反思] 编造嫌疑 {hits}，已丢弃字段: {text[:40]}")
        return ""
    return text


def _save_log(entry):
    with _lock:
        try:
            if os.path.exists(REFLECT_FILE):
                with open(REFLECT_FILE, "r", encoding="utf-8") as f:
                    log = json.load(f)
            else:
                log = []
        except Exception:
            log = []
        log.append(entry)
        log = log[-100:]
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(REFLECT_FILE, "w", encoding="utf-8") as f:
                json.dump(log, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[反思] 写日志失败: {e}")


def _do_reflect(client, provider, history, mode, reason):
    raw = _call_llm(client, provider, history)
    if not raw:
        return None
    parsed = _parse_json(raw)
    if not parsed:
        print(f"[反思] 解析失败，原始输出: {raw[:200]}")
        return None

    # 清洗三个文本字段（命中编造词 → 置空，但不清洗数值）
    for _k in ("feelings", "self_discovery", "note_to_self"):
        if parsed.get(_k):
            parsed[_k] = _clean_text_field(parsed[_k])

    # 数值调整照常应用
    P.apply_reflection(parsed, mode=mode)

    entry = {
        "ts": time.time(),
        "mode": mode,
        "reason": reason,
        "feelings": parsed.get("feelings", ""),
        "self_discovery": parsed.get("self_discovery", ""),
        "note_to_self": parsed.get("note_to_self", ""),
    }
    _save_log(entry)

    # 只打印未被清洗的字段
    has_text = any([entry["feelings"], entry["self_discovery"], entry["note_to_self"]])
    if not has_text:
        print(f"\n[反思-{reason}] 文本字段全部命中编造，已丢弃（数值调整照常应用）\n")
        return entry

    if entry["feelings"]:
        print(f"\n[反思-{reason}] {entry['feelings']}")
    if entry["self_discovery"]:
        print(f"[反思-{reason}] {entry['self_discovery']}")
    if entry["note_to_self"]:
        print(f"[反思-{reason}] 记住：{entry['note_to_self']}")
    print()
    return entry


def reflect_sync(client, provider, history, mode=None, reason="manual"):
    if not history:
        print("[反思] 没有对话历史，跳过")
        return None
    m = mode or P.get_mode()
    print(f"[反思] 开始（{reason}，模式={m}）...")
    entry = _do_reflect(client, provider, history, m, reason)
    if entry:
        P.mark_reflected()
    return entry


def reflect_async(client, provider, history, mode=None, reason="turns"):
    def _worker():
        try:
            m = mode or P.get_mode()
            _do_reflect(client, provider, list(history), m, reason)
            P.mark_reflected()
        except Exception as e:
            print(f"[反思] 异步失败: {e}")
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t