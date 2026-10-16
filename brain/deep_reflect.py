"""L0 深度反思 - 每周从经历中抽象"我是谁"

设计：
- 每周一次（距上次 > 7 天）
- 输入：本周 L2 + L0 所有观点 + 当前 self_identity
- 输出：候选 who_i_am.statement（可更新）
- 审计后写入，保存历史版本

风险控制：
- statement 必须保留核心："我是绯木，是哥哥的另一个我"
- core_values 只能加不能删
- boundaries 不改
- 无新认识 → 不更新
"""
import os
import json
import time

from datetime import datetime
from core.constants import DATA_DIR
from brain import l0_model


DEEP_REFLECT_LOG = os.path.join(DATA_DIR, "l0", "deep_reflect_log.json")
DEEP_REFLECT_STATE = os.path.join(DATA_DIR, "l0", "deep_reflect_last.txt")
INTERVAL_DAYS = 7


def _load_last_ts():
    if not os.path.exists(DEEP_REFLECT_STATE):
        return 0
    try:
        with open(DEEP_REFLECT_STATE, "r") as f:
            return float(f.read().strip())
    except Exception:
        return 0


def _save_last_ts(ts):
    try:
        os.makedirs(os.path.dirname(DEEP_REFLECT_STATE), exist_ok=True)
        with open(DEEP_REFLECT_STATE, "w") as f:
            f.write(str(ts))
    except Exception:
        pass


