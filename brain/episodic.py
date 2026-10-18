"""L2 情景记忆 - 绯木的数字自传体记忆"""
import os
import json
import time
import uuid
import threading
from datetime import datetime
from core.constants import DATA_DIR
from brain.llm_lock import LLM_LOCK

EPISODES_DIR = os.path.join(DATA_DIR, "episodes")
_lock = threading.Lock()

EVENT_TYPE_WEIGHTS = {
    "milestone": 1.0, "user_fact": 0.8, "self_emotion": 0.8,
    "user_emotion": 0.7, "interaction": 0.6, "discovery": 0.6, "casual": 0.2,
}

def _month_file(ts=None):
    if ts is None: ts = time.time()
    dt = datetime.fromtimestamp(ts)
    return os.path.join(EPISODES_DIR, f"{dt.strftime('%Y-%m')}.json")

def _load_month(ts=None):
    path = _month_file(ts)
    if not os.path.exists(path): return []
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return []

def _save_month(items, ts=None):
    path = _month_file(ts)
    os.makedirs(EPISODES_DIR, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp): os.remove(tmp)
        except Exception: pass

def _compute_importance(event_type, emotion_intensity, novelty=0.5):
    type_weight = EVENT_TYPE_WEIGHTS.get(event_type, 0.2)
    return min(1.0, max(0.0, 0.4 * emotion_intensity + 0.3 * type_weight + 0.2 * novelty + 0.05))

def record_episode(content, event_type="casual", entities=None, self_emotion=None,
                   self_intent="idle", self_role="responder", channel="local",
                   raw_context="", novelty=0.5, ts=None):
    if not content or not content.strip(): return None
    if ts is None: ts = time.time()
    dt = datetime.fromtimestamp(ts)
    ep_id = f"ep_{dt.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"

    emotion_intensity = 0.0
    if self_emotion and isinstance(self_emotion, dict):
        emotion_intensity = abs(self_emotion.get("valence", 0.0))

    importance = _compute_importance(event_type, emotion_intensity, novelty)

    entry = {
        "id": ep_id, "ts": ts, "day": dt.strftime("%Y-%m-%d"),
        "content": content.strip()[:300], "raw_context": raw_context[:500] if raw_context else "",
        "entities": entities or [], "event_type": event_type,
        "time_of_day": "morning", "channel": channel, "conversation_id": None,
        "self_emotion": self_emotion or {"label": "平静", "valence": 0.2, "arousal": 0.4},
        "self_intent": self_intent, "self_role": self_role, "self_reflection": None,
        "emotion_intensity": round(emotion_intensity, 3), "importance": round(importance, 3),
        "refs": 0, "last_ref": ts, "thread_id": None, "thread_title": None, "related_ids": [],
        "status": "active", "consolidated_at": None, "decay_rate": 0.023,
    }

    with _lock:
        items = _load_month(ts)
        items.append(entry)
        _save_month(items, ts)

    print(f"[L2] 新情景记忆 {ep_id} type={event_type} imp={importance:.2f}")
    return ep_id

def _current_emotion():
    try:
        from brain.persona import get_current_state
        st = get_current_state()
        e = st.get("emotion", {})
        return float(e.get("valence", 0.2)), float(e.get("arousal", 0.4))
    except Exception:
        return 0.2, 0.4

def _emotion_match(cur_v, cur_a, mem_emotion):
    if not mem_emotion or not isinstance(mem_emotion, dict): return 0.5
    mem_v, mem_a = float(mem_emotion.get("valence", 0.0)), float(mem_emotion.get("arousal", 0.4))
    dv, da = abs(cur_v - mem_v) / 2.0, abs(cur_a - mem_a)
    dist = (dv + da) / 2.0
    return max(0.0, 1.0 - dist)

def _char_jaccard(a, b):
    if not a or not b: return 0.0
    sa, sb = set(c for c in a if c.strip()), set(c for c in b if c.strip())
    if not sa or not sb: return 0.0
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0

def _semantic_score(query, m):
    if not query: return 0.0
    q = query.lower().strip()
    haystack = ((m.get("content", "") or "") + " " + (m.get("raw_context", "") or "")).lower()
    if not haystack.strip(): return 0.0
    jac = _char_jaccard(q, haystack)
    if len(q) < 2: return 1.0 if q in haystack else 0.0
    q_bigrams = set(q[i:i+2] for i in range(len(q) - 1))
    h_bigrams = set(haystack[i:i+2] for i in range(len(haystack) - 1))
    if not q_bigrams: return jac
    return round(0.4 * jac + 0.6 * (len(q_bigrams & h_bigrams) / len(q_bigrams)), 4)

