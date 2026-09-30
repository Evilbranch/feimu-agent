import json
import os
from openai import OpenAI

# 1. 模拟读取你的 config.json
config_path = os.path.join("data", "config.json")
with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

# 2. 提取 ollama_local 配置
provider = config["providers"]["ollama_local"]
model_name = provider["model"]
base_url = provider["base_url"]
api_key = provider["api_key"]

print(f"正在测试 Provider: {provider['name']}")
print(f"模型名称: {model_name}")
print(f"API 地址: {base_url}")

# 3. 使用 openai 库构建客户端（模拟你 build_client 的操作）
client = OpenAI(api_key=api_key, base_url=base_url)

# 4. 发送测试请求
print("\n发送请求中...")
try:
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": "你好，我是绯木的开发测试员，请简短确认你已就绪。"}],
        timeout=30
    )
    print("\n✅ 测试成功！本地大脑回复：")
    print(response.choices[0].message.content)
except Exception as e:
    print(f"\n❌ 请求失败：{e}")