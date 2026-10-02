from brain.anti_reflex import force_declarative, is_question_like

# 规则兜底
print(force_declarative("早啦。哥哥今天感觉怎么样。"))
# 期望：早啦。

print(force_declarative("今天挺平静的。哥哥今天过得怎么样呢，可以说说。"))
# 期望：今天挺平静的。

print(force_declarative("还没吃呢。哥哥应该吃过了。"))
# 期望：还没吃呢。哥哥应该吃过了。

print(force_declarative("在的，哥哥有事儿就说。"))
# 期望：在的，哥哥有事儿就说。

# 检测
assert is_question_like("早啦。哥哥今天感觉怎么样。") == True
assert is_question_like("今天挺平静的。") == False
assert is_question_like("在的，哥哥有事儿就说。") == False

print("全部通过")