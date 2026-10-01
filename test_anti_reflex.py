# test_anti_reflex.py
from brain.anti_reflex import (
    has_question_ending, count_recent_question_endings,
    strip_trailing_question, apply_anti_reflex,
)

# 1. 检测扩展
assert has_question_ending("你今天怎么样？") == True
assert has_question_ending("你有啥事儿嘛。") == True   # 末尾语气词
assert has_question_ending("我在家呢") == True        # 末尾语气词
assert has_question_ending("我在呢，哥哥。") == False  # 末尾是"哥哥"
assert has_question_ending("还没呢，哥哥你吃了没。") == False  # 末尾"没"不在表

# 2. 修正效果
print(strip_trailing_question("我在呢，哥哥有啥事儿嘛。"))
# 期望：我在呢，哥哥有啥事儿。

print(strip_trailing_question("你今天怎么样？"))
# 期望：你今天怎么样。

print(strip_trailing_question("我就在这儿等着和哥哥聊天呢，有啥想说的吗。"))
# 期望：我就在这儿等着和哥哥聊天呢，有啥想说的。

print(strip_trailing_question("要不要一起玩？"))
# 期望：想一起玩也可以。

# 3. 计数（修正后仍应被识别）
history = [
    {"role": "user", "content": "a"},
    {"role": "assistant", "content": "你有啥事儿嘛。"},   # 修正后，仍算反问
    {"role": "user", "content": "b"},
    {"role": "assistant", "content": "今天怎么样？"},
]
assert count_recent_question_endings(history) == 2

print("全部通过")