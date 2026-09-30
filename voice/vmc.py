"""Live2D 表情控制 - VTS API 驱动（眨眼、呼吸、头部微动、情绪、口型同步）"""
import os
import json
import time
import math
import random
import threading
import numpy as np
import websocket
from core.constants import (DATA_DIR, VMC_ENABLED, BLINK_INVERTED,
    BLINK_MIN_INTERVAL, BLINK_MAX_INTERVAL, BLINK_DURATION,
    EMOTION_KEYWORDS)
from core import state

# VTube Studio API 连接配置
VTS_HOST = "localhost"
VTS_PORT = 8001
TOKEN_FILE = os.path.join(DATA_DIR, "vts_token.json")
PLUGIN_NAME = "FeimuPlugin"
PLUGIN_DEV = "FeimuProject"

# ⚠️ VTS 追踪参数名（范围全部 0~1，除了 FaceAngle）
P_EYE_L = "EyeOpenLeft"         # 0=闭, 1=睁（注意和我们语义相反）
P_EYE_R = "EyeOpenRight"
P_ANGLE_X = "FaceAngleX"        # -30~30
P_ANGLE_Y = "FaceAngleY"
P_ANGLE_Z = "FaceAngleZ"
P_MOUTH_OPEN = "MouthOpen"      # 0=闭, 1=张开
P_MOUTH_SMILE = "MouthSmile"    # 0=不笑, 1=最大笑
P_BROW = "Brows"                # 0=自然, 1=动眉
P_ANGRY = "FaceAngry"           # 0=不生气, 1=最生气
P_CHEEK = "CheekPuff"           # 鼓腮帮

# 情绪 → VTS 追踪参数映射（都用 0~1 值）
EMOTION_PARAMS = {
    "开心": {P_MOUTH_SMILE: 1.0,  P_BROW: 0.6, P_ANGRY: 0.0},
    "难过": {P_MOUTH_SMILE: 0.0,  P_BROW: 0.3, P_ANGRY: 0.2},   # 不笑+轻微皱眉
    "生气": {P_MOUTH_SMILE: 0.0,  P_BROW: 0.7, P_ANGRY: 0.9},
    "有趣": {P_MOUTH_SMILE: 0.9,  P_BROW: 0.5, P_ANGRY: 0.0},
    "关切": {P_MOUTH_SMILE: 0.3,  P_BROW: 0.4, P_ANGRY: 0.1},
    "失落": {P_MOUTH_SMILE: 0.0,  P_BROW: 0.3, P_ANGRY: 0.15},
    "平静": {P_MOUTH_SMILE: 0.0,  P_BROW: 0.0, P_ANGRY: 0.0},
}


def detect_emotion(text):
    if not text: return "平静"
    for emo, kws in EMOTION_KEYWORDS.items():
        for kw in kws:
            if kw in text: return emo
    return "平静"


