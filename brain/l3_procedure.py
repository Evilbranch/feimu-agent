"""L3 程序记忆 - 习惯与技能养成"""
import os
import json
import time
import uuid
from core.constants import DATA_DIR

L3_FILE = os.path.join(DATA_DIR, "l3_procedures.json")
MAX_PROCEDURES = 50

def _load():
    if not os.path.exists(L3_FILE): return []
    try:
        with open(L3_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return []

def _save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(L3_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def add_procedure(trigger, strategy, score=1.0):
    if not trigger or not strategy: return
    data = _load()
    for p in data:
        if p["trigger"] == trigger and p["strategy"] == strategy:
            p["score"] += score; p["last_used"] = time.time(); _save(data)
            print(f"[L3] 强化程序: {trigger} -> {strategy} (score={p['score']:.1f})"); return
    data.append({"id": f"proc_{int(time.time())}_{uuid.uuid4().hex[:4]}", "trigger": trigger, "strategy": strategy, "score": score, "created_at": time.time(), "last_used": time.time()})
    data.sort(key=lambda x: -x["score"]); data = data[:MAX_PROCEDURES]; _save(data)
    print(f"[L3] 新增程序: {trigger} -> {strategy}")

def get_relevant_procedures(context, top_k=2, min_score=1.5):
    data = _load()
    if not data: return ""
    matched = []
    for p in data:
        if p["score"] < min_score: continue
        trigger = p.get("trigger", "")
        trigger_words = [trigger[i:i+2] for i in range(len(trigger)-1)]
        if any(w in context for w in trigger_words) or ("难过" in trigger and any(w in context for w in ["难过", "伤心", "心情不好", "抑郁", "低落"])):
            matched.append(p)
    if not matched: return ""
    matched.sort(key=lambda x: -x["score"]); top = matched[:top_k]
    lines = [f"- 当「{p['trigger']}」时，你可以：{p['strategy']}" for p in top]
    return "\n\n【根据过去的经验，你学到的最好做法】\n" + "\n".join(lines)