"""绯木微信端 - 纯转发（大脑在电脑端）

微信端不做任何 AI 处理：
- 收微信消息 → POST 电脑端 /chat → 拿回复 → 发回微信
- 电脑端离线 → 消息存 outbox + 回"她不在"
- 后台线程补发 outbox + 轮询主动消息
- 命令拦截（/status /recent /diary）：本地处理，不走大脑
"""
import os
import sys
import time
import json
import random
import threading
import atexit
import signal as _signal

import requests

from core import constants
from core.logger import mark_running, mark_clean_exit
from wechat_commands import try_handle

try:
    from weixin_ilink import WeixinBot
except ImportError:
    print("[错误] 请先安装：pip install \"weixin-ilink[qr]\"")
    sys.exit(1)


BRAIN_URL = "http://127.0.0.1:8765"
WECHAT_DATA_DIR = os.path.join(constants.SCRIPT_DIR, "data_wechat")
os.makedirs(WECHAT_DATA_DIR, exist_ok=True)
OUTBOX_FILE = os.path.join(WECHAT_DATA_DIR, "wechat_outbox.json")

OUTBOX_MAX = 50
OUTBOX_TTL = 24 * 3600

_last_user_id = [None]
_context_tokens = {}   # user_id -> context_token（主动发送必需）
_msg_lock = threading.Lock()
_outbox_lock = threading.Lock()

_OFFLINE_HINTS = [
    "（她好像偷吃去了，等会儿会回来找你~）",
    "（她溜出去玩了吧，晚点应该就回来了 (´･ω･`)）",
    "（她可能没听见呢……再等等，回来就找你）",
    "（她不在电脑边，我先把消息记着了 📝）",
]

_RESEND_PREFIX = ["刚刚没看见你消息，抱歉…", "还有你这条…", "另外…"]


# ══════════════════════════════════════════════════════════════
# 电脑端 HTTP 调用
# ══════════════════════════════════════════════════════════════
def _health():
    """返回 (online, brain_ready)"""
    try:
        r = requests.get(f"{BRAIN_URL}/health", timeout=3)
        if r.status_code != 200:
            return False, False
        d = r.json()
        return True, bool(d.get("brain_ready"))
    except Exception:
        return False, False


def _post_chat(text, user_id):
    """转发消息给电脑端，返回 reply 或 None"""
    try:
        r = requests.post(
            f"{BRAIN_URL}/chat",
            json={"text": text, "user_id": user_id},
            timeout=180,
        )
        if r.status_code != 200:
            print(f"[微信→大脑] HTTP {r.status_code}: {r.text[:100]}")
            return None
        d = r.json()
        return d.get("reply") or "……"
    except Exception as e:
        print(f"[微信→大脑] 异常: {e}")
        return None