# ==================== VTS API 客户端 ====================
class _VTSClient:
    def __init__(self):
        self.ws = None
        self.connected = False
        self.token = self._load_token()
        self.req_counter = 0
        self._lock = threading.Lock()

    def _load_token(self):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("token")
        except:
            return None

    def _save_token(self, token):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump({"token": token}, f)
        except: pass

    def _send(self, msg_type, data=None):
        if not self.ws: return
        with self._lock:
            self.req_counter += 1
            rid = f"feimu_{self.req_counter}"
        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": rid,
            "messageType": msg_type,
        }
        if data: msg["data"] = data
        try:
            self.ws.send(json.dumps(msg))
        except Exception as e:
            print(f"[VTS发送] {e}")

    def _on_open(self, ws):
        print("[VTS] 已连接到 VTube Studio")
        if self.token:
            self._auth()
        else:
            self._req_token()

    def _on_message(self, ws, msg):
        try:
            data = json.loads(msg)
            t = data.get("messageType", "")
            if t == "AuthenticationTokenResponse":
                token = data.get("data", {}).get("authenticationToken")
                if token:
                    self.token = token
                    self._save_token(token)
                    print("[VTS] 已保存授权 token")
                    self._auth()
            elif t == "AuthenticationResponse":
                if data.get("data", {}).get("authenticated"):
                    self.connected = True
                    print("[VTS] ✅ 认证成功，Live2D 控制已激活")
                else:
                    print("[VTS] 认证失败，重新申请 token")
                    self.token = None
                    self._req_token()
            elif t == "InputParameterListResponse":
                # 🆕 参数列表响应
                params = data.get("data", {}).get("defaultParameters", [])
                custom = data.get("data", {}).get("customParameters", [])
                print("\n========== VTS 可注入参数列表 ==========")
                print("【标准参数】")
                for p in params:
                    print(f"  {p.get('name')}  (范围 {p.get('min')} ~ {p.get('max')}, 默认 {p.get('defaultValue')})")
                if custom:
                    print("【自定义参数】")
                    for p in custom:
                        print(f"  {p.get('name')}  (范围 {p.get('min')} ~ {p.get('max')})")
                print("=========================================\n")
            elif t == "APIError":
                err = data.get("data", {})
                eid = err.get("errorID")
                if eid == 50:
                    self._req_token()
                else:
                    if eid != 453:  # 参数不存在不刷屏
                        print(f"[VTS错误] {err}")
        except Exception as e:
            print(f"[VTS消息] {e}")

    def _on_error(self, ws, e): pass

    def _on_close(self, ws, code, msg):
        if self.connected:
            print(f"[VTS] 连接断开")
        self.connected = False

    def _req_token(self):
        self._send("AuthenticationTokenRequest", {
            "pluginName": PLUGIN_NAME,
            "pluginDeveloper": PLUGIN_DEV,
        })
        print("[VTS] ⚠️ 请在 VTube Studio 里点击【允许】授权")

    def _auth(self):
        self._send("AuthenticationRequest", {
            "pluginName": PLUGIN_NAME,
            "pluginDeveloper": PLUGIN_DEV,
            "authenticationToken": self.token,
        })

    def connect(self):
        url = f"ws://{VTS_HOST}:{VTS_PORT}"
        self.ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        t = threading.Thread(target=self.ws.run_forever, daemon=True)
        t.start()

    def inject_params(self, params):
        if not self.connected: return
        inject = [{"id": k, "value": float(v), "weight": 1.0} for k, v in params.items()]
        self._send("InjectParameterDataRequest", {
            "faceFound": True,
            "mode": "set",
            "parameterValues": inject,
        })

    def list_parameters(self):
        """🆕 请求 VTS 返回所有可用参数"""
        self._send("InputParameterListRequest")


# ==================== 全局实例 ====================
_client = None


def init():
    global _client
    s = state.get_state()
    if not VMC_ENABLED:
        print("[VTS] 已禁用")
        return
    _client = _VTSClient()
    _client.connect()

    # 🆕 等最多 60 秒，直到认证成功（首次需要手动点允许）
    print("[VTS] 正在等待授权...")
    print("[VTS] ⚠️ 请立刻看 VTube Studio 窗口，弹窗出现时点【允许】")
    for i in range(120):   # 120 × 0.5s = 60 秒
        if _client.connected:
            break
        time.sleep(0.5)
        if i == 20:
            print("[VTS] ⏰ 已等 10 秒，还没看到弹窗？检查 VTS 窗口是否被挡住")
        if i == 60:
            print("[VTS] ⏰ 已等 30 秒，弹窗还没出现？")
    if _client.connected:
        print("[VTS] ✅ 认证完成，Live2D 控制已激活")
    else:
        print("[VTS] ⚠️ 授权超时（60秒），Live2D 控制未激活")
        print("[VTS]    下次启动时重试，或删掉 data\\vts_token.json 重置")
    s.vmc_client = _client


def list_parameters():
    """对外暴露：打印所有可用参数"""
    if _client and _client.connected:
        _client.list_parameters()
        time.sleep(2)
    else:
        print("[VTS] 未连接，无法查询参数")


def set_expression(emo):
    if not _client or not _client.connected: return
    params = EMOTION_PARAMS.get(emo, EMOTION_PARAMS["平静"])
    _client.inject_params(params)


def set_expression_smooth(emo, dur=0.5, steps=12):
    set_expression(emo)


def blink(raw):
    """raw: 0=不眨眼, 1=完全闭眼"""
    if not _client or not _client.connected: return
    v = (1.0 - raw) if BLINK_INVERTED else raw
    # VTS 追踪参数里：EyeOpenLeft/Right 是 1=睁眼，0=闭眼
    eye_open = 1.0 - v
    _client.inject_params({P_EYE_L: eye_open, P_EYE_R: eye_open})


