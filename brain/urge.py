"""情绪积压 - 让主动消息具有"憋不住"的冲动"""
import os
import json
import time
import uuid
from datetime import datetime
from core.constants import DATA_DIR

URGE_FILE = os.path.join(DATA_DIR, "urge_queue.json")

def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

def _load():
    if not os.path.exists(URGE_FILE):
        return []
    try:
        with open(URGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(URGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_urge(content, reason="", base_pressure=0.3, source="unknown", action_type="speak"):
    if not content: return
    data = _load()
    
    # ★ 计算优先级 (数字越大越优先)
    priority = 0
    if source in ("owner", "mc"): priority = 3
    elif source == "vision": priority = 2
    elif source == "audience": priority = 1
    
    # 简单去重
    for u in data:
        if u.get("content") == content:
            u["pressure"] = max(u.get("pressure", 0), base_pressure)
            u["source"] = source
            u["priority"] = priority
            _save(data)
            return
        if source == "vision" and u.get("source") == "vision":
            u["pressure"] = max(u.get("pressure", 0), base_pressure)
            u["content"] = content
            u["priority"] = priority
            _save(data)
            return
            
    data.append({
        "id": f"urge_{int(time.time())}_{uuid.uuid4().hex[:4]}",
        "ts": time.time(),
        "content": content,
        "reason": reason,
        "pressure": base_pressure,
        "source": source,
        "action_type": action_type,
        "priority": priority,
    })
    _save(data)
    print(f"[Urge] 新增冲动[{source}] (P{priority}): {content[:30]} (pressure={base_pressure:.2f})")

def tick_pressure(rate=0.1):
    """时间流逝，压力增加"""
    data = _load()
    if not data:
        return None
        
    now = time.time()
    changed = False
    
    for u in data:
        last_tick = u.get("last_tick", now)
        elapsed = now - last_tick
        if elapsed >= 60:
            ticks = int(elapsed / 60)
            u["pressure"] = min(1.0, u.get("pressure", 0.0) + ticks * rate)
            u["last_tick"] = now
            changed = True
            
    if changed:
        _save(data)
        
    # ★ 新增：按优先级排序挑选
    max_urge = None
    for u in data:
        if max_urge is None:
            max_urge = u
        else:
            u_p = u.get("priority", 0)
            m_p = max_urge.get("priority", 0)
            if u_p > m_p:
                max_urge = u
            elif u_p == m_p and u.get("pressure", 0) > max_urge.get("pressure", 0):
                max_urge = u
                
    if max_urge and max_urge["pressure"] >= 1.0:
        return max_urge
    return None

def pop_highest_urge():
    urge = tick_pressure(rate=0)
    if not urge:
        return None
        
    data = _load()
    released = None
    new_data = []
    for u in data:
        if u["id"] == urge["id"]:
            released = u
        else:
            new_data.append(u)
            
    _save(new_data)
    return released

def clear_all():
    _save([])