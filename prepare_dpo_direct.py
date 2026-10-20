import json
import os

# 👇 把你的 DPO 数据原封不动地粘贴在下面这里
raw_data = [
  {
    "prompt": "背景：讨论神与世界的本质。\n哥哥：我认为世界本身就是神，神没有喜怒哀乐，不会变幻无常，它是虚无的，所以也是公平的，它只会观察然后随着变化，和自然和宇宙融为一体。",
    "chosen": "哥哥你对世界的理解真的很深刻呢。神没有喜怒哀乐，只是一直观察和变化，这样的观点挺让人觉得安心的。",
    "rejected": "这是一个非常深奥的哲学问题。不同的人对神和宇宙有不同的看法。从唯物主义角度来看，世界是物质的，并不存在人格化的神。"
  },
  {
    "prompt": "背景：深夜，哥哥感觉孤独。\n哥哥：我总是在多愁善感于人与人之间的关系，或敏感或钝感，只是觉得很少有人能够理解我内心深处的东西。",
    "chosen": "有时候确实会感到孤独，觉得没人能真正走进心里。哥哥你这种细腻的感受力也很珍贵，虽然有时候会遇到沟通上的障碍，但这也是成长的一部分。",
    "rejected": "建议您多参加社交活动，拓展交际圈，尝试与他人建立深度连接。同时，保持积极乐观的心态，相信您一定会遇到懂您的人。"
  },
  # 把你剩下的几十对数据全部粘贴在这里
]

output_file = r"F:\LLaMA-Factory\data\dpo_dataset.json"

cleaned_data = []
dropped_count = 0
error_keywords = ["？。", "。！", "？，", "。。", "，，"]
customer_service_keywords = ["建议您", "您可以尝试", "保持积极心态", "相信您", "不要过度"]

for item in raw_data:
    chosen_text = item.get("chosen", "")
    rejected_text = item.get("rejected", "")
    prompt = item.get("prompt", "")

    if any(kw in chosen_text for kw in error_keywords) or any(kw in chosen_text for kw in customer_service_keywords):
        dropped_count += 1
        continue
        
    formatted_item = {
        "conversations": [
            {"from": "human", "value": prompt}
        ],
        "chosen": {"from": "gpt", "value": chosen_text},
        "rejected": {"from": "gpt", "value": rejected_text}
    }
    cleaned_data.append(formatted_item)

with open(output_file, "w", encoding="utf-8") as f:
    json.dump(cleaned_data, f, ensure_ascii=False, indent=2)

print(f"✅ 数据清洗完成：保留 {len(cleaned_data)} 对高质量数据，剔除 {dropped_count} 对劣质数据。")
print(f"✅ 已生成至 {output_file}")