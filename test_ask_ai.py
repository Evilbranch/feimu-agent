import time
from openai import OpenAI
from brain.llm import ask_ai
from core import state

# 初始化全局状态
s = state.get_state()
s.rag = None
s.mood_mgr = None
s.last_interrupted = False

provider = {
    "name": "本地 Ollama (feimu)",
    "api_key": "ollama",
    "base_url": "http://localhost:11434/v1",
    "model": "qwen2.5:3b",   # 如果你在 Ollama 里建了 feimu，就改成 "feimu"
    "temperature": 0.1,
    "max_tokens": 600,
    "supports_tools": True,
}
client = OpenAI(api_key=provider["api_key"], base_url=provider["base_url"])

# 故意说一句老代码接不住的
ui = "我十分钟后要开会，帮我记一下，到时候提醒我。"
history = []

print("=" * 50)
print(f"用户：{ui}")
print("=" * 50)

rep = ask_ai(client, provider, history, ui, time.time(), use_tools=True)

print("\n" + "=" * 50)
print(f"最终回复：{rep}")
print("=" * 50)