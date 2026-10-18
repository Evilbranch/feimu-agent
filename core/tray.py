"""系统托盘图标 - 显示绯木状态"""
import threading
import os
import sys

try:
    import pystray
    from PIL import Image, ImageDraw
    _HAS_TRAY = True
except ImportError:
    _HAS_TRAY = False

from core import state


# ==================== 用代码生成图标（不需要外部图片）====================
def _make_icon(color, symbol):
    """生成 64x64 圆底图标。color 是 RGB，symbol 是单个符号"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 外圆
    draw.ellipse([4, 4, 60, 60], fill=color)
    # 中心符号
    try:
        draw.text((32, 32), symbol, fill="white", anchor="mm")
    except:
        pass
    return img


# 三种状态图标
ICON_SLEEP = None
ICON_ACTIVE = None
ICON_THINK = None


def _init_icons():
    global ICON_SLEEP, ICON_ACTIVE, ICON_THINK
    ICON_SLEEP = _make_icon((80, 80, 100), "💤")
    ICON_ACTIVE = _make_icon((80, 180, 120), "●")
    ICON_THINK = _make_icon((220, 180, 80), "…")


# ==================== 托盘管理器 ====================
class TrayManager:
    def __init__(self):
        self.icon = None
        self._thread = None
        self._current_status = "sleep"

    def start(self):
        if not _HAS_TRAY:
            print("[Tray] pystray 未安装，托盘不可用")
            return
        _init_icons()
        self.icon = pystray.Icon(
            "feimu",
            icon=ICON_SLEEP,
            title="绯木 · 休眠中",
            menu=pystray.Menu(
                pystray.MenuItem("显示状态", self._on_show),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出程序", self._on_exit),
            )
        )
        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()
        print("[Tray] 托盘图标已启动")

    def set_status(self, status):
        """status: sleep / active / think"""
        if not self.icon: return
        if status == self._current_status: return
        self._current_status = status
        try:
            if status == "sleep":
                self.icon.icon = ICON_SLEEP
                self.icon.title = "绯木 · 休眠中"
            elif status == "active":
                self.icon.icon = ICON_ACTIVE
                self.icon.title = "绯木 · 对话中"
            elif status == "think":
                self.icon.icon = ICON_THINK
                self.icon.title = "绯木 · 思考中"
        except Exception as e:
            print(f"[Tray] 更新状态失败: {e}")

    def _on_show(self, icon, item):
        try:
            icon.notify(f"当前状态：{self._current_status}", "绯木")
        except: pass

    def _on_exit(self, icon, item):
        print("[Tray] 用户点击退出")
        try:
            self.icon.stop()
        except: pass
        s = state.get_state()
        s.shutdown_flag.set()