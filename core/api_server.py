"""电脑端 HTTP 服务 - 给微信端做大脑

接口：
- POST /chat          : 微信端发来的消息，走完整对话流程，返回回复
- GET  /pending       : 微信端拉主动消息（她主动想说的）
- GET  /health        : 探活 + brain_ready
- GET  /self/status   : 她的当前状态（intent + 情绪 + 驱力）
- GET  /self/recent   : 她最近的内在活动（?limit=N，默认 10）
- GET  /self/diary    : 她今天的日记（?date=YYYY-MM-DD，默认今天）

只绑 127.0.0.1，不对外暴露。
"""
import os
import re
import json
import time
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from core import state
from core.constants import DATA_DIR

HOST = "127.0.0.1"
PORT = 8765

PENDING_TTL = 600

_client = None
_provider = None
_history_ref = None
_session_start = [0.0]


def set_context(client, provider, history, session_start):
    global _client, _provider, _history_ref
    _client = client
    _provider = provider
    _history_ref = history
    _session_start[0] = session_start


def _brain_ready():
    return _client is not None and _provider is not None and _history_ref is not None


def _cleanup_pending():
    s = state.get_state()
    with s.proactive_queue_lock:
        now = time.time()
        s.wechat_pending = [
            m for m in getattr(s, "wechat_pending", [])
            if now - m.get("ts", 0) < PENDING_TTL
        ]


def _parse_query(query):
    result = {}
    if not query:
        return result
    for kv in query.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            result[k] = v
    return result


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        full = self.path
        if "?" in full:
            path, query = full.split("?", 1)
        else:
            path, query = full, ""

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

        if path == "/self/status":
            self._handle_self_status()
            return

        if path == "/self/recent":
            self._handle_self_recent(query)
            return

        if path == "/self/diary":
            self._handle_self_diary(query)
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]

        if path != "/chat":
            self._send_json(404, {"error": "not found"})
            return

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

        s = state.get_state()
        s.last_active_channel = "wechat"
        s.last_active_channel_time = time.time()

        try:
            reply = _handle_wechat_message(text)
        except Exception as e:
            print(f"[API] /chat 处理异常: {e}")
            self._send_json(500, {"error": str(e)})
            return

        self._send_json(200, {"reply": reply or ""})

    def do_PUT(self):
        self._send_json(404, {"error": "not found"})

    def _handle_self_status(self):
        try:
            from brain.inner_life import get_inner_log
            from brain.persona import get_current_state

            log = get_inner_log(limit=1)
            last = log[-1] if log else {}

            _s = state.get_state()
            now = time.time()
            last_min = int((now - getattr(_s, "last_interaction_time", 0)) / 60)

            try:
                pstate = get_current_state()
                emotion = pstate.get("emotion", {})
                drives = pstate.get("drives", {})
            except Exception:
                emotion = {}
                drives = {}

            next_wake_in = None
            if last.get("ts") and last.get("next_wake"):
                next_wake_in = max(0, int(last["ts"] + last["next_wake"] - now))

            self._send_json(200, {
                "ok": True,
                "intent": last.get("intent", "idle"),
                "reason": last.get("reason", ""),
                "ts": last.get("ts", 0),
                "next_wake_in": next_wake_in,
                "wake_reason": last.get("wake_reason", ""),
                "emotion": {
                    "label": emotion.get("label", "平静"),
                    "valence": emotion.get("valence", 0.2),
                    "arousal": emotion.get("arousal", 0.4),
                },
                "drives": drives,
                "last_interaction_min": last_min,
                "now": now,
            })
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_self_recent(self, query):
        q = _parse_query(query)
        try:
            limit = int(q.get("limit", "10"))
        except Exception:
            limit = 10
        limit = max(1, min(50, limit))

        try:
            from brain.inner_life import get_inner_log
            log = get_inner_log(limit=limit)
            items = []
            for e in log:
                items.append({
                    "ts": e.get("ts", 0),
                    "intent": e.get("intent", ""),
                    "content": e.get("content", ""),
                    "reason": e.get("reason", ""),
                    "next_wake": e.get("next_wake", 0),
                    "wake_reason": e.get("wake_reason", ""),
                    "elapsed_min": e.get("elapsed_min", 0),
                })
            self._send_json(200, {
                "ok": True,
                "items": items,
                "count": len(items),
            })
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_self_diary(self, query):
        q = _parse_query(query)
        target = q.get("date", "").strip()
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', target):
            target = time.strftime("%Y-%m-%d")

        candidates = [
            os.path.join(DATA_DIR, "diary", f"{target}.txt"),
            os.path.join(DATA_DIR, "diary", f"{target}.md"),
            os.path.join(DATA_DIR, "diary", f"{target}.json"),
            os.path.join(DATA_DIR, "diaries", f"{target}.txt"),
            os.path.join(DATA_DIR, "diaries", f"{target}.md"),
            os.path.join(DATA_DIR, f"diary_{target}.txt"),
            os.path.join(DATA_DIR, f"diary_{target}.md"),
        ]
        bulk_candidates = [
            os.path.join(DATA_DIR, "diary.json"),
            os.path.join(DATA_DIR, "diaries.json"),
        ]

        try:
            for path in candidates:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if path.endswith(".json"):
                        try:
                            content = json.loads(content)
                        except Exception:
                            pass
                    self._send_json(200, {
                        "ok": True,
                        "date": target,
                        "exists": True,
                        "source": os.path.basename(path),
                        "content": content,
                    })
                    return

            for path in bulk_candidates:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and target in data:
                        self._send_json(200, {
                            "ok": True,
                            "date": target,
                            "exists": True,
                            "source": os.path.basename(path),
                            "content": data[target],
                        })
                        return

            self._send_json(200, {
                "ok": True,
                "date": target,
                "exists": False,
                "content": "",
                "note": "没有找到该日期的日记文件",
            })
        except Exception as e:
            self._send_json(500, {"error": str(e)})


def _handle_wechat_message(text):
    """微信消息的完整处理流程。

    注意：detect_event_from_text / bump_turn 由 ask_ai 内部自动调用，
    这里不要重复调用。
    """
    from brain.llm import ask_ai

    s = state.get_state()

    now = time.time()
    old = getattr(s, "last_interaction_time", 0)
    s.last_turn_gap = now - old if old > 0 else 0
    s.last_interaction_time = now

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
        source="owner",   # 微信端走主人人格
    )

    if not rep:
        rep = "……"

    try:
        from brain.output_audit import audit_output, pick_safe_reply
        _suspicious, _reasons = audit_output(rep)
        if _suspicious:
            print(f"[审计] 编造嫌疑: {_reasons} | 原文: {rep[:60]}")
            rep = pick_safe_reply()
    except Exception as e:
        print(f"[审计] 异常: {e}")

    print(f"[API→微信] {rep}")

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

    try:
        from brain.self_reflect import reflect_on_own_speech
        _ = reflect_on_own_speech(_client, _provider, rep, source="wechat")
    except Exception as e:
        print(f"[API-L4] 异常: {e}")

    return rep


def start():
    try:
        server = ThreadingHTTPServer((HOST, PORT), _Handler)
    except OSError as e:
        print(f"[API] ⚠️ 启动失败（端口 {PORT} 可能被占）: {e}")
        return None

    def _run():
        print(f"[API] HTTP 服务已启动: http://{HOST}:{PORT}")
        server.serve_forever()

    threading.Thread(target=_run, daemon=True).start()
    return server