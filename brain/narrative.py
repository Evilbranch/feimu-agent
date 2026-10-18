"""叙事整合器 - 把 L2 记忆转化为第一人称的"回忆"

目的：
让她的回复不仅仅基于短时记忆和 L0 观点，
还能自然地带出过去的 L2 事件。
"""
import time

def get_relevant_memories(user_msg, top_k=3):
    """从 L2 中检索和当前消息相关的记忆，返回第一人称的叙述"""
    try:
        from brain.episodic import retrieve_episodes
        # 借助现有的检索引擎（带情绪匹配和时间衰减）
        eps = retrieve_episodes(query=user_msg, top_k=top_k, days_back=30)
        if not eps:
            return ""
            
        lines = []
        for e in eps:
            day = e.get("day", "")
            content = e.get("content", "")
            # 去除可能存在的第三人称（比如"哥哥说"），转化为自然描述
            content = content.replace("哥哥说：", "")
            content = content.replace("哥哥说", "")
            # 简略过滤，避免太长的记忆
            if len(content) > 40:
                content = content[:40] + "…"
            lines.append(f"- ({day}) {content}")
            
        if not lines:
            return ""
            
        return "\n".join(lines)
    except Exception as e:
        print(f"[叙事] 检索失败: {e}")
        return ""

def build_narrative_prompt(memories):
    """把记忆片段包装成 prompt"""
    if not memories:
        return ""
    
    return (
        "\n\n【你脑海中浮现出的真实记忆】\n"
        f"{memories}\n\n"
        "（注意：如果这些记忆和当前话题相关，你可以自然地提起来。"
        "比如'我记得你之前说过……'或者'前几天我还在想……'。"
        "如果你觉得不相关，就不提，不要强行回忆。）"
    )