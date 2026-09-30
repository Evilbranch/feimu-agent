"""绯木微信端 - 复用主系统所有能力（记忆、人格、内在循环）"""
import os
import sys
import time
import json
import threading
import atexit
import signal as _signal

from core import state, constants
from core.logger import logger, mark_running, mark_clean_exit
from brain.memory import RAGMemory, load_history, save_history, set_slot
from brain.mood import MoodManager
from brain.llm import ask_ai
from brain.persona import set_mode as _set_mode, decay_loop
from brain.inner_life import inner_life_loop
from openai import OpenAI

try:
    from weixin_ilink import WeixinBot
except ImportError:
    print("[错误] 请先安装：pip install \"weixin-ilink[qr]\"")
    sys.exit(1)


def load_config():
    with open(constants.CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def build_client(p):
    return OpenAI(api_key=p["api_key"], base_url=p["base_url"])


# 全局：最近对话的人（用于主动消息）
_last_user_id = [None]


def main():
    print("=" * 50)
    print("  绯木 - 微信端")
    print("=" * 50)

    s = state.get_state()
    mark_running()

    _set_mode("master")
    set_slot("owner")

    config = load_config()
    current = config.get("current_provider")
    provider = config["providers"].get(current)
    if not provider:
        print("[错误] config.json 中没有 current_provider")
        sys.exit(1)
    client = build_client(provider)
    print(f"🧠 大脑：{provider.get('name')} [{provider.get('model')}]")

    # RAG / Mood
    s.rag = RAGMemory()
    s.mood_mgr = MoodManager()
    print("[RAG] 后台加载中（30~60 秒）...")
    s.rag.start_background_load()

    history = load_history(slot="owner")
    print(f"[记忆] 已加载 {len(history)} 条短期记忆")
    session_start = time.time()

    # 后台线程
    threading.Thread(target=decay_loop, daemon=True).start()
    print("[人格] 衰减循环已启动")

    threading.Thread(
        target=inner_life_loop,
        args=(client, provider),
        daemon=True
    ).start()
    print("[内在] 生活循环已启动")


    # 🆕 元认知观测器
    from brain.metacognition import metacognition_loop
    threading.Thread(
        target=metacognition_loop,
        args=(client, provider),
        daemon=True
    ).start()
    print("[元认知] 观测器已启动")

    # 消费 proactive_queue：她主动想说话 → 发给最近对话的人
    def proactive_consumer():
        while not s.shutdown_flag.is_set():
            time.sleep(2)
            try:
                with s.proactive_queue_lock:
                    if not s.proactive_queue:
                        continue
                    if not _last_user_id[0]:
                        # 没人聊过，丢弃
                        s.proactive_queue.clear()
                        continue
                    item = s.proactive_queue.pop(0)
                    pro_msg = item["msg"]
                print(f"\n绯木（主动）：{pro_msg}")
                history.append({"role": "assistant", "content": pro_msg})
                save_history(history, slot="owner")
                try:
                    bot.send_text(to=_last_user_id[0], text=pro_msg)
                    print(f"[微信] 主动消息已发送")
                except Exception as e:
                    print(f"[微信] 主动发送失败: {e}")
            except Exception as e:
                print(f"[主动消费] 异常: {e}")

    threading.Thread(target=proactive_consumer, daemon=True).start()

    # 初始化 bot
    creds_path = os.path.join(constants.SCRIPT_DIR, "creds.json")
    if os.path.exists(creds_path):
        print(f"[微信] 使用已有凭据：{creds_path}")
        bot = WeixinBot(credentials_file=creds_path)
    else:
        print("[微信] 首次登录，请扫码...")
        bot = WeixinBot.from_login(save_to=creds_path)

    # 回调：收到文本消息
    @bot.on_text
    def handle_text(msg):
        try:
            user_id = getattr(msg, "sender", None) or getattr(msg, "user_id", None) or getattr(msg, "from_user", None) or "unknown"
            _last_user_id[0] = user_id

            ui = (msg.text or "").strip()
            if not ui:
                return
            print(f"\n[微信] <{user_id}> {ui}")

            s.last_interaction_time = time.time()
            s.mood_mgr.update_from_message(ui)

            print("绯木：思考中...", end="\r")
            rep = ask_ai(
                client, provider, history, ui, session_start,
                use_tools=False,        # 微信端暂不启用工具
                speaker="主人",
                relation="主人",
                source="owner",
            )
            if not rep:
                return
            print(f"绯木：{rep}\n")

            try:
                msg.reply_text(rep)
                print(f"[微信] 已回复")
            except Exception as e:
                print(f"[微信] 回复失败: {e}")
        except Exception as e:
            print(f"[微信处理] 异常: {e}")

    # 回调：其他类型消息
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

    # 阻塞：SDK 内部长轮询 + 分发
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n[微信] 收到 Ctrl+C")
    except Exception as e:
        print(f"\n[微信] run 异常: {e}")
    finally:
        save_history(history, slot="owner")
        mark_clean_exit()
        print("\n绯木微信端已退出~")


if __name__ == "__main__":
    def _onexit(): mark_clean_exit()
    atexit.register(_onexit)
    def _sig(sig, frame):
        st = state.get_state()
        st.shutdown_flag.set()
        try:
            from weixin_ilink import WeixinBot
        except:
            pass
    try:
        _signal.signal(_signal.SIGINT, _sig)
        _signal.signal(_signal.SIGTERM, _sig)
    except:
        pass
    main()