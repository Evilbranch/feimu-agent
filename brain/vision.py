"""视觉感知模块 - 截图/摄像头/读取本地图片，并转为第一人称感知"""
import os
import io
import json
import base64
import requests
from core.network import is_online
from core.constants import (ENABLE_VISION, VISION_KEYWORDS,
    VISION_BASE_URL, VISION_MODEL, SCRIPT_DIR,
    ENABLE_CAMERA, CAMERA_KEYWORDS, CAMERA_INDEX)

MAX_VL_DIM = 800          # Qwen2.5-VL 入口统一缩放到这个最长边
VL_JPEG_QUALITY = 85


def _load_cfg():
    try:
        with open(os.path.join(SCRIPT_DIR, "data", "config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_vision_provider():
    """返回 (provider, base_url, api_key, model)"""
    cfg = _load_cfg()
    provider = (cfg.get("vision_provider") or "zhipu").lower()

    if provider == "ollama":
        base = cfg.get("vision_base_url") or "http://localhost:11434/v1"
        model = cfg.get("vision_model") or "qwen2.5vl:7b"
        return "ollama", base, "ollama", model

    # 默认智谱
    key = cfg.get("zhipu_api_key") or cfg.get("providers", {}).get("glm", {}).get("api_key") or ""
    base = cfg.get("vision_base_url") or "https://open.bigmodel.cn/api/paas/v4"
    model = cfg.get("vision_model") or "glm-4v-flash"
    return "zhipu", base, key, model


def _resize_b64(b64, max_dim=MAX_VL_DIM):
    """入口统一缩放：Qwen2.5-VL 按原分辨率编码，1080p 截图约 9800 视觉 token，必须缩"""
    if not b64:
        return b64
    try:
        from PIL import Image
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=VL_JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"[视觉] 缩放失败，用原图: {e}")
        return b64


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
    """全屏截图，保留原接口。窗口级请用 tools/screen_capture.py"""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=VL_JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"[截图失败] {e}")
        return None


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
            print("[摄像头] 读取失败")
            return None
        h, w = frame.shape[:2]
        if w > MAX_VL_DIM:
            scale = MAX_VL_DIM / w
            frame = cv2.resize(frame, (MAX_VL_DIM, int(h * scale)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, VL_JPEG_QUALITY])
        if not ok:
            return None
        print(f"[摄像头] 已抓拍（{len(buf) // 1024} KB）")
        return base64.b64encode(buf.tobytes()).decode()
    except Exception as e:
        print(f"[摄像头失败] {e}")
        return None


def file_to_b64(image_path):
    if not os.path.exists(image_path):
        return None
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception as e:
        print(f"[视觉] 读取文件失败: {e}")
        return None


_VISION_PROMPT = (
    "你是绯木（哥哥的AI伴侣）。哥哥给你看了一个画面，并说：'{q}'\n"
    "请用第一人称（我）描述你在这个画面里看到了什么。\n"
    "要求：\n"
    "1. 绝对不要像AI助手一样报流水账。\n"
    "2. 直接说你的视觉感受。\n"
    "3. 必须带上你的主观情绪反应。\n"
    "4. 50字以内。"
)


def vision_analyze(q, b64):
    if not b64:
        return None

    provider, base_url, api_key, model = _get_vision_provider()
    if provider == "zhipu" and not api_key:
        print("[视觉] 缺少智谱 API Key，请在 config.json 配置 glm.api_key")
        return None

    b64 = _resize_b64(b64)

    prompt = _VISION_PROMPT.format(q=q)
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        "max_tokens": 300,
        "temperature": 0.7,
        "keep_alive": 0,  # ★ 加上这一行：分析完立刻卸载模型，释放显存
        "num_ctx": 8192
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json; charset=utf-8",
    }

    url = base_url.rstrip("/") + "/chat/completions"
    try:
        body = json.dumps(payload).encode("utf-8")
        r = requests.post(url, headers=headers, data=body, timeout=60)
        if r.status_code != 200:
            print(f"[VL 原始返回] {r.text[:300]}")
            return f"[视觉失败] HTTP {r.status_code}"
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[视觉失败] {e}"