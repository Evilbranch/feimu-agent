"""电脑端 HTTP 服务 - 给微信端做大脑"""
import os
import re
import json
import time
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from core import state
from core.constants import DATA_DIR
from brain.streaming import ask_ai_streaming

HOST = "127.0.0.1"
PORT = 8765
PENDING_TTL = 600

_client = None
_provider = None
_history_ref = None
_session_start = [0.0]
# ★ 新增：弹幕专属的短期记忆，防止污染哥哥的聊天上下文

_danmu_history = []

def set_context(client, provider, history, session_start):
    global _client, _provider, _history_ref
    _client = client; _provider = provider; _history_ref = history; _session_start[0] = session_start

def _brain_ready():
    return _client is not None and _provider is not None and _history_ref is not None

def _cleanup_pending():
    s = state.get_state()
    with s.proactive_queue_lock:
        now = time.time()
        s.wechat_pending = [m for m in getattr(s, "wechat_pending", []) if now - m.get("ts", 0) < PENDING_TTL]

def _parse_query(query):
    result = {}
    if not query: return result
    for kv in query.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            result[k] = v
    return result

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError): pass

    def do_GET(self):
        try: state.get_state().last_wechat_ping_time = time.time()
        except Exception: pass
        full = self.path
        if "?" in full: path, query = full.split("?", 1)
        else: path, query = full, ""
        if path == "/health": self._send_json(200, {"ok": True, "brain_ready": _brain_ready(), "ts": time.time()}); return
        if path == "/pending":
            _cleanup_pending()
            s = state.get_state()
            with s.proactive_queue_lock:
                items = list(getattr(s, "wechat_pending", [])); s.wechat_pending = []
            self._send_json(200, {"items": items}); return
        if path == "/self/status": self._handle_self_status(); return
        if path == "/self/recent": self._handle_self_recent(query); return
        if path == "/self/diary": self._handle_self_diary(query); return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        try: state.get_state().last_wechat_ping_time = time.time()
        except Exception: pass
        
        path = self.path.split("?")[0]
        
        # 1. 读取请求数据
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw)
        except Exception as e: 
            self._send_json(400, {"error": f"bad json: {e}"}); return

        # ★ 2. 新增：处理 B 站弹幕的 POST 请求
        if path == "/danmu":
            text = str(data.get("text") or "").strip()
            uname = str(data.get("uname") or "观众").strip()
            if not text: 
                self._send_json(400, {"error": "empty text"}); return
            if not _brain_ready(): 
                self._send_json(503, {"error": "brain not ready"}); return

            try: 
                reply = _handle_danmu_message(text, uname)
            except Exception as e:
                print(f"[API] /danmu 处理异常: {e}")
                self._send_json(500, {"error": str(e)}); return
            self._send_json(200, {"reply": reply or ""})
            return
        # --------------------------------------------------

        # 3. 原来的微信 /chat 路由
        if path != "/chat": 
            self._send_json(404, {"error": "not found"}); return
        
        text = str(data.get("text") or "").strip()
        user_id = str(data.get("user_id") or "wechat").strip()
        image_path = data.get("image_path")

        if not text and not image_path: 
            self._send_json(400, {"error": "empty text"}); return
        if not _brain_ready(): 
            self._send_json(503, {"error": "brain not ready"}); return

        s = state.get_state()
        s.last_active_channel = "wechat"; s.last_active_channel_time = time.time()

        try: reply = _handle_wechat_message(text, image_path)
        except Exception as e:
            print(f"[API] /chat 处理异常: {e}")
            self._send_json(500, {"error": str(e)}); return
        self._send_json(200, {"reply": reply or ""})

    def _handle_self_status(self):
        try:
            from brain.inner_life import get_inner_log
            from brain.persona import get_current_state
            log = get_inner_log(limit=1); last = log[-1] if log else {}
            _s = state.get_state(); now = time.time(); last_min = int((now - getattr(_s, "last_interaction_time", 0)) / 60)
            try:
                pstate = get_current_state(); emotion = pstate.get("emotion", {}); drives = pstate.get("drives", {})
            except Exception: emotion = {}; drives = {}
            next_wake_in = None
            if last.get("ts") and last.get("next_wake"): next_wake_in = max(0, int(last["ts"] + last["next_wake"] - now))
            self._send_json(200, {"ok": True, "intent": last.get("intent", "idle"), "reason": last.get("reason", ""), "ts": last.get("ts", 0), "next_wake_in": next_wake_in, "wake_reason": last.get("wake_reason", ""), "emotion": {"label": emotion.get("label", "平静"), "valence": emotion.get("valence", 0.2), "arousal": emotion.get("arousal", 0.4)}, "drives": drives, "last_interaction_min": last_min, "now": now})
        except Exception as e: self._send_json(500, {"error": str(e)})

    def _handle_self_recent(self, query):
        q = _parse_query(query)
        try: limit = int(q.get("limit", "10"))
        except Exception: limit = 10
        limit = max(1, min(50, limit))
        try:
            from brain.inner_life import get_inner_log
            log = get_inner_log(limit=limit)
            items = [{"ts": e.get("ts", 0), "intent": e.get("intent", ""), "content": e.get("content", ""), "reason": e.get("reason", ""), "next_wake": e.get("next_wake", 0), "wake_reason": e.get("wake_reason", ""), "elapsed_min": e.get("elapsed_min", 0)} for e in log]
            self._send_json(200, {"ok": True, "items": items, "count": len(items)})
        except Exception as e: self._send_json(500, {"error": str(e)})

    def _handle_self_diary(self, query):
        q = _parse_query(query); target = q.get("date", "").strip()
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', target): target = time.strftime("%Y-%m-%d")
        try:
            from tools.diary import _load_diary
            diary = _load_diary()
            found = [d for d in diary if d.get("date") == target]
            if found: self._send_json(200, {"ok": True, "date": target, "exists": True, "content": found[0].get("summary", "")})
            else: self._send_json(200, {"ok": True, "date": target, "exists": False, "content": "", "note": "没有找到该日期的日记文件"})
        except Exception as e: self._send_json(500, {"error": str(e)})