def _append_log(entry):
    try:
        os.makedirs(os.path.dirname(DEEP_REFLECT_LOG), exist_ok=True)
        logs = []
        if os.path.exists(DEEP_REFLECT_LOG):
            with open(DEEP_REFLECT_LOG, "r", encoding="utf-8") as f:
                logs = json.load(f)
        logs.append(entry)
        logs = logs[-30:]
        with open(DEEP_REFLECT_LOG, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_week_episodes(days=7):
    """读最近 N 天的 L2 情景"""
    try:
        from brain.episodic import retrieve_episodes
        eps = retrieve_episodes(query=None, top_k=30, days_back=days,
                                 min_importance=0.4)
        return eps or []
    except Exception as e:
        print(f"[深反思] L2 读取失败: {e}")
        return []


def _format_opinions():
    """把当前 L0 观点格式化成文本"""
    ops = l0_model.list_opinions()
    if not ops:
        return "（暂无观点）"
    lines = []
    for topic, op in list(ops.items())[:15]:
        view = op.get("my_view", "")[:50]
        conf = op.get("confidence", 0.5)
        lines.append(f"· [{topic}] {view}（conf={conf:.2f}）")
    return "\n".join(lines)


def _format_episodes(eps):
    """把 L2 格式化成文本"""
    if not eps:
        return "（本周没有重要事件）"
    lines = []
    for e in eps[:20]:
        day = e.get("day", "")
        content = e.get("content", "")[:60]
        etype = e.get("event_type", "")
        lines.append(f"· [{day}][{etype}] {content}")
    return "\n".join(lines)


_DEEP_REFLECT_SYSTEM = """你是绯木。现在是你每周一次的深度反思时间。

你会看到：
1. 你当前对自己的认知（who_i_am）
2. 你最近的观点（opinions）
3. 本周你经历过的事（episodes）

请回顾：这一周里，你对自己有没有**新的认识**？

【重要原则】
- 不是总结"本周做了什么"，而是"我对自己的理解有没有变化"
- 如果没有新认识，直接返回 {"updated": false}
- 如果有，给出**新的 who_i_am.statement**（1~2 句）

【who_i_am.statement 规则】
- 必须保留核心："我是绯木，是哥哥的另一个我"
- 可以加："我最近对 X 有了新的理解" / "我发现自己是 Y"
- 不能删核心
- 不能编造没发生的事
- 长度 ≤ 100 字

【core_values 变更规则】
- 只能**新增**，不能删
- 每次最多新增 1 个
- 已有值：[好奇, 成长, 陪伴, 不假装, 不编造]

【输出格式】
如果有更新：
{
  "updated": true,
  "new_statement": "我是绯木……",
  "new_value": "（可选，要新增的价值观，无则留空）",
  "reason": "为什么这样更新"
}

如果无更新：
{"updated": false}
"""


def _call_llm(client, provider, prompt):
    try:
        r = client.chat.completions.create(
            model=provider["model"],
            messages=[
                {"role": "system", "content": _DEEP_REFLECT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            timeout=120,
            temperature=0.5,
            max_tokens=400,
            extra_body={"keep_alive": "30m", "think": False},
        )
        text = (r.choices[0].message.content or "").strip()
        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        return json.loads(text[start:end + 1])
    except Exception as e:
        print(f"[深反思] LLM 失败: {e}")
        return None


def _audit_update(old_statement, result):
    """审计 LLM 返回的更新

    返回 (ok, cleaned) 或 (False, reason)
    """
    if not result.get("updated"):
        return False, "no_update"

    new = (result.get("new_statement") or "").strip()
    if not new:
        return False, "empty_statement"
    if len(new) > 100:
        return False, "too_long"
    if "绯木" not in new or "哥哥" not in new:
        return False, "missing_core"

    # 检查编造词
    _FAB = ["看到", "听到", "闻到", "去过", "上次", "小时候"]
    for w in _FAB:
        if w in new:
            return False, f"fabrication:{w}"

    # 检查 new_value
    new_value = (result.get("new_value") or "").strip()
    if new_value and len(new_value) > 10:
        new_value = ""

    return True, {
        "statement": new,
        "new_value": new_value,
        "reason": result.get("reason", ""),
    }


def deep_reflect_once(client, provider, force=False):
    """跑一次深度反思"""
    last = _load_last_ts()
    now = time.time()
    if not force and (now - last) < INTERVAL_DAYS * 86400:
        remain = int((INTERVAL_DAYS * 86400 - (now - last)) / 86400)
        print(f"[深反思] 距上次 {remain} 天，跳过")
        return

    m = l0_model.load_self_model()
    old_statement = m["self_identity"]["who_i_am"]["statement"]
    old_values = m["self_identity"]["core_values"]

    eps = _load_week_episodes(days=7)

    prompt = (
        f"【当前的 who_i_am】\n{old_statement}\n\n"
        f"【当前 core_values】\n{', '.join(old_values)}\n\n"
        f"【我最近的观点】\n{_format_opinions()}\n\n"
        f"【本周经历】\n{_format_episodes(eps)}\n\n"
        f"请回顾：我对自己有没有新的认识？"
    )

    print(f"[深反思] 开始分析（L2: {len(eps)} 条）...")
    result = _call_llm(client, provider, prompt)
    if not result:
        print("[深反思] LLM 无返回")
        return

    ok, cleaned = _audit_update(old_statement, result)
    if not ok:
        print(f"[深反思] 审计未通过: {cleaned}")
        _save_last_ts(now)
        _append_log({
            "ts": now,
            "updated": False,
            "reason": str(cleaned),
        })
        return

    # 写回
    with l0_model._lock:
        mm = l0_model.load_self_model()
        mm["self_identity"]["who_i_am"]["statement"] = cleaned["statement"]
        mm["self_identity"]["who_i_am"]["last_updated"] = l0_model._now_iso()

        if cleaned["new_value"] and cleaned["new_value"] not in old_values:
            mm["self_identity"]["core_values"].append(cleaned["new_value"])

        mm["meta"]["deep_reflect_count"] = mm["meta"].get("deep_reflect_count", 0) + 1
        l0_model.save_self_model(mm, snapshot=True)

    print(f"[深反思] ✅ 更新 who_i_am")
    print(f"  旧: {old_statement[:40]}")
    print(f"  新: {cleaned['statement'][:40]}")
    if cleaned["new_value"]:
        print(f"  新增价值观: {cleaned['new_value']}")

    _save_last_ts(now)
    _append_log({
        "ts": now,
        "updated": True,
        "old": old_statement,
        "new": cleaned["statement"],
        "new_value": cleaned["new_value"],
        "reason": cleaned["reason"],
    })