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

# ★ 全局游戏状态缓存
MC_CURRENT_STATE = "哥哥当前处于正常状态。"

_last_event_time = {}
_event_lock = threading.Lock()

def _check_throttle(event_type, cooldown=5):
    with _event_lock:
        now = time.time()
        if now - _last_event_time.get(event_type, 0) < cooldown:
            return False
        _last_event_time[event_type] = now
        return True

def set_event_callback(cb):
    global _event_callback
    _event_callback = cb

    def _connect_loop():
        from core import state
        s = state.get_state()
        while not s.shutdown_flag.is_set():
            try:
                if not ensure_connection():
                    time.sleep(10)
                    continue
                while (not s.shutdown_flag.is_set() and _ws and _ws.sock and _ws.sock.connected):
                    time.sleep(2)
                if not s.shutdown_flag.is_set():
                    time.sleep(5)
            except Exception:
                time.sleep(10)

    threading.Thread(target=_connect_loop, daemon=True).start()

def _on_message(ws, msg):
    global MC_CURRENT_STATE
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
        ev_type = data.get("event")
        
        # 1. 玩家掉血/残血警报
        if ev_type == "health":
            hp = data.get("health", 20)
            if hp <= 8 and _check_throttle("health", 8):
                print(f"\n[MC遥测] 🩸 哥哥残血了！当前血量: {hp}")
                try:
                    from brain.urge import add_urge
                    add_urge(content=f"啊啊啊哥你快跑！你只有 {hp} 滴血了，快吃东西躲起来！", 
                             reason="MC残血警报", base_pressure=1.0, source="mc")
                except Exception: pass
                
        # 2. 玩家死亡
        elif ev_type == "death":
            if _check_throttle("death", 10):
                print(f"\n[MC遥测] 💀 哥哥死亡了！")
                try:
                    from brain.urge import add_urge
                    add_urge(content="天呐！哥你怎么死了？刚刚发生什么了？", 
                             reason="MC死亡", base_pressure=1.0, source="mc")
                except Exception: pass

        # 3. 捡到物品
        elif ev_type == "pickup":
            item = str(data.get("item", ""))
            _GOOD_ITEMS = ["钻石", "下界合金", "金锭", "附魔", "绿宝石", "远古残骸", "铁锭"]
            if any(w in item for w in _GOOD_ITEMS) and _check_throttle("pickup", 5):
                print(f"\n[MC遥测] 💎 哥哥捡到好东西了: {item}")
                try:
                    from brain.urge import add_urge
                    add_urge(content=f"哇塞！哥你居然搞到{item}了！快让我看看！", 
                             reason="MC拾取宝物", base_pressure=0.8, source="mc")
                except Exception: pass

        # 4. 附近有敌对生物
        elif ev_type == "danger":
            mob = data.get("mob", "怪物")
            if _check_throttle("danger", 15):
                print(f"\n[MC遥测] ⚠️ 附近出现危险: {mob}")
                try:
                    from brain.urge import add_urge
                    add_urge(content=f"哥！你旁边有{mob}！小心点，别被阴了！", 
                             reason="MC危险预警", base_pressure=0.6, source="mc")
                except Exception: pass

        # 5. 接收状态并缓存（上帝视角：不触发主动说话，只更新大脑）
        elif ev_type == "status_update":
            d = data.get("data", {})
            pos = d.get("pos", {})
            mobs_str = "、".join(d.get("mobs", [])) if d.get("mobs") else "没看到怪物"
            time_str = "夜晚" if d.get("isNight") else "白天"
            
            env = d.get("env", {})
            env_special = "、".join(env.get("special", [])) if env.get("special") else "没看到什么特别的东西"
            
            MC_CURRENT_STATE = (
                f"哥哥当前坐标 (X:{pos.get('x')}, Y:{pos.get('y')}, Z:{pos.get('z')})，"
                f"时间：{time_str}，天气：{d.get('weather')}，手里拿着：{d.get('heldItem')}。\n"
                f"哥哥脚踩在【{env.get('foot')}】上，正前方是【{env.get('front')}】。\n"
                f"周围8格内：{mobs_str}。附近比较显眼的方块有：{env_special}。"
            )
            return

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

def _on_error(ws, err):
    pass

def ensure_connection():
    global _ws
    if not _HAS_WS: return False
    if _ws and _ws.sock and _ws.sock.connected: return True
    _ws_ready.clear()
    try:
        _ws = websocket.WebSocketApp(
            WS_URL, on_message=_on_message, 
            on_open=_on_open, on_close=_on_close, on_error=_on_error
        )
        t = threading.Thread(target=_ws.run_forever, daemon=True)
        t.start()
        return _ws_ready.wait(timeout=1.5)
    except Exception:
        return False

def _send(action, args=None, timeout=15):
    if not ensure_connection():
        return {"ok": False, "result": "WebSocket 未连接"}
    cmd_id = str(uuid.uuid4())
    event = threading.Event()
    fut = {"event": event, "result": None}
    with _lock:
        _pending[cmd_id] = fut
    try:
        _ws.send(json.dumps({"id": cmd_id, "action": action, "args": args or {}}))
    except Exception as e:
        return {"ok": False, "result": f"发送失败: {e}"}
    if not event.wait(timeout):
        return {"ok": False, "result": "等待响应超时"}
    return fut["result"] if fut.get("result") else {"ok": False, "result": "无响应"}

def mc_say(text): return _send("say", {"text": text}, timeout=6).get("result") or "发送失败"
def mc_follow(target): return _send("follow", {"target": target}, timeout=6).get("result") or "跟随失败"
def mc_come(target): return _send("come", {"target": target}, timeout=6).get("result") or "移动失败"
def mc_stop(): return _send("stop", timeout=6).get("result") or "停止失败"
def mc_status(): return _send("status", timeout=6).get("result") or "查询失败"
def mc_look(): return _send("look", timeout=6).get("result") or "查询失败"
def mc_reconnect(): return _send("reconnect", timeout=6).get("result") or "重连失败"
def mc_mine(block_name, count=3): return _send("mine", {"block_name": block_name, "count": count}, timeout=90).get("result") or "挖矿失败"
def mc_attack(): return _send("attack", {}, timeout=30).get("result") or "攻击失败"
def mc_collect(): return _send("collect", {}, timeout=40).get("result") or "捡东西失败"
def mc_eat(): return _send("eat", {}, timeout=15).get("result") or "吃东西失败"
def mc_drop(item_name, count=1): return _send("drop", {"item_name": item_name, "count": str(count)}, timeout=10).get("result") or "丢弃失败"
def mc_inventory(): return _send("inventory", {}, timeout=8).get("result") or "查询失败"