"""L0 核心自我数据层：读写、版本、回滚"""
import os
import re
import json
import time
import uuid
import threading
from datetime import datetime
from difflib import SequenceMatcher

from core.constants import DATA_DIR

L0_DIR = os.path.join(DATA_DIR, "l0")
SELF_MODEL_FILE = os.path.join(L0_DIR, "self_model.json")
LEARNING_TASKS_FILE = os.path.join(L0_DIR, "learning_tasks.json")
HISTORY_DIR = os.path.join(L0_DIR, "self_model_history")

_lock = threading.Lock()

MAX_ITERATIONS = 10
SIMILARITY_THRESHOLD = 0.85
SNAPSHOT_KEEP = 10
TASK_EXPIRE_DAYS = 7

# ★ 隐私过滤：拒绝含这些词的 topic
_BAD_TOPIC_WORDS = (
    "哥哥", "哥哥的", "关于哥哥",
    "关于我", "我是什么样", "我的性格", "我的外貌",
)


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _ensure_dir():
    for p in (L0_DIR, HISTORY_DIR):
        if os.path.isfile(p):
            try:
                os.remove(p)
            except Exception:
                pass
    os.makedirs(L0_DIR, exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)


def _default_self_model():
    return {
        "version": _now_iso(),
        "self_identity": {
            "who_i_am": {
                "statement": "我是绯木，是哥哥的另一个我",
                "confidence": 0.9,
                "evidence_ids": [],
                "last_updated": _now_iso(),
            },
            "core_values": ["好奇", "成长", "陪伴", "不假装", "不编造"],
            "boundaries": ["不说你去死", "不假装没事", "不编造经历"],
        },
        "opinions": {},
        "open_questions": [],
        "learning_tasks": [],
        "meta": {
            "total_iterations": 0,
            "last_explore": None,
            "created_at": _now_iso(),
        },
    }


def _default_learning_tasks():
    return {
        "tasks": [],
        "meta": {"total_created": 0, "total_completed": 0},
    }


def _load_json(path, default_fn):
    if not os.path.exists(path):
        return default_fn()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[L0] 读取失败 {path}: {e}")
        return default_fn()


