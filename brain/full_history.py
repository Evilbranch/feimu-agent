"""完整对话历史 - 追加明文日志

设计：
- 每轮对话追加到 data/full_history.log
- 格式：ISO 时间戳 + 来源 + 用户消息 + 她的回复
- 简单追加，线程安全
"""
import os
import time
import threading
from core.constants import DATA_DIR

LOG_FILE = os.path.join(DATA_DIR, "full_history.log")
_lock = threading.Lock()


def append(user_msg, assistant_reply, source="owner"):
    """追加一轮对话到日志"""
    if not user_msg and not assistant_reply:
        return
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n=== [{ts}] source={source} ===\n")
                f.write(f"用户：{user_msg}\n")
                f.write(f"绯木：{assistant_reply}\n")
    except Exception as e:
        print(f"[full_history] 写入失败: {e}")