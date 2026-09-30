import json
import base64
import requests

# 从 constants.py 读 key
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.constants import ZHIPU_API_KEY, VISION_MODEL

print("Key 前 10 位:", ZHIPU_API_KEY[:10] if ZHIPU_API_KEY else "空")
print("模型:", VISION_MODEL)

# 读图
with open("test_cam.jpg", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
print("图 base64 长度:", len(b64))

url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
headers = {
    "Authorization": f"Bearer {ZHIPU_API_KEY}",
    "Content-Type": "application/json; charset=utf-8",
}
# ⚠️ 用纯英文 prompt，绕开一切中文编码问题
payload = {
    "model": VISION_MODEL,
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "What do you see in this image? Describe briefly."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        ]
    }],
    "max_tokens": 300,
    "temperature": 0.7,
}

try:
    body = json.dumps(payload).encode("ascii")
    print("body 类型:", type(body), "长度:", len(body))
    r = requests.post(url, headers=headers, data=body, timeout=60)
    print("状态码:", r.status_code)
    print("响应前 500 字:", r.text[:500])
except Exception as e:
    print("异常:", type(e).__name__, e)