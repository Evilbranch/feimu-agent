import threading

# 全局大模型互斥锁：确保同一时间只有一个任务占用 Ollama 显存
LLM_LOCK = threading.Lock()