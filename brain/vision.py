"""截图 + 看图 + 摄像头"""
import os
import json
import base64
import requests
from core.network import is_online
from core.constants import (ENABLE_VISION, VISION_KEYWORDS, ZHIPU_API_KEY,
    VISION_BASE_URL, VISION_MODEL, SCRIPT_DIR,
    ENABLE_CAMERA, CAMERA_KEYWORDS, CAMERA_INDEX)


def should_use_vision(text):
    if not is_online(): return False
    if not ENABLE_VISION or not text: return False
    return any(kw in text for kw in VISION_KEYWORDS)


def should_use_camera(text):
    if not is_online(): return False
    if not ENABLE_CAMERA or not text: return False
    if should_use_vision(text): return False
    return any(kw in text for kw in CAMERA_KEYWORDS)


def capture_screen():
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        img.thumbnail((1280, 720))
        img = img.convert("RGB")
        tp = os.path.join(SCRIPT_DIR, "screen_temp.jpg")
        img.save(tp, "JPEG", quality=85)
        with open(tp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        try: os.remove(tp)
        except: pass
        return b64
    except Exception as e:
        print(f"[截图失败] {e}"); return None


def capture_camera():
    try:
        import cv2
        import time as _t
        cap = cv2.VideoCapture(CAMERA_INDEX)
        if not cap.isOpened():
            print("[摄像头] 打不开，检查是否被占用")
            return None
        for _ in range(10):
            cap.read()
            _t.sleep(0.03)
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            print("[摄像头] 读取失败"); return None
        h, w = frame.shape[:2]
        if w > 1280:
            scale = 1280 / w
            frame = cv2.resize(frame, (1280, int(h * scale)))
        tp = os.path.join(SCRIPT_DIR, "camera_temp.jpg")
        cv2.imwrite(tp, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        with open(tp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        try: os.remove(tp)
        except: pass
        print(f"[摄像头] 已抓拍（{len(b64) // 1024} KB）")
        return b64
    except Exception as e:
        print(f"[摄像头失败] {e}"); return None


def vision_analyze(q, b64):
    """用 requests 直连智谱 GLM-4V，纯 ASCII 请求绕开一切中文编码问题"""
    if not ZHIPU_API_KEY or "填入" in ZHIPU_API_KEY or "你的" in ZHIPU_API_KEY:
        return "[提示] 需要 ZHIPU_API_KEY"

    try:
        url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        headers = {
            "Authorization": f"Bearer {ZHIPU_API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
        }

        # 🎯 prompt 组装好后，json.dumps 默认会把它转义成 \uXXXX，纯 ASCII
        prompt = (f"你是绯木，成熟温柔知性的御姐。"
                  f"用户给你看画面，请直接描述你看到的内容（人物、环境、物品），"
                  f"三句以内，不要 markdown。用户说：{q}")

        payload = {
            "model": VISION_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                ]
            }],
            "max_tokens": 300,
            "temperature": 0.7,
        }

        # ⚠️ 关键：用默认的 ensure_ascii=True，中文自动转义
        body = json.dumps(payload).encode("ascii")

        r = requests.post(url, headers=headers, data=body, timeout=60)
        if r.status_code != 200:
            print(f"[GLM-4V 原始返回] {r.text[:300]}")
            return f"[视觉失败] HTTP {r.status_code}"

        data = r.json()
        content = data["choices"][0]["message"]["content"]
        return content
    except Exception as e:
        return f"[视觉失败] {e}"