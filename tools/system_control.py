"""系统控制 - 音量 + 系统信息"""
import os
import time

# ==================== 音量控制（pycaw）====================
try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    _HAS_PYCAW = True
except ImportError:
    _HAS_PYCAW = False


def _get_volume_interface():
    """兼容 pycaw 新老版本的接口获取方式"""
    devices = AudioUtilities.GetSpeakers()
    # 新版 pycaw（>=1.4.3）：EndpointVolume 属性直接可用
    if hasattr(devices, "EndpointVolume"):
        return devices.EndpointVolume
    # 老版 pycaw：通过 Activate 拿接口
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def get_volume_level():
    if not _HAS_PYCAW: return None
    try:
        v = _get_volume_interface()
        return int(v.GetMasterVolumeLevelScalar() * 100)
    except Exception as e:
        print(f"[音量读取失败] {e}")
        return None


def set_volume_level(level):
    if not _HAS_PYCAW: return False
    try:
        level = max(0, min(100, int(level)))
        v = _get_volume_interface()
        v.SetMasterVolumeLevelScalar(level / 100.0, None)
        return True
    except Exception as e:
        print(f"[音量设置失败] {e}")
        return False


def is_muted():
    if not _HAS_PYCAW: return None
    try:
        v = _get_volume_interface()
        return bool(v.GetMute())
    except: return None


def set_mute(mute):
    if not _HAS_PYCAW: return False
    try:
        v = _get_volume_interface()
        v.SetMute(1 if mute else 0, None)
        return True
    except: return False


def adjust_volume(delta):
    cur = get_volume_level()
    if cur is None: return None
    new = max(0, min(100, cur + delta))
    if set_volume_level(new):
        return new
    return None


# ==================== 系统信息（psutil）====================
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


def get_system_info():
    if not _HAS_PSUTIL:
        return "需要安装 psutil 库呢。"
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        try:
            disk = psutil.disk_usage("C:\\")
            disk_str = f"C盘剩余 {disk.free // (1024**3)}G"
        except:
            disk_str = ""
        used_gb = mem.used // (1024**3)
        total_gb = mem.total // (1024**3)
        parts = [
            f"CPU {cpu:.0f}%",
            f"内存 {mem.percent:.0f}%（{used_gb}G/{total_gb}G）",
        ]
        if disk_str:
            parts.append(disk_str)
        return "，".join(parts) + "。"
    except Exception as e:
        return f"系统信息读取失败：{e}"