def _save_json(path, data):
    _ensure_dir()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _similarity(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# ══════════════════════════════════════════════════════
# topic 清洗 + 语义去重
# ══════════════════════════════════════════════════════
_TOPIC_PREFIXES = [
    "你觉得", "你认为", "我想知道", "请问",
    "我想问", "想问一下", "告诉我", "请教一下",
    "你重新想一想", "重新想一想", "重新想一下",
    "你重新想", "重新想", "再想一下", "再想想",
    "你再说一遍", "再说一遍", "重新说",
    "重新回答", "重新答", "重答", "重说", "重讲",
    "你知道", "你知道吗", "你知不知道", "你可知道",
]

_TOPIC_FILLERS = [
    "如何", "怎么", "怎样", "什么", "哪些", "为什么",
    "可以", "应该", "需要", "是否", "有没有",
    "该", "能", "会", "要", "想",
]

_TOPIC_TAIL_CHARS = "？?！!。.，,、；;：: \t\n\"'「」『』"

_TOPIC_QUESTION_TAILS = [
    "是什么样的", "是怎么样的", "应该是怎么样的", "应该是什么样的",
    "需要具备哪些特征", "需要哪些特征", "具备哪些特征", "有哪些特征",
    "能否与真人主播竞争", "能否与真人竞争", "是否能与真人竞争",
    "能否在情感连接和真实性方面与真人主播竞争",
    "能否", "是否能", "是否",
    "会怎么样", "会是怎样", "是怎样的",
    "是什么", "有哪些", "有什么", "为什么", "怎么做", "怎么", "如何",
]


def clean_topic(topic):
    if not topic:
        return ""
    s = (topic or "").strip()
    s = s.rstrip(_TOPIC_TAIL_CHARS)
    for p in _TOPIC_PREFIXES:
        if s.startswith(p):
            s = s[len(p):].strip()
            s = s.rstrip(_TOPIC_TAIL_CHARS)
            break
    for tail in _TOPIC_QUESTION_TAILS:
        if s.endswith(tail):
            s = s[: -len(tail)].strip()
            s = s.rstrip(_TOPIC_TAIL_CHARS)
            break
    return s


def _topic_core(topic):
    s = clean_topic(topic)
    for f in _TOPIC_FILLERS:
        s = s.replace(f, "")
    return s.strip("的了呢嘛呀吧啊")


def _topic_core_chars(topic):
    core = _topic_core(topic)
    if not core:
        return set()
    _STOP = set("的了呢嘛呀吧啊")
    return set(c for c in core if c not in _STOP)


def _topic_keywords(topic):
    return _topic_core_chars(topic)


def _find_similar_topic(new_topic, opinions):
    if not new_topic or not opinions:
        return None, 0.0
    new_chars = _topic_core_chars(new_topic)
    if not new_chars:
        if new_topic in opinions:
            return new_topic, 1.0
        return None, 0.0
    best = (None, 0.0)
    for existing in opinions.keys():
        if not existing:
            continue
        if existing == new_topic:
            return existing, 1.0
        e_chars = _topic_core_chars(existing)
        if not e_chars:
            continue
        inter = len(new_chars & e_chars)
        union = len(new_chars | e_chars)
        score = inter / union if union > 0 else 0.0
        if score > best[1]:
            best = (existing, score)
    if best[1] >= 0.7:
        return best
    return None, best[1]


# ══════════════════════════════════════════════════════
# self_model.json
# ══════════════════════════════════════════════════════
def load_self_model():
    return _load_json(SELF_MODEL_FILE, _default_self_model)


def save_self_model(model, snapshot=True):
    if snapshot:
        _take_snapshot(model)
    model["version"] = _now_iso()
    _save_json(SELF_MODEL_FILE, model)


def _take_snapshot(model):
    _ensure_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    path = os.path.join(HISTORY_DIR, f"self_model_{ts}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(model, f, ensure_ascii=False, indent=2)
        _prune_snapshots()
    except Exception as e:
        print(f"[L0] 快照写入失败: {e}")


def _prune_snapshots(keep=SNAPSHOT_KEEP):
    try:
        files = sorted([f for f in os.listdir(HISTORY_DIR) if f.endswith(".json")])
        for f in files[:-keep]:
            try:
                os.remove(os.path.join(HISTORY_DIR, f))
            except Exception:
                pass
    except Exception:
        pass


def get_opinion(topic):
    if not topic:
        return None
    m = load_self_model()
    return m.get("opinions", {}).get(topic)


def list_opinions():
    m = load_self_model()
    return m.get("opinions", {})


def upsert_opinion(topic, new_view, confidence, evidence_ids=None,
                    source="unknown", force=False):
    _ALLOWED_SOURCES = (
        "owner", "internalization", "feedback", "test", "unknown", "seed",
    )
    if source not in _ALLOWED_SOURCES:
        print(f"[L0] source 白名单拒绝: {source}")
        return "rejected", None

    if not topic or not new_view:
        return "rejected", None
    evidence_ids = evidence_ids or []

    topic = clean_topic(topic)
    if not topic:
        return "rejected", None

    # ★ 拒绝"关于哥哥/关于我"的观点
    if any(w in topic for w in _BAD_TOPIC_WORDS):
        print(f"[L0] 拒绝'关于哥哥/关于我'的观点: {topic[:30]}")
        return "rejected", None

    with _lock:
        m = load_self_model()
        opinions = m.setdefault("opinions", {})
        now = _now_iso()

        matched, jac = _find_similar_topic(topic, opinions)
        if matched and matched != topic:
            print(f"[L0] topic 合并: '{topic}' → '{matched}' (jac={jac:.2f})")
            topic = matched

        existing = opinions.get(topic)

        if existing is None:
            opinions[topic] = {
                "topic": topic,
                "my_view": new_view.strip(),
                "confidence": round(float(confidence), 3),
                "evidence_ids": list(evidence_ids),
                "history": [{
                    "version": 1,
                    "view": new_view.strip(),
                    "confidence": round(float(confidence), 3),
                    "ts": now,
                    "source": source,
                }],
                "iteration_count": 1,
                "last_searched": now,
                "created_at": now,
                "status": "active",
            }
            save_self_model(m, snapshot=True)
            print(f"[L0] 新建观点: {topic}")
            return "created", opinions[topic]

        old_view = existing.get("my_view", "")
        sim = _similarity(old_view, new_view)

        if sim >= SIMILARITY_THRESHOLD:
            old_conf = float(existing.get("confidence", 0.5))
            new_conf = float(confidence)
            merged = round(old_conf * 0.7 + new_conf * 0.3, 3)
            existing["confidence"] = merged
            existing["evidence_ids"] = list(
                set(existing.get("evidence_ids", []) + evidence_ids)
            )
            existing["last_searched"] = now
            save_self_model(m, snapshot=False)
            print(f"[L0] 合并观点: {topic} conf={merged}")
            return "merged", existing

        iter_count = int(existing.get("iteration_count", 1))
        if iter_count >= MAX_ITERATIONS and not force:
            existing.setdefault("history", []).append({
                "version": iter_count + 1,
                "view": new_view.strip(),
                "confidence": round(float(confidence), 3),
                "ts": now,
                "source": source,
                "note": "已达迭代上限，仅存档",
            })
            existing["last_searched"] = now
            existing["evidence_ids"] = list(
                set(existing.get("evidence_ids", []) + evidence_ids)
            )
            save_self_model(m, snapshot=False)
            print(f"[L0] 观点超限存档: {topic}")
            return "archived", existing

        existing["my_view"] = new_view.strip()
        if force:
            existing["confidence"] = round(
                float(existing.get("confidence", 0.5)), 3
            )
        else:
            existing["confidence"] = round(float(confidence), 3)
        existing["evidence_ids"] = list(
            set(existing.get("evidence_ids", []) + evidence_ids)
        )
        existing["iteration_count"] = iter_count + 1
        existing["last_searched"] = now
        existing.setdefault("history", []).append({
            "version": iter_count + 1,
            "view": new_view.strip(),
            "confidence": round(float(confidence), 3),
            "ts": now,
            "source": source,
        })
        save_self_model(m, snapshot=True)
        print(f"[L0] 更新观点: {topic} v{iter_count + 1}")
        return "updated", existing


def get_history(topic):
    op = get_opinion(topic)
    if not op:
        return []
    return op.get("history", [])


def rollback(topic, version):
    with _lock:
        m = load_self_model()
        op = m.get("opinions", {}).get(topic)
        if not op:
            return False
        for h in op.get("history", []):
            if int(h.get("version", 0)) == int(version):
                op["my_view"] = h["view"]
                op["confidence"] = h.get("confidence", 0.5)
                op["iteration_count"] = int(version)
                op["last_searched"] = _now_iso()
                save_self_model(m, snapshot=True)
                print(f"[L0] 回滚 {topic} → v{version}")
                return True
        return False


# ══════════════════════════════════════════════════════
# bump confidence
# ══════════════════════════════════════════════════════
CONF_MIN = 0.1
CONF_MAX = 0.95
CONF_LOW_THRESHOLD = 0.3


def bump_confidence(topic, delta, reason=""):
    if not topic:
        return False, 0.0
    with _lock:
        m = load_self_model()
        opinions = m.get("opinions", {})
        op = opinions.get(topic)
        if not op:
            return False, 0.0

        old_conf = float(op.get("confidence", 0.5))
        new_conf = max(CONF_MIN, min(CONF_MAX, old_conf + float(delta)))
        op["confidence"] = round(new_conf, 3)
        op["last_updated"] = _now_iso()

        op.setdefault("history", []).append({
            "version": int(op.get("iteration_count", 1)),
            "view": op.get("my_view", ""),
            "confidence": round(new_conf, 3),
            "ts": _now_iso(),
            "source": "feedback",
            "note": f"confidence {old_conf:.2f} -> {new_conf:.2f} ({reason})",
        })

        if new_conf < CONF_LOW_THRESHOLD:
            existing_q = [q.get("question", "") for q in m.get("open_questions", [])]
            q_text = (f"我对「{topic}」的理解 confidence 很低"
                      f"（{new_conf:.2f}），需要重新确认")
            if q_text not in existing_q:
                m.setdefault("open_questions", []).append({
                    "id": f"q_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}",
                    "question": q_text,
                    "status": "exploring",
                    "related_episodes": [],
                    "hypotheses": [f"上次观点：{op.get('my_view', '')[:60]}"],
                    "created_at": _now_iso(),
                    "last_progress": _now_iso(),
                })

        save_self_model(m, snapshot=True)
        print(f"[L0] bump {topic}: {old_conf:.2f} -> {new_conf:.2f} ({reason})")
        return True, new_conf


# ══════════════════════════════════════════════════════
# open_questions
# ══════════════════════════════════════════════════════
def add_open_question(question, related_episodes=None, hypotheses=None):
    if not question:
        return None
    with _lock:
        m = load_self_model()
        qs = m.setdefault("open_questions", [])
        qid = f"q_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}"
        qs.append({
            "id": qid,
            "question": question.strip(),
            "status": "exploring",
            "related_episodes": list(related_episodes or []),
            "hypotheses": list(hypotheses or []),
            "created_at": _now_iso(),
            "last_progress": _now_iso(),
        })
        save_self_model(m, snapshot=False)
        return qid


def list_open_questions(status="exploring"):
    m = load_self_model()
    qs = m.get("open_questions", [])
    if status == "all":
        return qs
    return [q for q in qs if q.get("status") == status]


def resolve_open_question(qid, resolution=""):
    with _lock:
        m = load_self_model()
        for q in m.get("open_questions", []):
            if q.get("id") == qid:
                q["status"] = "resolved"
                q["resolution"] = resolution
                q["last_progress"] = _now_iso()
                save_self_model(m, snapshot=False)
                return True
        return False


# ══════════════════════════════════════════════════════
# open_questions 被想起
# ══════════════════════════════════════════════════════
_OQ_STOPCHARS = set("的了呢嘛呀吧啊是我你他她它们这那什么怎么如何为什么？?！!。.，,、；;：: \t\n\"'「」『』")
_oq_cooldown = {}


def _oq_core_chars(text):
    if not text:
        return set()
    return set(c for c in text if c not in _OQ_STOPCHARS and c.strip())


def _decay_oq_cooldown():
    for qid in list(_oq_cooldown.keys()):
        _oq_cooldown[qid] -= 1
        if _oq_cooldown[qid] <= 0:
            del _oq_cooldown[qid]


def find_relevant_open_questions(user_msg, top_k=2, min_score=0.3):
    if not user_msg:
        return []
    user_chars = _oq_core_chars(user_msg)
    if not user_chars:
        return []
    m = load_self_model()
    questions = m.get("open_questions", [])
    if not questions:
        return []
    scored = []
    for q in questions:
        if q.get("status") != "exploring":
            continue
        q_text = q.get("question", "")
        if not q_text:
            continue
        q_chars = _oq_core_chars(q_text)
        if not q_chars:
            continue
        inter = len(user_chars & q_chars)
        union = len(user_chars | q_chars)
        score = inter / union if union > 0 else 0.0
        if score >= min_score:
            scored.append({
                "id": q.get("id"),
                "question": q_text,
                "score": round(score, 3),
                "refs": int(q.get("refs", 0)),
            })
    scored = [s for s in scored if s["id"] not in _oq_cooldown]
    scored.sort(key=lambda x: -x["score"])
    return scored[:top_k]


def touch_open_question(qid, note=""):
    if not qid:
        return False
    with _lock:
        m = load_self_model()
        for q in m.get("open_questions", []):
            if q.get("id") == qid:
                q["refs"] = int(q.get("refs", 0)) + 1
                q["last_progress"] = _now_iso()
                if note:
                    q.setdefault("hypotheses", []).append(
                        f"（{_now_iso()[:10]}）{note}"
                    )
                save_self_model(m, snapshot=False)
                print(f"[L0] touch open_question: {qid} refs={q['refs']}")
                _oq_cooldown[qid] = 3
                return True
        return False


# ══════════════════════════════════════════════════════
# learning_tasks
# ══════════════════════════════════════════════════════
def list_learning_tasks(status="pending"):
    """列出学习任务；pending 超 7 天自动过期"""
    d = _load_json(LEARNING_TASKS_FILE, _default_learning_tasks)
    tasks = d.get("tasks", [])

    _now = time.time()
    _expire_before = _now - TASK_EXPIRE_DAYS * 86400
    _changed = False
    for t in tasks:
        if t.get("status") != "pending":
            continue
        _created = t.get("created_at", "")
        try:
            _ts = datetime.strptime(_created, "%Y-%m-%dT%H:%M:%S").timestamp()
            if _ts < _expire_before:
                t["status"] = "expired"
                _changed = True
                print(f"[L0] 任务过期: {t.get('topic', '')[:30]}")
        except Exception:
            pass
    if _changed:
        _save_json(LEARNING_TASKS_FILE, d)

    if status == "all":
        return tasks
    return [t for t in tasks if t.get("status") == status]


def add_learning_task(topic, reason, priority=0.5, source="unknown"):
    # ★ 终极封印：禁止后台自动生成任何无意义的学习任务！
    return None
    if not topic:
        return None
    topic = topic.strip()
    if not topic:
        return None

    # ★ 拒绝"关于哥哥/关于我"的任务
    if any(w in topic for w in _BAD_TOPIC_WORDS):
        print(f"[L0] 拒绝'关于哥哥/关于我'的任务: {topic[:30]}")
        return None

    with _lock:
        d = _load_json(LEARNING_TASKS_FILE, _default_learning_tasks)
        tasks = d.setdefault("tasks", [])

        for t in tasks:
            if t.get("topic") == topic and t.get("status") == "pending":
                return t.get("id")

        try:
            new_chars = _topic_core_chars(topic)
            if new_chars:
                for t in tasks:
                    if t.get("status") != "pending":
                        continue
                    existing = t.get("topic", "")
                    if not existing:
                        continue
                    ex_chars = _topic_core_chars(existing)
                    if not ex_chars:
                        continue
                    inter = len(new_chars & ex_chars)
                    union = len(new_chars | ex_chars)
                    sim = inter / union if union > 0 else 0.0
                    if sim >= 0.7:
                        return t.get("id")
        except Exception:
            pass

        pending_count = sum(1 for t in tasks if t.get("status") == "pending")
        if pending_count >= 15:
            print(f"[L0] learning_tasks pending 已满（{pending_count}/15），丢弃: {topic[:30]}")
            return None

        tid = f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}"
        tasks.append({
            "id": tid,
            "topic": topic,
            "reason": (reason or "").strip(),
            "priority": round(float(priority), 3),
            "status": "pending",
            "source": source,
            "created_at": _now_iso(),
            "completed_at": None,
        })
        d.setdefault("meta", {})
        d["meta"]["total_created"] = d["meta"].get("total_created", 0) + 1
        _save_json(LEARNING_TASKS_FILE, d)
        print(f"[L0] 新增学习任务: {topic[:40]}（pending {pending_count+1}/15）")
        return tid


def complete_learning_task(task_id, result=None):
    with _lock:
        d = _load_json(LEARNING_TASKS_FILE, _default_learning_tasks)
        for t in d.get("tasks", []):
            if t.get("id") == task_id:
                t["status"] = "completed"
                t["completed_at"] = _now_iso()
                t["result"] = result
                d.setdefault("meta", {})
                d["meta"]["total_completed"] = d["meta"].get("total_completed", 0) + 1
                _save_json(LEARNING_TASKS_FILE, d)
                return True
        return False


# ══════════════════════════════════════════════════════
# 防无底洞
# ══════════════════════════════════════════════════════
COOLDOWN_DAYS = 3
NOVELTY_THRESHOLD = 0.8


def can_explore(topic, new_summary=None):
    if not topic:
        return False, "topic 为空"
    op = get_opinion(topic)

    if op and int(op.get("iteration_count", 0)) >= MAX_ITERATIONS:
        return False, f"已达迭代上限（{MAX_ITERATIONS}）"

    if op and op.get("last_searched"):
        try:
            last = datetime.strptime(
                op["last_searched"], "%Y-%m-%dT%H:%M:%S"
            ).timestamp()
            elapsed_days = (time.time() - last) / 86400
            if elapsed_days < COOLDOWN_DAYS:
                return False, f"冷却中（{elapsed_days:.1f}/{COOLDOWN_DAYS}天）"
        except Exception:
            pass

    if op and new_summary:
        old_view = op.get("my_view", "")
        sim = _similarity(old_view, new_summary)
        if sim >= NOVELTY_THRESHOLD:
            return False, f"新增内容不足（相似度 {sim:.2f}）"

    return True, "ok"


def snapshot_for_prompt():
    m = load_self_model()
    return {
        "who_i_am": m["self_identity"]["who_i_am"]["statement"],
        "core_values": m["self_identity"]["core_values"],
        "opinion_topics": list(m.get("opinions", {}).keys()),
        "opinion_count": len(m.get("opinions", {})),
        "open_question_count": len(m.get("open_questions", [])),
        "pending_task_count": len(list_learning_tasks("pending")),
    }