def _fetch_pending():
    """拉主动消息"""
    try:
        r = requests.get(f"{BRAIN_URL}/pending", timeout=3)
        if r.status_code != 200:
            return []
        return r.json().get("items", [])
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════
# 发送（带 context_token）
# ══════════════════════════════════════════════════════════════
def _send_to_user(bot, user_id, text):
    """统一的发送函数：带 context_token"""
    if not user_id or user_id == "unknown":
        print(f"[send] 无效 user_id: {user_id}")
        return False
    ctx = _context_tokens.get(user_id)
    if not ctx:
        print(f"[send] 没有 {user_id} 的 context_token，无法发送")
        return False
    try:
        bot.send_text(to=user_id, text=text, context_token=ctx)
        return True
    except Exception as e:
        print(f"[send] 发送失败: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# Outbox（离线缓存）
# ══════════════════════════════════════════════════════════════
def _load_outbox():
    with _outbox_lock:
        if not os.path.exists(OUTBOX_FILE):
            return []
        try:
            with open(OUTBOX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []


def _save_outbox(items):
    with _outbox_lock:
        now = time.time()
        items = [m for m in items if now - m.get("ts", 0) < OUTBOX_TTL]
        items = items[-OUTBOX_MAX:]
        try:
            with open(OUTBOX_FILE, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[outbox] 保存失败: {e}")


def _append_outbox(text, user_id):
    items = _load_outbox()
    items.append({"ts": time.time(), "user_id": user_id, "text": text})
    _save_outbox(items)


def _pop_outbox_front():
    items = _load_outbox()
    if not items:
        return None
    item = items[0]
    _save_outbox(items[1:])
    return item


def _requeue_outbox_front(item):
    items = _load_outbox()
    items.insert(0, item)
    _save_outbox(items)


# ══════════════════════════════════════════════════════════════
# 后台线程
# ══════════════════════════════════════════════════════════════
def _worker_resend(bot, stop_event):
    """每 30 秒检测电脑端；在线且 brain_ready 则逐条补发（最多 5 条）"""
    while not stop_event.is_set():
        stop_event.wait(30)
        if stop_event.is_set():
            break

        ok, ready = _health()
        if not ok or not ready:
            continue

        idx = 0
        while True:
            if idx >= 5:
                remain = _load_outbox()
                if remain:
                    print(f"[补发] 已达 5 条上限，丢弃剩余 {len(remain)} 条旧消息")
                    _save_outbox([])
                break

            item = _pop_outbox_front()
            if not item:
                break
            text = item.get("text", "")
            user_id = item.get("user_id") or _last_user_id[0]
            if not user_id:
                continue

            reply = _post_chat(text, user_id)
            if reply is None:
                _requeue_outbox_front(item)
                break

            prefix = _RESEND_PREFIX[min(idx, 2)]
            full = f"{prefix}\n{reply}"
            idx += 1
            if _send_to_user(bot, user_id, full):
                print(f"[补发] {full[:60]}")
            else:
                _requeue_outbox_front(item)
                break
            time.sleep(2.5)


def _worker_pending(bot, stop_event):
    """每 3 秒拉主动消息"""
    while not stop_event.is_set():
        stop_event.wait(3)
        if stop_event.is_set():
            break

        items = _fetch_pending()
        if not items:
            continue

        user_id = _last_user_id[0]
        if not user_id:
            continue

        for item in items:
            msg = item.get("msg") or ""
            if not msg:
                continue
            if _send_to_user(bot, user_id, msg):
                print(f"[主动] 已推微信: {msg[:40]}")
            else:
                print(f"[主动] 推送失败（无 token？）: {msg[:40]}")
            time.sleep(0.5)


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════
def main():
    print("=" * 50)
    print("  绯木 - 微信端（纯转发）")
    print("=" * 50)

    mark_running()

    creds_path = os.path.join(constants.SCRIPT_DIR, "creds.json")
    if os.path.exists(creds_path):
        print(f"[微信] 使用已有凭据：{creds_path}")
        bot = WeixinBot(credentials_file=creds_path)
    else:
        print("[微信] 首次登录，请扫码...")
        bot = WeixinBot.from_login(save_to=creds_path)

    ok, ready = False, False
    for i in range(10):
        ok, ready = _health()
        if ok and ready:
            break
        print(f"[大脑] 探测中... ({i+1}/10)")
        time.sleep(3)

    if ok and ready:
        print(f"[大脑] ✅ 电脑端在线 ({BRAIN_URL})")
    elif ok:
        print(f"[大脑] ⚠️ 电脑端在线但大脑未就绪（后台会自动重试）")
    else:
        print(f"[大脑] ℹ️ 电脑端还没起来，消息先缓存，上线后自动补发")

    stop_event = threading.Event()
    threading.Thread(target=_worker_resend, args=(bot, stop_event), daemon=True).start()
    threading.Thread(target=_worker_pending, args=(bot, stop_event), daemon=True).start()
    print("[后台] 补发线程 + 主动消息轮询已启动")

    @bot.on_text
    def handle_text(msg):
        if not _msg_lock.acquire(blocking=False):
            return
        try:
            user_id = getattr(msg, "from_user", None) or "unknown"
            ctx_token = getattr(msg, "context_token", None)
            if user_id and user_id != "unknown" and ctx_token:
                _context_tokens[user_id] = ctx_token
            _last_user_id[0] = user_id

            ui = (msg.text or "").strip()
            if not ui:
                return
            print(f"\n[微信←] <{user_id}> {ui}")

            # ══════════════════════════════════════════════════════
            # 命令拦截（/status /recent /diary）—— 本地处理，不走大脑
            # ══════════════════════════════════════════════════════
            cmd_reply, handled = try_handle(ui)
            if handled:
                print(f"[命令] 命中：{ui[:20]}")
                try:
                    msg.reply_text(cmd_reply)
                    print(f"[微信→] {cmd_reply[:60]}")
                except Exception as e:
                    print(f"[命令] 回复失败: {e}")
                return
            # ══════════════════════════════════════════════════════

            ok, ready = _health()
            if not ok or not ready:
                _append_outbox(ui, user_id)
                hint = random.choice(_OFFLINE_HINTS)
                try:
                    msg.reply_text(hint)
                except Exception:
                    pass
                print(f"[离线] 已缓存，回提示: {hint}")
                return

            reply = _post_chat(ui, user_id)
            if reply is None:
                _append_outbox(ui, user_id)
                hint = random.choice(_OFFLINE_HINTS)
                try:
                    msg.reply_text(hint)
                except Exception:
                    pass
                return

            try:
                msg.reply_text(reply)
                print(f"[微信→] {reply[:60]}")
            except Exception as e:
                print(f"[微信] 回复失败: {e}")
        finally:
            _msg_lock.release()

    @bot.on_image
    def handle_image(msg):
        try:
            msg.reply_text("我收到图片啦，不过我还看不懂图片内容呢~")
        except:
            pass

    @bot.on_voice
    def handle_voice(msg):
        try:
            msg.reply_text("我收到语音啦，不过我还听不懂语音呢~")
        except:
            pass

    print("\n" + "=" * 50)
    print("  微信端已就绪，等待消息...")
    print("=" * 50 + "\n")

    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n[微信] 收到 Ctrl+C")
    except Exception as e:
        print(f"\n[微信] run 异常: {e}")
    finally:
        stop_event.set()
        mark_clean_exit()
        print("\n绯木微信端已退出~")


if __name__ == "__main__":
    def _onexit(): mark_clean_exit()
    atexit.register(_onexit)
    def _sig(sig, frame):
        print("\n[收到退出信号]")
        sys.exit(0)
    try:
        _signal.signal(_signal.SIGINT, _sig)
        _signal.signal(_signal.SIGTERM, _sig)
    except:
        pass
    main()