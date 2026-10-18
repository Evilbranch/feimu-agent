"""L0 认知清理工具 - 防止 L0 无限膨胀"""
import json
import os
from core.constants import DATA_DIR
from brain import l0_model

def prune_learning_tasks(max_pending=10):
    """清理过期的学习任务，只保留优先级最高的前 N 个"""
    p = os.path.join(DATA_DIR, "l0", "learning_tasks.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        
        tasks = d.get("tasks", [])
        pending = [t for t in tasks if t.get("status") == "pending"]
        done = [t for t in tasks if t.get("status") == "completed"]
        
        # 按 priority 降序，只保留前 N 个 pending
        pending.sort(key=lambda x: x.get("priority", 0), reverse=True)
        keep_pending = pending[:max_pending]
        
        d["tasks"] = keep_pending + done[-20:] # done 也只保留最近 20 个
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        print(f"[L0清理] 学习任务：{len(pending)} -> {len(keep_pending)} 个")
    except Exception as e:
        print(f"[L0清理] 任务清理失败: {e}")

def cluster_opinions(threshold=0.5):
    """把相似的观点聚类，保留最新的，归档旧的"""
    # 这是一个相对轻量的清理，主要是合并极端相似的
    m = l0_model.load_self_model()
    ops = m.get("opinions", {})
    if len(ops) < 30:
        return # 没超过 30 个不清理
        
    topics = list(ops.keys())
    to_merge = []
    
    for i in range(len(topics)):
        for j in range(i+1, len(topics)):
            t1, t2 = topics[i], topics[j]
            # 使用底层的相似度计算
            from brain.l0_model import _similarity
            if _similarity(t1, t2) > threshold:
                to_merge.append((t1, t2))
                
    # 执行合并（保留创建时间较晚的）
    for t1, t2 in to_merge:
        if t1 in ops and t2 in ops:
            # 简单策略：保留 iter 较多的
            if ops[t1].get("iteration_count", 1) >= ops[t2].get("iteration_count", 1):
                del ops[t2]
            else:
                del ops[t1]
                
    m["opinions"] = ops
    l0_model.save_self_model(m, snapshot=False)
    print(f"[L0清理] 观点合并：清理了 {len(to_merge)} 个冗余观点，当前剩余 {len(ops)} 个")

def clean_all():
    prune_learning_tasks(max_pending=10)
    cluster_opinions(threshold=0.5)