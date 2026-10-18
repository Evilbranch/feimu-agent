"""视觉感知循环 - 让她像看直播一样观察哥哥玩电脑"""
import time
import threading
import base64
import io
import numpy as np
from PIL import Image

from core import state
from tools.screen_capture import grab_window, grab_fullscreen, grab_window_fast
from brain.vision import vision_analyze
from brain.perception import ingest

# 配置项
CHECK_INTERVAL = 3          # 扫描间隔放慢
DIFF_THRESHOLD = 0.20       # ★ 阈值从 0.06 提升到 0.20（必须画面剧烈变化，比如切维度、天黑才会触发）
MIN_URGE_GAP = 180          # ★ 强制冷却拉长到 180 秒（3 分钟内最多主动说一次画面）

_last_hash = [None]
_last_urge_time = [0]
_last_analysis_time = [0]
_shutdown = threading.Event()
_force_trigger = threading.Event()  # 外部事件唤醒标志

def trigger_vision_now(reason="外部事件"):
    """外部可调用：立即唤醒视觉循环，强制分析一次画面"""
    print(f"[视觉感知] 收到唤醒信号: {reason}")
    _force_trigger.set()

def _is_changed_fast(img_small, threshold=DIFF_THRESHOLD):
    """计算 32x32 小图的变化程度"""
    current_arr = np.array(img_small, dtype=np.float32)
    
    if _last_hash[0] is None:
        _last_hash[0] = current_arr
        return True

    diff = np.abs(current_arr - _last_hash[0])
    mse = np.mean(diff) / 255.0
    
    _last_hash[0] = current_arr
    return mse > threshold

def vision_loop():
    s = state.get_state()
    print(f"[视觉感知] 事件驱动循环已启动（扫描间隔 {CHECK_INTERVAL}s，触发阈值 {DIFF_THRESHOLD}）")
    
    while not s.shutdown_flag.is_set():
        if _shutdown.is_set():
            break
            
        # 等待 2 秒，或者被外部强制唤醒
        triggered = _force_trigger.wait(timeout=CHECK_INTERVAL)
        if triggered:
            _force_trigger.clear()
        
        # ★ 把静默期从 10 秒缩短到 2 秒
        last_interaction = getattr(s, "last_interaction_time", 0)
        if time.time() - last_interaction < 2:
            continue

        # 读配置获取窗口标题
        import json, os
        from core.constants import SCRIPT_DIR
        game_title = "Minecraft"
        try:
            with open(os.path.join(SCRIPT_DIR, "data", "config.json"), "r", encoding="utf-8") as f:
                cfg = json.load(f)
                game_title = cfg.get("game_window_title", "Minecraft")
        except: pass

        # 1. 极速帧差抓取
        small_img = grab_window_fast(game_title)
        
        # ★ 智能兜底：如果找不到配置里的 Minecraft，就看看当前最前台的窗口
        if not small_img or small_img == "INACTIVE":
            from tools.screen_capture import get_active_window_title
            active_title = get_active_window_title()
            
            # 定义一个排除名单：不要在终端、代码、浏览器、聊天工具里瞎看
            _IGNORE_KEYWORDS = ["Visual Studio Code", "Ollama", "PowerShell", "CMD", "Edge", "Chrome", "微信", "QQ", "任务管理器", "Program Manager"]
            
            if active_title and not any(kw in active_title for kw in _IGNORE_KEYWORDS):
                # 尝试抓取当前这个激活的窗口
                test_img = grab_window_fast(active_title)
                if test_img and test_img != "INACTIVE":
                    small_img = test_img
                    game_title = active_title  # 动态更新当前游戏标题
        
        if not small_img or small_img == "INACTIVE":
            _last_hash[0] = None
            time.sleep(5)
            continue

        # 2. 判断是否需要分析
        changed = _is_changed_fast(small_img)
        if not changed and not triggered:
            # print("[视觉感知] 画面无变化") # 这句太吵，需要看的话可以解开
            continue
            
        # 3. 冷却限制
        if time.time() - _last_urge_time[0] < MIN_URGE_GAP:
            continue
            
        # ★ 限制 API 的调用频率（每 120 秒最多分析一次画面）
        if time.time() - _last_analysis_time[0] < 120:
            continue
            
        _last_urge_time[0] = time.time()
        _last_analysis_time[0] = time.time() # 更新分析时间

        # 4. 抓取高清图交给视觉模型
        b64 = grab_window(game_title)
        if not b64:
            continue
            
        print("[视觉感知] 🔥 画面发生显著变化/被唤醒，正在分析...")
        prompt = (
            "你是绯木，正在看哥哥玩 Minecraft。请仔细看屏幕正中央，用 15 字以内的大白话描述："
            "画面最中间有什么？直接说物品或生物的名字（比如：苦力怕、工作台、胡萝卜）。"
            "如果太模糊看不清，就说“看不清”。"
            "绝对禁止使用“仿佛、宛如、神秘、宁静”等散文词汇！"
        )
        
        import time as _time
        _t0 = _time.time()
        desc = vision_analyze(prompt, b64)
        _cost = _time.time() - _t0
        
        if not desc or desc.startswith("[视觉失败]"):
            print(f"[视觉感知] ❌ 分析失败或超时 (耗时 {_cost:.1f}s)")
            continue
            
        print(f"[视觉感知] ✅ 分析完成，耗时 {_cost:.1f}s: {desc}")
                # ★ 保存截图供你审查
        try:
            import base64
            with open("latest_vision.jpg", "wb") as f:
                f.write(base64.b64decode(b64))
            print("[视觉感知] 截图已保存至 latest_vision.jpg")
        except Exception:
            pass
        
        # 5. 清洗与过滤
        _BAD_WORDS = ["仿佛", "时光", "童年", "回忆", "感叹", "荏苒", "梦幻", "怀旧", "映入眼帘", "让我", "感觉", "心中", "显示着", "看起来", "虽然", "屏幕", "画面", "玩家", "游戏界面"]
        
        if any(w in desc for w in _BAD_WORDS):
            print(f"[视觉感知] 拦截生硬废话: {desc}")
            continue

        # ★ 新增：紧急事态直接触发，绕过 Urge 冷却
        _DANGER_WORDS = ["死", "失败", "血", "僵尸", "苦力怕", "怪物", "岩浆", "爆炸", "没血"]
        if any(w in desc for w in _DANGER_WORDS):
            print(f"[视觉感知] 🚨 检测到紧急事态，强制立即发声！")
            with s.proactive_queue_lock:
                s.proactive_queue.append({"msg": desc, "emotion": "关切"})
            _last_urge_time[0] = time.time()
            continue

        # 防复读机制
        try:
            from brain.urge import _load as _load_urge
            _recent_urges = [u["content"] for u in _load_urge() if "看哥哥打游戏" in u.get("reason", "")]
            if _recent_urges:
                _last = _recent_urges[-1]
                _overlap = sum(1 for c in set(desc) if c in _last)
                if _overlap / len(set(desc)) > 0.6:
                    print(f"[视觉感知] 触发防复读拦截: {desc}")
                    continue
        except Exception:
            pass

        ingest(source="screen", content=desc, pressure=0.5)
        _last_urge_time[0] = time.time()

def start_vision_loop():
    _shutdown.clear()
    t = threading.Thread(target=vision_loop, daemon=True)
    t.start()
    return t

def stop_vision_loop():
    _shutdown.set()