def head(pitch=0.0, yaw=0.0, roll=0.0):
    if not _client or not _client.connected: return
    x = math.degrees(yaw)
    y = math.degrees(pitch)
    z = math.degrees(roll)
    _client.inject_params({P_ANGLE_X: x, P_ANGLE_Y: y, P_ANGLE_Z: z})


def set_mouth(value):
    """🆕 设置嘴巴张开程度 0~1"""
    if not _client or not _client.connected: return
    value = max(0.0, min(1.0, float(value)))
    _client.inject_params({P_MOUTH_OPEN: value})


# 兼容旧接口
def _send(d): pass
def _head(pitch=0.0, yaw=0.0, roll=0.0): head(pitch, yaw, roll)
def _blink(raw): blink(raw)


# ==================== 🆕 口型同步 ====================
_mouth_stop = threading.Event()
_mouth_thread = None


def _load_envelope(path, fps=30):
    """读音频文件，返回每帧的 RMS 包络（0~1）"""
    try:
        try:
            import soundfile as sf
            data, sr = sf.read(path, dtype='float32')
        except Exception:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(path)
            sr = audio.frame_rate
            samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
            if audio.channels == 2:
                samples = samples.reshape(-1, 2).mean(axis=1)
            data = samples / 32768.0
    except Exception as e:
        print(f"[口型] 读取音频失败: {e}")
        return None

    if len(data.shape) > 1:
        data = data.mean(axis=1)

    frame_len = max(1, sr // fps)
    n_frames = len(data) // frame_len
    if n_frames == 0:
        return None

    frames = data[:n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt((frames ** 2).mean(axis=1))

    peak = rms.max()
    if peak > 1e-6:
        rms = rms / peak

    return rms


def start_mouth_sync(audio_path, fps=30):
    """🆕 播放音频时启动口型同步"""
    global _mouth_thread
    stop_mouth_sync()
    _mouth_stop.clear()

    env = _load_envelope(audio_path, fps)
    if env is None or len(env) == 0:
        return

    def _worker():
        interval = 1.0 / fps
        for v in env:
            if _mouth_stop.is_set():
                break
            # 平滑：微弱信号当闭嘴，放大强信号
            val = max(0.0, min(1.0, float(v) * 1.6))
            set_mouth(val)
            time.sleep(interval)
        set_mouth(0.0)

    _mouth_thread = threading.Thread(target=_worker, daemon=True)
    _mouth_thread.start()


def stop_mouth_sync():
    """🆕 停止口型同步"""
    _mouth_stop.set()
    set_mouth(0.0)


# ==================== 生活模拟器 ====================
class LifeSimulator:
    def __init__(self):
        self.head_yaw = 0.0
        self.head_pitch = 0.0
        self.head_yaw_vel = 0.0
        self.head_pitch_vel = 0.0
        self.next_blink = time.time() + random.uniform(BLINK_MIN_INTERVAL, BLINK_MAX_INTERVAL)
        self.blink_start = 0.0
        self.is_blinking = False
        self.last_update = time.time()
        self.breath_phase = 0.0
        blink(0.0)

    def update(self):
        if not _client or not _client.connected: return
        now = time.time()
        dt = now - self.last_update
        self.last_update = now
        if random.random() < 0.005:
            self.head_yaw = random.uniform(-0.12, 0.12)
            self.head_pitch = random.uniform(-0.08, 0.08)
        k, d = 3.0, 0.7
        self.head_yaw_vel += (self.head_yaw - 0) * k * dt
        self.head_yaw_vel *= (1 - d * dt)
        self.head_pitch_vel += (self.head_pitch - 0) * k * dt
        self.head_pitch_vel *= (1 - d * dt)
        self.breath_phase += dt * 1.2
        bo = math.sin(self.breath_phase) * 0.03
        bv = 0.0
        if self.is_blinking:
            e = now - self.blink_start
            if e < BLINK_DURATION:
                bv = 1.0 - abs((e / BLINK_DURATION) * 2 - 1)
            else:
                self.is_blinking = False
                self.next_blink = now + random.uniform(BLINK_MIN_INTERVAL, BLINK_MAX_INTERVAL)
        else:
            if now >= self.next_blink:
                self.is_blinking = True
                self.blink_start = now
        head(self.head_pitch + bo, self.head_yaw, 0.0)
        blink(bv)