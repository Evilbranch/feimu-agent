"""L0 审计：证据链、冲突检测、编造检查

L0 比普通对话更容易编造，因为它会说"我一直认为""我记得你说过"。
所有 L0 写入前必须过审计。
"""
import os
import re
import json
import time

from core.constants import DATA_DIR
from brain.l0_model import get_opinion

EPISODES_DIR = os.path.join(DATA_DIR, "episodes")

# L0 特有的编造黑名单（比 L4 更严格）
_L0_FABRICATION_PATTERNS = [
    r"我(小|初|高)时候",
    r"我(上|以|下)辈子",
    r"我的(父母|家人|兄弟|姐妹|爷爷|奶奶)",
    r"我(看|听|闻|摸)到",
    r"我(在|去)过",
    r"我(昨天|今天|刚才)(做|去|见|吃|玩)",
    r"我(学会|考|通过)了",
]

# 允许的观点开头
_VALID_VIEW_STARTS = [
    "我觉得", "我认为", "我想", "对我来说",
    "在我看来", "我的看法", "我的观点",
]


def _load_episode_ids(days_back=180):
    ids = set()
    if not os.path.exists(EPISODES_DIR):
        return ids
    cutoff = time.time() - days_back * 86400
    try:
        for fname in os.listdir(EPISODES_DIR):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(EPISODES_DIR, fname),
                          "r", encoding="utf-8") as f:
                    items = json.load(f)
                for m in items:
                    if m.get("ts", 0) >= cutoff:
                        eid = m.get("id")
                        if eid:
                            ids.add(eid)
            except Exception:
                continue
    except Exception:
        pass
    return ids


def validate_evidence(evidence_ids):
    """检查 evidence_ids 是否有效

    识别：
    - ep_xxx → 检查 L2 是否存在
    - search_xxx / l4_xxx / conv_xxx → 格式合法即放行
    - 其他 → 无效

    返回 (all_valid, invalid_list)
    """
    if not evidence_ids:
        return False, []
    valid = _load_episode_ids()
    invalid = []
    for eid in evidence_ids:
        if not eid:
            continue
        if eid.startswith("ep_"):
            if eid not in valid:
                invalid.append(eid)
        elif eid.startswith(("search_", "l4_", "conv_", "dialog_", "learn_")):
            pass
        else:
            invalid.append(eid)
    return len(invalid) == 0, invalid


def is_fabricated(text):
    """检查文本是否含编造
    返回 (is_fab, hits)
    """
    if not text:
        return True, ["empty"]
    hits = []
    for pat in _L0_FABRICATION_PATTERNS:
        m = re.search(pat, text)
        if m:
            hits.append(m.group(0))
    return len(hits) > 0, hits


def check_confidence_sanity(view, confidence, evidence_ids):
    """置信度合理性检查

    规则：
    - confidence > 0.6 但无证据 → 降到 0.3
    - confidence > 0.8 但证据 < 2 → 降到 0.6
    """
    try:
        c = float(confidence)
    except Exception:
        c = 0.5
    c = max(0.0, min(1.0, c))

    has_evidence = bool(evidence_ids)

    if c > 0.8 and len(evidence_ids) < 2:
        return 0.6
    if c > 0.6 and not has_evidence:
        return 0.3
    return c


def detect_conflict(topic, new_view):
    """检测新观点是否和现有观点冲突
    返回 None 或 {"old_view": ..., "similarity": ...}
    """
    from difflib import SequenceMatcher
    op = get_opinion(topic)
    if not op:
        return None
    old = op.get("my_view", "")
    if not old or not new_view:
        return None
    sim = SequenceMatcher(None, old, new_view).ratio()
    if sim < 0.5:
        return {"old_view": old, "similarity": round(sim, 3)}
    return None


def suggest_view_prefix(view):
    """建议观点加上'我觉得'前缀，避免伪装成事实"""
    if not view:
        return view
    v = view.strip()
    if any(v.startswith(p) for p in _VALID_VIEW_STARTS):
        return v
    return "我觉得，" + v


def audit_opinion(topic, view, confidence, evidence_ids):
    """完整审计

    返回 (ok, cleaned, issues)
    - ok: 是否通过
    - cleaned: {view, confidence, evidence_ids}；不通过时为 None
    - issues: 问题列表
    """
    issues = []

    # 1. 编造检查
    fab, hits = is_fabricated(view)
    if fab:
        issues.append(f"编造嫌疑：{hits}")
        return False, None, issues

    # 2. 证据链检查
    all_valid, invalid = validate_evidence(evidence_ids)
    if invalid:
        issues.append(f"无效证据：{invalid}")

    # 3. 置信度合理性
    new_conf = check_confidence_sanity(view, confidence, evidence_ids)
    if abs(new_conf - float(confidence or 0)) > 1e-6:
        issues.append(f"置信度调整：{confidence} → {new_conf}")

    # 4. 冲突检测（不阻塞，只记录）
    conflict = detect_conflict(topic, view)
    if conflict:
        issues.append(
            f"与现有观点差异大（相似度 {conflict['similarity']}）："
            f"旧='{conflict['old_view'][:30]}'"
        )

    # 5. 观点应带主观前缀
    if not any(view.strip().startswith(p) for p in _VALID_VIEW_STARTS):
        issues.append("观点未带'我觉得/我认为'等主观前缀")

    cleaned = {
        "view": view.strip(),
        "confidence": new_conf,
        "evidence_ids": list(evidence_ids or []),
    }

    if invalid:
        # 无效证据直接拒
        return False, None, issues

    return True, cleaned, issues


def audit_identity_statement(statement, confidence, evidence_ids):
    """审计 self_identity 的 statement（更严格）"""
    issues = []
    if not statement or len(statement.strip()) < 4:
        return False, None, ["statement 太短"]
    fab, hits = is_fabricated(statement)
    if fab:
        return False, None, [f"编造嫌疑：{hits}"]
    new_conf = check_confidence_sanity(statement, confidence, evidence_ids)
    return True, {
        "statement": statement.strip(),
        "confidence": new_conf,
        "evidence_ids": list(evidence_ids or []),
    }, issues