def _dedup_results(results, threshold=0.85):
    if not results: return []
    kept = []
    for m in results:
        c = m.get("content", "") or ""
        dup_idx = -1
        for i, k in enumerate(kept):
            jac = _char_jaccard(c, k.get("content", "") or "")
            if jac >= threshold: dup_idx = i; break
            if m.get("day") == k.get("day") and m.get("event_type") == k.get("event_type") and jac >= 0.55:
                dup_idx = i; break
        if dup_idx == -1: kept.append(m)
        else:
            if float(m.get("importance", 0)) > float(kept[dup_idx].get("importance", 0)): kept[dup_idx] = m
    return kept

def retrieve_episodes(query=None, top_k=5, days_back=90, min_importance=0.3, include_archived=False):
    now = time.time()
    cutoff = now - days_back * 86400
    all_items = []

    if not os.path.exists(EPISODES_DIR): return []
    for path in sorted([f for f in os.listdir(EPISODES_DIR) if f.endswith(".json")], reverse=True)[:6]:
        try:
            with open(os.path.join(EPISODES_DIR, path), "r", encoding="utf-8") as f:
                all_items.extend([m for m in json.load(f) if m.get("ts", 0) > cutoff])
        except Exception: continue

    all_items = [m for m in all_items if m.get("importance", 0) >= min_importance and (include_archived or m.get("status") != "archived")]

    cur_v, cur_a = _current_emotion()
    has_query = bool(query and query.strip())
    import math as _math
    import numpy as np

    query_vec = None
    item_vecs = None
    item_norms = None

    # ★ 向量化：使用 RAGMemory 提供的 embedding_function
    if has_query:
        try:
            from core import state
            s = state.get_state()
            if s.rag and s.rag._loaded:
                query_vec = s.rag.get_embedding(query)
                if query_vec is not None:
                    _vec_texts = [m.get("content", "") for m in all_items]
                    ef = s.rag.collections["owner"]._embedding_function
                    item_vecs = ef(_vec_texts)
                    query_vec = np.array(query_vec).reshape(-1)
                    item_vecs = np.array(item_vecs)
                    item_norms = np.linalg.norm(item_vecs, axis=1)
                    query_norm = np.linalg.norm(query_vec)
        except Exception:
            pass

    scored = []
    for i, m in enumerate(all_items):
        age_days = (now - m.get("ts", now)) / 86400
        freshness = max(0.0, 1.0 - age_days / max(1, days_back))
        refs = m.get("refs", 0)
        last_ref = m.get("last_ref", m.get("ts", now))
        ref_decay = max(0.3, 1.0 - (now - last_ref) / (86400 * 60))
        ref_score = min(1.0, _math.sqrt(refs / 20.0)) * ref_decay
        emo_match = _emotion_match(cur_v, cur_a, m.get("self_emotion"))
        emo_boost = 0.4 + emo_match * 0.6
        imp = float(m.get("importance", 0))

        if has_query:
            if query_vec is not None and item_vecs is not None and i < len(item_vecs):
                if item_norms[i] > 1e-6 and query_norm > 1e-6:
                    sem = float(np.dot(query_vec, item_vecs[i]) / (item_norms[i] * query_norm))
                else: sem = 0.0
            else:
                sem = _semantic_score(query, m)
            score = sem * 0.60 + imp * 0.15 + freshness * 0.10 + emo_boost * 0.15
        else:
            score = (freshness * 0.35 + imp * 0.50 + ref_score * 0.15) * emo_boost

        scored.append((score, m))

    scored.sort(key=lambda x: -x[0])
    deduped = _dedup_results([m for _, m in scored], threshold=0.85)

    results = []
    emotion_count = {}
    for m in deduped:
        label = m.get("self_emotion", {}).get("label", "中性")
        if emotion_count.get(label, 0) >= 2: continue
        results.append(m)
        emotion_count[label] = emotion_count.get(label, 0) + 1
        if len(results) >= top_k: break

    if results: _bump_refs([m["id"] for m in results])
    return results

