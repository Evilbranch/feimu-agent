from wechat_commands import try_handle

tests = [
    "/status",
    "你在干嘛",
    "你在干嘛？",
    "最近在想什么",
    "今天的日记",
    "/help",
    "你好",
    "我今天很难过",
    "你在想什么",
]
for t in tests:
    reply, handled = try_handle(t)
    preview = (reply or "")[:40].replace("\n", " ")
    print(f"{handled}  | {t}  →  {preview}")