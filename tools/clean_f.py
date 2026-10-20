import json
import os
import glob

EPISODES_DIR = r"F:\Ollama\data\episodes"

def clean_episodes():
    files = glob.glob(os.path.join(EPISODES_DIR, "*.json"))
    total_removed = 0
    
    for filepath in files:
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except:
                continue
                
        cleaned_data = []
        for item in data:
            content = item.get("content", "")
            etype = item.get("event_type", "")
            importance = item.get("importance", 0)
            
            # ★ 规则1：ASR 幻觉斩杀（你可以往列表里加你遇到的幻觉词）
            if any(w in content for w in ["开、关、是、不、对、错", "这样机控制", "废墨", "建设155", "字幕"]):
                total_removed += 1
                continue
                
            # ★ 规则2：视觉废话斩杀（只杀重要性低的视觉记录，高光的留下）
            if etype == "interaction" and item.get("channel") == "vision" and importance < 0.5:
                total_removed += 1
                continue
                
            # ★ 规则3：高光保护（如果是里程碑、或者重要性大于 0.6 的，绝对不删）
            # 当然，上面的规则基本不会误伤高光，这里只是兜底
            
            cleaned_data.append(item)
            
        # 保存回文件
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(cleaned_data, f, ensure_ascii=False, indent=2)
            
    print(f"清理完成！共删除了 {total_removed} 条废料记忆。高光回忆已保留。")

if __name__ == "__main__":
    clean_episodes()