def _bump_refs(ids):
    if not ids: return
    ids_set = set(ids)
    now = time.time()
    with _lock:
        if not os.path.exists(EPISODES_DIR): return
        for fname in os.listdir(EPISODES_DIR):
            if not fname.endswith(".json"): continue
            path = os.path.join(EPISODES_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f: items = json.load(f)
            except Exception: continue

            changed = False
            for m in items:
                last_ref = m.get("last_ref", m.get("ts", now))
                if (now - last_ref) / 86400 > 30 and m.get("refs", 0) > 0:
                    m["refs"] = max(0, int(m.get("refs", 0) * 0.5))
                    m["last_ref"] = now
                    changed = True
                if m.get("id") in ids_set:
                    cur = m.get("refs", 0)
                    if cur < 10: m["refs"] = cur + 1
                    elif cur < 30: m["refs"] = cur + 0.2
                    m["last_ref"] = now
                    changed = True
            if changed:
                try:
                    tmp = path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(items, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, path)
                except Exception: pass

_L2_JUDGE_SYSTEM = """你是绯木的记忆整理助手。判断这轮对话中，哥哥是否透露了"值得长期记住的信息"。
直接输出 JSON：
{
  "should_record": true | false,
  "event_type": "user_fact | user_emotion | user_view | milestone | interaction | casual",
  "content": "一句话总结"
}"""

def judge_should_record(client, provider, user_msg, reply):
    if not user_msg or not client or not provider: return False, "", "", 0.0
    _EMOTION_QUICK = ["难过", "累", "烦", "哭", "失业", "孤独", "开心", "成功", "考上"]
    if len(user_msg) < 6 and not any(k in user_msg for k in _EMOTION_QUICK): return False, "", "", 0.0

    try:
        msgs = [{"role": "system", "content": _L2_JUDGE_SYSTEM}, {"role": "user", "content": f"哥哥说：{user_msg}\n绯木回：{reply[:80]}"}]
        # ★ 加锁防抢占
        with LLM_LOCK:
            r = client.chat.completions.create(
                model=provider["model"], messages=msgs, timeout=30, temperature=0.2, max_tokens=200,
                extra_body={"keep_alive": "30m", "think": False},
            )
        text = (r.choices[0].message.content or "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start: return False, "", "", 0.0

        data = json.loads(text[start:end + 1])
        should = bool(data.get("should_record", False))
        etype = (data.get("event_type") or "casual").strip()
        content = (data.get("content") or "").strip()
        if not should: return False, "", "", 0.0
        importance_hint = {"user_fact": 0.8, "user_view": 0.85, "user_emotion": 0.75, "milestone": 1.0, "interaction": 0.6}.get(etype, 0.3)
        return should, etype, content, importance_hint
    except Exception as e:
        return False, "", "", 0.0

def get_today_episodes():
    return [m for m in _load_month() if m.get("day") == time.strftime("%Y-%m-%d")]

def mark_consolidated(ids):
    with _lock:
        items = _load_month()
        now = time.time()
        for m in items:
            if m.get("id") in ids:
                m["status"] = "consolidated"
                m["consolidated_at"] = now
        _save_month(items)

def format_episodes_for_prompt(episodes, max_items=3):
    if not episodes: return ""
    lines = ["【我记得这些事】"]
    for e in episodes[:max_items]:
        self_emo = e.get("self_emotion", {}).get("label", "")
        lines.append(f"· {e.get('day', '')}：{e.get('content', '')}")
        if self_emo and self_emo != "平静": lines.append(f"  （我当时{self_emo}）")
    return "\n".join(lines)

def retrieve_few_shot(query, top_k=2):
    """检索高质量的历史对话，作为 Few-shot 示例注入 Prompt。"""
    if not query or len(query) < 2:
        return []

    now = time.time()
    cutoff = now - 90 * 86400
    all_items = []

    if not os.path.exists(EPISODES_DIR): return []
    for path in sorted([f for f in os.listdir(EPISODES_DIR) if f.endswith(".json")], reverse=True)[:3]:
        try:
            with open(os.path.join(EPISODES_DIR, path), "r", encoding="utf-8") as f:
                items = json.load(f)
            all_items.extend([m for m in items if m.get("ts", 0) > cutoff])
        except Exception:
            continue

    scored = []
    for m in all_items:
        raw = m.get("raw_context", "")
        if not raw or "我：" not in raw:
            continue
        
        parts = raw.split("我：", 1)
        if len(parts) != 2: continue
            
        user_msg = parts[0].replace("哥哥：", "").replace("主人：", "").strip()
        assistant_msg = parts[1].strip()
        
        if len(assistant_msg) < 5 or assistant_msg in ["嗯。", "啊。", "哦。", "嗯，", "唔……"]:
            continue
            
        sem = _semantic_score(query, m)
        if sem < 0.25: continue
            
        score = sem * 0.7 + float(m.get("importance", 0.5)) * 0.3
        scored.append((score, {"user": user_msg, "assistant": assistant_msg}))

    scored.sort(key=lambda x: -x[0])
    
    results = []
    seen_users = set()
    for _, pair in scored:
        if pair["user"] in seen_users: continue
        results.append(pair)
        seen_users.add(pair["user"])
        if len(results) >= top_k: break
    return results