from brain.episodic import record_episode, retrieve_episodes, format_episodes_for_prompt

# 写两条测试数据
record_episode(
    content="哥哥说他小时候养过英短",
    event_type="user_fact",
    entities=["哥哥", "英短", "猫"],
    self_emotion={"label": "有点开心", "valence": 0.41, "arousal": 0.48},
    self_intent="speak",
    self_role="responder",
    channel="local",
    raw_context="哥哥：我以前养过",
)
record_episode(
    content="哥哥今天说他工作有点累",
    event_type="user_emotion",
    entities=["哥哥", "工作"],
    self_emotion={"label": "关切", "valence": 0.1, "arousal": 0.5},
    self_intent="speak",
    self_role="responder",
    channel="local",
)

# 检索
eps = retrieve_episodes(query="猫", top_k=5)
print(f"检索到 {len(eps)} 条")
for e in eps:
    day = e.get("day", "")
    content = e.get("content", "")
    imp = e.get("importance", 0)
    print(f"  [{day}] {content[:40]}  imp={imp}")

print()
print(format_episodes_for_prompt(eps))