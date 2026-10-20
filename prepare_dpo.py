import json
import os

input_file = "raw_dpo.json" # 确保这个文件在 F:\Ollama 目录下
output_file = r"F:\LLaMA-Factory\data\dpo_dataset.json"

with open(input_file, "r", encoding="utf-8") as f:
    raw_data = json.load(f)

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
        
    # 🌟 核心修改：直接使用纯文本 prompt 字符串，不搞 conversations 嵌套
    formatted_item = {
        "prompt": prompt,
        "chosen": chosen_text,
        "rejected": rejected_text
    }
    cleaned_data.append(formatted_item)

with open(output_file, "w", encoding="utf-8") as f:
    json.dump(cleaned_data, f, ensure_ascii=False, indent=2)

print(f"✅ 数据清洗完成：保留 {len(cleaned_data)} 对高质量数据，剔除 {dropped_count} 对劣质数据。")
print(f"✅ 已生成至 {output_file}")