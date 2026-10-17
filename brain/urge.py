"""情绪积压 - 让主动消息具有“憋不住”的冲动

设计：
- 当用户倾诉痛苦，或高频互动后突然冷场，会产生 urge
- 随着时间推移，pressure 累积
- 达到阈值，强制触发主动 speak
"""
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

def add_urge(content, reason="", base_pressure=0.3):
    """添加一个未说出口的冲动
    
    content: 心里想说的话/想问的事
    """
    if not content:
        return
    data = _load()
    # 简单去重：相似的内容不重复加
    for u in data:
        if u.get("content") == content:
            return
            
    data.append({
        "id": f"urge_{int(time.time())}_{uuid.uuid4().hex[:4]}",
        "ts": time.time(),
        "content": content,
        "reason": reason,
        "pressure": base_pressure,
    })
    _save(data)
    print(f"[Urge] 新增冲动: {content[:30]} (pressure={base_pressure:.2f})")

def tick_pressure(rate=0.1):
    """时间流逝，压力增加（基于真实时间差，而不是调用次数）"""
    data = _load()
    if not data:
        return None
        
    now = time.time()
    changed = False
    max_urge = None
    
    for u in data:
        last_tick = u.get("last_tick", now)
        # 每 60 秒（1分钟）才涨 rate (0.1)，即 10 分钟涨满
        elapsed = now - last_tick
        if elapsed >= 60:
            ticks = int(elapsed / 60)
            u["pressure"] = min(1.0, u.get("pressure", 0.0) + ticks * rate)
            u["last_tick"] = now
            changed = True
        if max_urge is None or u["pressure"] > max_urge["pressure"]:
            max_urge = u
            
    if changed:
        _save(data)
        
    if max_urge and max_urge["pressure"] >= 1.0:
        return max_urge
    return None

def pop_highest_urge():
    """如果憋不住了，拿出最高的一条并清空它"""
    urge = tick_pressure(rate=0) # 仅用来获取当前最高
    if not urge:
        return None
        
    # 不管压力多高，只要调用了 pop，就说明要释放了
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