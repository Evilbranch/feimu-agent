import json
import os

# 你的文件路径
FILE_PATH = "data/episodes/2026-10.json"

def clean_memory():
    if not os.path.exists(FILE_PATH):
        print("找不到文件")
        return

    with open(FILE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    cleaned_data = []
    seen_contents = set()
    
    # 黑名单词汇（视觉废话特征）
    BAD_WORDS = ["你在玩什么游戏", "建筑太高", "方块世界", "仿佛", "童年", "时光荏苒", "映入眼帘", "这个游戏真", "好好玩", "这游戏里的"]

    for item in data:
        content = item.get("content", "")
        
        # 1. 干掉视觉废话
        if item.get("channel") == "screen":
            if any(w in content for w in BAD_WORDS):
                continue
            # 如果两分钟内有重复的 screen 记录，直接跳过
            if cleaned_data and cleaned_data[-1].get("channel") == "screen":
                if abs(item.get("ts", 0) - cleaned_data[-1].get("ts", 0)) < 120:
                    continue

        # 2. 干掉重复的搜索探索（比如搜了 10 次虚拟主播赚钱）
        if item.get("event_type") == "discovery":
            # 提取前 10 个字作为去重特征
            key = content[:10]
            if key in seen_contents:
                continue
            seen_contents.add(key)

        # 3. 必须保留的
        cleaned_data.append(item)

    # 备份原文件，写入新文件
    os.rename(FILE_PATH, FILE_PATH + ".bak")
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned_data, f, ensure_ascii=False, indent=2)

    print(f"清理完成！原数据 {len(data)} 条，清理后 {len(cleaned_data)} 条。")
    print("原文件已备份为 2026-10.json.bak")

if __name__ == "__main__":
    clean_memory()