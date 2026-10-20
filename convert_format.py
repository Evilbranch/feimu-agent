import json

# 相对路径：基于你 VS Code 打开的根目录 Ollama
input_path = "finetune_data/dpo_dataset.json" 
output_path = "finetune_data/dpo_dataset_new.json" # 输出为新文件

with open(input_path, "r", encoding="utf-8") as f:
    data = json.load(f)

result = []

for item in data:
    result.append({
        "conversations": [
            {
                "from": "human",
                "value": item["prompt"]
            }
        ],
        "chosen": item["chosen"],
        "rejected": item["rejected"]
    })

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"转换完成！共处理 {len(result)} 条数据。")
print(f"新文件保存在: {output_path}")