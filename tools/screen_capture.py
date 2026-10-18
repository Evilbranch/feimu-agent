"""窗口级 / 全屏截图 - 供 vision_loop 和 look_at_screen 使用"""
import io
import base64

try:
    import pygetwindow as gw
    _HAS_GW = True
except ImportError:
    _HAS_GW = False

MAX_DIM = 1920
JPEG_QUALITY = 95


def _encode(img, max_dim=MAX_DIM):
    from PIL import Image
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode()


def grab_window(title_keyword, max_dim=MAX_DIM):
    """按标题关键词截窗口。找不到返回 None（调用方应 fallback 全屏）"""
    if not _HAS_GW or not title_keyword:
        return None
    try:
        wins = gw.getWindowsWithTitle(title_keyword)
        if not wins:
            return None
        win = next((w for w in wins if not w.isMinimized), None)
        if win is None:
            return None
        rect = (win.left, win.top, win.right, win.bottom)
        if rect[2] - rect[0] < 100 or rect[3] - rect[1] < 100:
            return None
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=rect)
        return _encode(img, max_dim)
    except Exception as e:
        print(f"[窗口截图] 失败: {e}")
        return None


def grab_fullscreen(crop=None, max_dim=MAX_DIM):
    """全屏截图，crop=(left, top, right, bottom) 可选裁剪"""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        if crop:
            img = img.crop(crop)
        return _encode(img, max_dim)
    except Exception as e:
        print(f"[全屏截图] 失败: {e}")
        return None


def grab_game(title_keyword=None, crop=None):
    """优先窗口级，失败 fallback 全屏"""
    if title_keyword:
        b64 = grab_window(title_keyword)
        if b64:
            return b64
    return grab_fullscreen(crop=crop)

# 在 tools/screen_capture.py 末尾追加：

def grab_window_fast(title_keyword, size=(32, 32)):
    """获取超低分辨率灰度图，用于极速帧差判断。"""
    if not _HAS_GW or not title_keyword:
        return None
    try:
        wins = gw.getWindowsWithTitle(title_keyword)
        if not wins:
            return None
        win = next((w for w in wins if not w.isMinimized), None)
        if win is None:
            return None
        
        # ★ 取消前台限制：只要没被最小化，后台也能截图！
        # 这样你切回终端看日志时，她依然能看到你游戏画面
        # （注意：如果你切去看敏感网页，建议在 config 里把 game_window_title 设死，避免截到隐私）
        # 我们在这里移除对 isActive 的强拦截
        pass
            
        rect = (win.left, win.top, win.right, win.bottom)
        if rect[2] - rect[0] < 100 or rect[3] - rect[1] < 100:
            return "INACTIVE" # 窗口太小，可能没渲染
            
        from PIL import ImageGrab
        # 尝试使用 all_screens 截取多显示器内容，防止全屏黑屏
        img = ImageGrab.grab(bbox=rect, all_screens=True)
        
        # 转灰度并大幅缩小
        img = img.convert("L").resize(size)
        return img
    except Exception as e:
        print(f"[窗口截图失败] {e}")
        return None

def get_active_window_title():
    """获取当前处于最前台（激活状态）的窗口标题"""
    if not _HAS_GW: return ""
    try:
        import pygetwindow as gw
        win = gw.getActiveWindow()
        if win and not win.isMinimized:
            return win.title
    except Exception:
        pass
    return ""