def _handle_wechat_message(text, image_path=None):
    import queue
    s = state.get_state()
    now = time.time(); old = getattr(s, "last_interaction_time", 0)
    s.last_turn_gap = now - old if old > 0 else 0; s.last_interaction_time = now
    if s.mood_mgr:
        try: s.mood_mgr.update_from_message(text)
        except Exception: pass
    print(f"\n[API←微信] {text} (图片: {image_path})")
    
    q = queue.Queue()
    try:
        ask_ai_streaming(_client, _provider, _history_ref, text, _session_start[0], speaker="主人", relation="主人", source="owner", out_queue=q, image_path=image_path)
    except Exception as e: print(f"[API] 流式调用异常: {e}")

    parts = []
    while True:
        try:
            item = q.get(timeout=60)
            if item is None: break
            if isinstance(item, str): parts.append(item)
        except queue.Empty: break
    rep = "".join(parts).strip()
    if not rep: rep = "……"

    try:
        from brain.output_audit import audit_output, pick_safe_reply
        _suspicious, _reasons = audit_output(rep)
        if _suspicious: rep = pick_safe_reply()
    except Exception: pass

    print(f"[API→微信] {rep}")
    try:
        from brain.full_history import append as _fh_append
        _fh_append(text, rep, source="wechat")
    except Exception: pass

    try:
        from brain.episodic import judge_should_record, record_episode
        from brain.persona import get_current_state
        _should, _etype, _content, _imp = judge_should_record(_client, _provider, text, rep)
        if _should and _content:
            _st = get_current_state()
            record_episode(content=_content, event_type=_etype, entities=[], self_emotion=_st.get("emotion"), self_intent="speak", self_role="responder", channel="wechat", raw_context=f"哥哥：{text}\n我：{rep}", novelty=_imp)
            print(f"[L2] 微信端记录: type={_etype} content={_content[:40]}")
    except Exception as e: print(f"[L2-微信] 异常: {e}")

    try:
        from brain.self_mood import apply_own_speech_impact
        _self_last = ""
        for m in reversed(_history_ref):
            if m.get("role") == "assistant" and m.get("content"): _self_last = m["content"].strip(); break
        if _self_last: apply_own_speech_impact(_self_last, source="wechat")
    except Exception as e: pass

    try:
        from brain.self_reflect import reflect_on_own_speech
        _ = reflect_on_own_speech(_client, _provider, rep, source="wechat")
    except Exception as e: pass

    return rep

def _handle_danmu_message(text, uname):
    global _danmu_history
    import queue
    s = state.get_state()
    
    print(f"\n[API←弹幕] {uname}: {text}")
    
    # 为了让大模型分清是谁在说话，把名字也放进对话内容里
    ui = f"观众【{uname}】说：{text}"
    
    q = queue.Queue()
    try:
        # 核心：source="audience" 强制走观众/直播模式
        # 传入专属的 _danmu_history，不污染哥哥的 _history_ref
        ask_ai_streaming(
            _client, _provider, _danmu_history, ui, _session_start[0], 
            speaker=uname, relation="观众", 
            source="audience", out_queue=q, image_path=None
        )
    except Exception as e: 
        print(f"[API] 弹幕流式调用异常: {e}")

    parts = []
    while True:
        try:
            item = q.get(timeout=30) # 弹幕等待时间短一点
            if item is None: break
            if isinstance(item, str): parts.append(item)
        except queue.Empty: break
    rep = "".join(parts).strip()
    if not rep: rep = "……"

    # 基本的安全审计
    try:
        from brain.output_audit import audit_output, pick_safe_reply
        _suspicious, _reasons = audit_output(rep)
        if _suspicious: rep = pick_safe_reply()
    except Exception: pass

    print(f"[API→弹幕] 绯木回复 {uname}: {rep}")
    
    # ★ 新增：让弹幕回复也通过 TTS 念出来，并驱动 VTS 皮套
    try:
        import asyncio
        from voice.player import speak
        from voice import vmc
        
        # 1. 判断情绪，驱动 VTS 表情
        emotion = vmc.detect_emotion(rep)
        
        # 2. 播放声音（注意：这里使用 asyncio.run 会阻塞当前请求，直到她说完）
        # 如果不想阻塞弹幕请求，可以用 threading.Thread(target=lambda: asyncio.run(speak(rep, emotion))).start()
        print(f"[弹幕TTS] 正在发声: {rep[:20]}...")
        asyncio.run(speak(rep, emotion))
        
    except Exception as e:
        print(f"[弹幕TTS] 发声异常: {e}")
    
    # 为了防止弹幕历史太长导致显存爆掉，最多只保留最近 6 条（3轮对话）
    if len(_danmu_history) > 6:
        _danmu_history[:] = _danmu_history[-6:]

    # 弹幕回复不写入 L2，也不触发内部反思，节省算力
    return rep

def start():
    try: server = ThreadingHTTPServer((HOST, PORT), _Handler)
    except OSError as e: print(f"[API] ⚠️ 启动失败（端口 {PORT} 可能被占）: {e}"); return None
    def _run():
        print(f"[API] HTTP 服务已启动: http://{HOST}:{PORT}")
        server.serve_forever()
    threading.Thread(target=_run, daemon=True).start()
    return server