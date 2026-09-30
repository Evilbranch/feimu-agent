"""Minecraft 桥接 - 通过 WebSocket 控制 Node bot"""
import json
import time
import uuid
import threading

try:
    import websocket
    _HAS_WS = True
except ImportError:
    _HAS_WS = False

WS_URL = "ws://127.0.0.1:8200"
_lock = threading.Lock()
_pending = {}
_ws = None
_ws_ready = threading.Event()
_event_callback = None


def set_event_callback(cb):
    """设置 MC 事件回调 + 后台保持 WebSocket 长连接"""
    global _event_callback
    _event_callback = cb

    def _connect_loop():
        from core import state
        s = state.get_state()
        while not s.shutdown_flag.is_set():
            try:
                if not ensure_connection():
                    time.sleep(10)  # 10 秒重试一次
                    continue
                while (not s.shutdown_flag.is_set()
                       and _ws and _ws.sock and _ws.sock.connected):
                    time.sleep(2)
                if not s.shutdown_flag.is_set():
                    time.sleep(5)
            except Exception:
                time.sleep(10)

    threading.Thread(target=_connect_loop, daemon=True).start()


def _on_message(ws, msg):
    try:
        data = json.loads(msg)
    except Exception:
        return
    t = data.get("type")
    if t == "response":
        cid = data.get("id")
        with _lock:
            fut = _pending.pop(cid, None)
        if fut:
            fut["result"] = data
            fut["event"].set()
    elif t == "event":
        if _event_callback:
            try:
                _event_callback(data)
            except Exception as e:
                print(f"[MC] 事件回调异常: {e}")


def _on_open(ws):
    _ws_ready.set()
    print("[MC] WebSocket 已连接")


def _on_close(ws, code, msg):
    _ws_ready.clear()
    # 静默，不再打印


def _on_error(ws, err):
    from core import state
    if state.get_state().shutdown_flag.is_set():
        return
    global _silent_err_count
    try:
        _silent_err_count
    except NameError:
        _silent_err_count = 0
    _silent_err_count += 1
    if _silent_err_count <= 2:
        print(f"[MC] bot.js 未连接，自动重试中（不刷屏）")


def ensure_connection():
    global _ws
    if not _HAS_WS:
        print("[MC] 未安装 websocket-client：pip install websocket-client")
        return False
    if _ws and _ws.sock and _ws.sock.connected:
        return True
    _ws_ready.clear()
    try:
        _ws = websocket.WebSocketApp(
            WS_URL,
            on_message=_on_message,
            on_open=_on_open,
            on_close=_on_close,
            on_error=_on_error,
        )
        t = threading.Thread(target=_ws.run_forever, daemon=True)
        t.start()
        return _ws_ready.wait(timeout=1.5)
    except Exception as e:
        print(f"[MC] 连接异常: {e}")
        return False


def _send(action, args=None, timeout=15):
    if not ensure_connection():
        return {"ok": False, "result": "WebSocket 未连接（bot.js 没启动？）"}
    cmd_id = str(uuid.uuid4())
    event = threading.Event()
    fut = {"event": event, "result": None}
    with _lock:
        _pending[cmd_id] = fut
    try:
        _ws.send(json.dumps({"id": cmd_id, "action": action, "args": args or {}}))
    except Exception as e:
        with _lock:
            _pending.pop(cmd_id, None)
        return {"ok": False, "result": f"发送失败: {e}"}
    if not event.wait(timeout):
        with _lock:
            _pending.pop(cmd_id, None)
        return {"ok": False, "result": "等待响应超时"}
    return fut["result"] if fut.get("result") else {"ok": False, "result": "无响应"}


# ==================== 基础工具 ====================
def mc_say(text):
    r = _send("say", {"text": text}, timeout=6)
    return r.get("result") or "发送失败"


def mc_follow(target):
    r = _send("follow", {"target": target}, timeout=6)
    return r.get("result") or "跟随失败"


def mc_come(target):
    r = _send("come", {"target": target}, timeout=6)
    return r.get("result") or "移动失败"


def mc_stop():
    r = _send("stop", timeout=6)
    return r.get("result") or "停止失败"


def mc_status():
    r = _send("status", timeout=6)
    return r.get("result") or "查询失败"


def mc_look():
    r = _send("look", timeout=6)
    return r.get("result") or "查询失败"


def mc_reconnect():
    r = _send("reconnect", timeout=6)
    return r.get("result") or "重连失败"


# ==================== 🆕 动作工具 ====================
def mc_mine(block_name, count=3):
    """挖指定方块，可能需要较长时间"""
    r = _send("mine", {"block_name": block_name, "count": count}, timeout=90)
    return r.get("result") or "挖矿失败"


def mc_attack():
    """攻击最近怪物，最长 15 秒"""
    r = _send("attack", {}, timeout=30)
    return r.get("result") or "攻击失败"


def mc_collect():
    """捡附近掉落物"""
    r = _send("collect", {}, timeout=40)
    return r.get("result") or "捡东西失败"


def mc_eat():
    """吃东西"""
    r = _send("eat", {}, timeout=15)
    return r.get("result") or "吃东西失败"


def mc_drop(item_name, count=1):
    """丢弃物品。count 可以是数字或 'all'"""
    r = _send("drop", {"item_name": item_name, "count": str(count)}, timeout=10)
    return r.get("result") or "丢弃失败"


def mc_inventory():
    """查看 Minecraft 里的背包"""
    r = _send("inventory", {}, timeout=8)
    return r.get("result") or "查询失败"