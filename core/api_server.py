"""电脑端 HTTP 服务 - 给微信端做大脑

三个接口：
- POST /chat    : 微信端发来的消息，走完整对话流程，返回回复
- GET  /pending : 微信端拉主动消息（她主动想说的）
- GET  /health  : 探活 + brain_ready

只绑 127.0.0.1，不对外暴露。
"""
import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from core import state

HOST = "127.0.0.1"
PORT = 8765

# 主动消息保留时长（秒）——超时自动清理
PENDING_TTL = 600

# 全局（被 my_ai.py 启动时注入）
_client = None
_provider = None
_history_ref = None  # 引用主循环的 history 列表
_session_start = [0.0]


def set_context(client, provider, history, session_start):
    """my_ai.py 启动时调用，注入上下文"""
    global _client, _provider, _history_ref
    _client = client
    _provider = provider
    _history_ref = history
    _session_start[0] = session_start


def _brain_ready():
    """大脑是否就绪（LLM client + history 都拿到了）"""
    return _client is not None and _provider is not None and _history_ref is not None


def _cleanup_pending():
    """清理超时的主动消息（每轮请求前调用一次，轻量）"""
    s = state.get_state()
    with s.proactive_queue_lock:
        now = time.time()
        s.wechat_pending = [
            m for m in getattr(s, "wechat_pending", [])
            if now - m.get("ts", 0) < PENDING_TTL
        ]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 静音默认日志
        pass

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/health":
            self._send_json(200, {
                "ok": True,
                "brain_ready": _brain_ready(),
                "ts": time.time(),
            })
            return

        if path == "/pending":
            _cleanup_pending()
            s = state.get_state()
            with s.proactive_queue_lock:
                items = list(getattr(s, "wechat_pending", []))
                s.wechat_pending = []
            self._send_json(200, {"items": items})
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]

        if path != "/chat":
            self._send_json(404, {"error": "not found"})
            return

        # 读 body
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw)
        except Exception as e:
            self._send_json(400, {"error": f"bad json: {e}"})
            return

        text = (data.get("text") or "").strip()
        user_id = (data.get("user_id") or "wechat").strip()
        if not text:
            self._send_json(400, {"error": "empty text"})
            return

        if not _brain_ready():
            self._send_json(503, {"error": "brain not ready"})
            return

        # 标记活跃渠道为 wechat
        s = state.get_state()
        s.last_active_channel = "wechat"
        s.last_active_channel_time = time.time()

        # 走完整对话流程
        try:
            reply = _handle_wechat_message(text)
        except Exception as e:
            print(f"[API] /chat 处理异常: {e}")
            self._send_json(500, {"error": str(e)})
            return

        self._send_json(200, {"reply": reply or ""})

    def do_PUT(self):
        self._send_json(404, {"error": "not found"})


def _handle_wechat_message(text):
    """微信消息的完整处理流程。

    注意：detect_event_from_text / bump_turn 由 ask_ai 内部自动调用，
    这里不要重复调用。
    """
    from brain.llm import ask_ai

    s = state.get_state()

    # 时间感（ask_ai 内部会读 s.last_turn_gap）
    now = time.time()
    old = getattr(s, "last_interaction_time", 0)
    s.last_turn_gap = now - old if old > 0 else 0
    s.last_interaction_time = now

    # 情绪更新
    if s.mood_mgr:
        try:
            s.mood_mgr.update_from_message(text)
        except Exception:
            pass

    print(f"\n[API←微信] {text}")

    rep = ask_ai(
        _client, _provider, _history_ref, text, _session_start[0],
        use_tools=True,
        speaker="主人",
        relation="主人",
        source="wechat",
    )

    if not rep:
        rep = "……"

    print(f"[API→微信] {rep}")

    # L2：她自己的话影响情绪
    try:
        from brain.self_mood import apply_own_speech_impact
        _self_last = ""
        for m in reversed(_history_ref):
            if m.get("role") == "assistant" and m.get("content"):
                _self_last = m["content"].strip()
                break
        if _self_last:
            apply_own_speech_impact(_self_last, source="wechat")
    except Exception as e:
        print(f"[API-L2] 异常: {e}")

    # L4：自我反思 → 塞 pending，微信端会拉到
    try:
        from brain.self_reflect import reflect_on_own_speech
        reflect_text = reflect_on_own_speech(_client, _provider, rep, source="wechat")
        if reflect_text:
            with s.proactive_queue_lock:
                s.wechat_pending.append({
                    "ts": time.time(),
                    "msg": reflect_text,
                    "type": "self_reflect",
                })
            print(f"[API] L4 自语入队: {reflect_text[:40]}")
    except Exception as e:
        print(f"[API-L4] 异常: {e}")

    return rep


def start():
    """启动 HTTP 服务（后台线程）"""
    try:
        server = HTTPServer((HOST, PORT), _Handler)
    except OSError as e:
        print(f"[API] ⚠️ 启动失败（端口 {PORT} 可能被占）: {e}")
        return None

    def _run():
        print(f"[API] HTTP 服务已启动: http://{HOST}:{PORT}")
        server.serve_forever()

    threading.Thread(target=_run, daemon=True).start()
    return server