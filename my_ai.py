"""绯木 - 主入口"""
import os
import sys
import time
import json
import glob
import queue
import asyncio
import atexit
import signal as _signal
import threading
import random
import re as _re

from core import state, constants
from core import api_server
from core.logger import (logger, save_crash_report, check_last_crash,
    mark_running, mark_clean_exit, cleanup_old_crash_reports)
from core.network import is_online
from core.device import choose_audio_device

from brain.memory import (RAGMemory, load_history, save_history,
    set_slot, get_slot, SLOTS)
from brain.mood import MoodManager
from brain.llm import ask_ai, is_sensitive
# from brain.proactive import proactive_loop  # 已停用（方案 A）

from voice import vmc
from voice.stt import init_whisper_bg, warmup, listen_wake, listen_record, speech_to_text
from voice.player import speak

from tools.reminders import check_loop as reminders_loop, pop as pop_reminder
from tools.diary import cleanup as cleanup_logs, append as diary_append

from core.constants import (SHORT_TERM_TURNS, CONVERSATION_KEEPALIVE,
    WAKE_WORD_MODE, FALLBACK_PROVIDER, SENSITIVE_COOLDOWN_TURNS, SCRIPT_DIR,
    USE_GPT_SOVITS, GPT_SOVITS_API, VOICE, PYGAME_VOLUME,
    GPT_SOVITS_REF_AUDIO_ZH, GPT_SOVITS_REF_TEXT_ZH)

from openai import OpenAI
import pygame
import requests

try:
    from core.tray import TrayManager
    _HAS_TRAY = True
except ImportError:
    _HAS_TRAY = False


def load_config():
    if not os.path.exists(constants.CONFIG_FILE):
        print("[错误] 未找到 config.json"); sys.exit(1)
    try:
        with open(constants.CONFIG_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception as e: print(f"[错误] {e}"); sys.exit(1)


def save_config(c):
    try:
        with open(constants.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
    except: pass


def select_provider(config, title="选择大脑"):
    ps = config.get("providers", {})
    if not ps: return None, None
    keys = list(ps.keys())
    cur = config.get("current_provider", keys[0])
    if cur not in ps: cur = keys[0]
    if len(ps) == 1: return cur, ps[cur]
    print("\n" + "="*50); print(f"  {title}："); print("="*50)
    for i, k in enumerate(keys, 1):
        p = ps[k]; m = " * 当前" if k == cur else ""
        print(f"  {i}. {p.get('name', k)} [{p.get('model', '?')}]{m}")
    print("="*50)
    try: c = input(f"序号（回车保持 {cur}）：").strip()
    except: c = ""
    if c == "": sel = cur
    else:
        try: sel = keys[int(c)-1]
        except: sel = cur
    config["current_provider"] = sel; save_config(config)
    return sel, ps[sel]


def validate_provider(p):
    k = p.get("api_key", "")
    if not k or "填入" in k or "你的" in k:
        print(f"\n[警告] [{p.get('name', '?')}] API Key 未填"); return False
    return True


def build_client(p): return OpenAI(api_key=p["api_key"], base_url=p["base_url"])


def handle_learning(ui):
    s = state.get_state()
    st = s.learning_state
    if not st["active"]: return False
    stage = st["stage"]

    if stage == "waiting_name":
        an = st["app_name"]
        from tools.computer import _scan
        from core.constants import MY_APPS_DIR as APPDIR
        hint = ui.strip(); cand = None
        cp = hint.strip('"\'""')
        if os.path.exists(cp): cand = cp
        if cand is None: cand = _scan([hint, an])
        if cand is None and os.path.exists(APPDIR):
            for f in os.listdir(APPDIR):
                if f.lower().endswith(".lnk") and hint.lower() in f.lower():
                    cand = os.path.join(APPDIR, f); break
        if cand:
            st["stage"] = "waiting_confirm"; st["candidate_path"] = cand
            asyncio.run(speak(f"找到了：{cand}，对吗？", "关切"))
        else:
            st.update({"active": False, "stage": None, "app_name": None, "candidate_path": None})
            asyncio.run(speak(f"没找到。把快捷方式放到 data\\my_apps\\ 再试。", "难过"))
        return True

    elif stage == "waiting_confirm":
        an = st["app_name"]
        pos = ["对", "是", "是的", "对的", "没错", "确定", "嗯", "好的", "可以", "正确", "对呀", "是呀"]
        neg = ["不对", "不是", "错", "否", "不要", "算了", "否定", "错误", "别"]
        from tools.computer import load_learned, save_learned
        if any(k in ui for k in pos):
            path = st["candidate_path"]
            learned = load_learned(); learned[an] = path; save_learned(learned)
            st.update({"active": False, "stage": None, "app_name": None, "candidate_path": None})
            asyncio.run(speak(f"好的，以后说『打开{an}』就能帮你打开。", "开心"))
            return True
        elif any(k in ui for k in neg):
            st.update({"active": False, "stage": None, "app_name": None, "candidate_path": None})
            asyncio.run(speak("好的，再等等。", "难过"))
            return True
        else:
            asyncio.run(speak("说『对』或『不对』哦。", "关切"))
            return True

    elif stage == "waiting_close_confirm":
        pos = ["对", "是", "关", "关闭", "关掉", "确定", "嗯", "好的", "可以", "yes", "ok",
               "關", "關閉", "關掉", "确认", "確認"]
        neg = ["不", "别", "不要", "取消", "算了", "否", "no", "別"]
        ui_low = ui.lower()
        if any(k in ui_low for k in pos):
            from tools.computer import kill_processes
            pids = st.get("close_pids", [])
            target = st.get("close_target", "")
            total = len(pids)
            print(f"[关闭执行] 关闭 {target} 的 {total} 个进程")
            success, fail = kill_processes(pids)
            st.update({"active": False, "stage": None, "close_target": None,
                       "close_pids": None, "close_names": None})
            if success == total:
                asyncio.run(speak(f"好的，已经关闭 {success} 个 {target} 进程了。", "开心"))
            elif success > 0:
                asyncio.run(speak(f"尝试关闭 {total} 个，成功 {success} 个。", "开心"))
            else:
                reason = fail[0] if fail else "权限不足"
                asyncio.run(speak(f"没能关闭 {target} 呢，{reason}", "难过"))
            return True
        elif any(k in ui_low for k in neg):
            st.update({"active": False, "stage": None, "close_target": None,
                       "close_pids": None, "close_names": None})
            asyncio.run(speak("好的，不关啦。", "开心"))
            return True
        else:
            asyncio.run(speak("说要还是不要哦。", "关切"))
            return True

    return False


def handle_slash_command(ui, s):
    cmd = ui[1:].strip().lower()
    if cmd in ["mute", "静音", "关闭声音"]:
        s.mute_mode = True
        print("[模式切换] 静音（不发声）"); return True
    elif cmd in ["unmute", "取消静音", "开启声音"]:
        s.mute_mode = False
        print("[模式切换] 正常（发声）"); return True
    elif cmd in ["voice", "语音"]:
        s.text_mode = False; s.interrupt_listen.set()
        print("[模式切换] 语音输入"); return True
    elif cmd in ["text", "文本"]:
        s.text_mode = True
        print("[模式切换] 文本输入"); return True
    elif cmd in ["proactive_on", "主动on", "开启主动"]:
        s.proactive_enabled = True; s.proactive_missed = 0
        print("[主动] 已开启主动关心"); return True
    elif cmd in ["proactive_off", "主动off", "关闭主动"]:
        s.proactive_enabled = False
        print("[主动] 已关闭主动关心"); return True
    elif cmd in ["quiet", "安静", "别打扰"]:
        s.proactive_enabled = False
        s.last_proactive_time = time.time()
        print("[主动] 已进入静默"); return True
    elif cmd.startswith("interval") or cmd.startswith("间隔"):
        parts = cmd.split()
        if len(parts) >= 2:
            v = parts[1].lower()
            try:
                if v.endswith("s") or v.endswith("秒"):
                    secs = int(v.rstrip("s秒"))
                    s.proactive_interval = secs
                    print(f"[主动] 间隔已设为 {secs} 秒")
                elif v.endswith("m") or v.endswith("分"):
                    mins = int(v.rstrip("m分"))
                    s.proactive_interval = mins * 60
                    print(f"[主动] 间隔已设为 {mins} 分钟")
                elif v.isdigit():
                    s.proactive_interval = int(v)
                    print(f"[主动] 间隔已设为 {v} 秒")
                else:
                    print("[主动] 用法：/interval 30s  或  /interval 2m")
            except ValueError:
                print("[主动] 数字格式错误")
        else:
            cur = s.proactive_interval
            if cur < 60:
                print(f"[主动] 当前间隔 {cur} 秒")
            else:
                print(f"[主动] 当前间隔 {cur // 60} 分钟")
            print("       用法：/interval 30s  或  /interval 2m")
        return True
    elif cmd in ["memory", "记忆"]:
        try:
            from brain import memory as _mem
            print(f"\n=== 记忆槽位 ===")
            for slot in _mem.SLOTS:
                h = _mem.load_history(slot=slot)
                rag_count = "?"
                try:
                    s_ = state.get_state()
                    if s_.rag and s_.rag._loaded:
                        rag_count = s_.rag.collections[slot].count()
                except:
                    pass
                marker = " ← 当前" if slot == _mem.get_slot() else ""
                print(f"  {slot:10s}  短期 {len(h):3d} 条  RAG {rag_count} 条{marker}")
            print("====================\n")
        except Exception as e:
            print(f"[记忆] 读取失败: {e}")
        return True
    elif cmd in ["quit", "exit", "退出"]:
        s.shutdown_flag.set(); return True
    elif cmd in ["speakers", "声纹"]:
        try:
            from voice.speaker_id import list_speakers
            print(list_speakers())
        except Exception as e:
            print(f"[声纹] 列出失败: {e}")
        return True
    elif cmd in ["mode", "模式"]:
        from brain.persona import get_mode, get_current_state
        m = get_mode()
        st = get_current_state()
        print(f"\n=== 当前模式：{m} ===")
        print(f"  connection: {st['drives']['connection']:.2f}")
        print(f"  security:   {st['drives']['security']:.2f}")
        print(f"  curiosity:  {st['drives']['curiosity']:.2f}")
        print(f"  expression: {st['drives']['expression']:.2f}")
        print(f"  validation: {st['drives']['validation']:.2f}")
        print(f"  情绪：{st['emotion']['label']}")
        print("用法：/mode master  或  /mode audience\n")
        return True
    elif cmd.startswith("mode ") or cmd.startswith("模式 "):
        parts = cmd.split()
        if len(parts) >= 2:
            v = parts[1].lower()
            from brain.persona import set_mode
            if v in ["master", "主人"]:
                set_mode("master")
                print("[模式] 已切到主人模式")
            elif v in ["audience", "观众", "直播"]:
                set_mode("audience")
                print("[模式] 已切到观众模式")
            else:
                print("[模式] 用法：/mode master  或  /mode audience")
        return True
    elif cmd in ["reflect", "反思"]:
        try:
            from brain.reflect import reflect_sync
            print("[反思] 手动反思需要历史，稍后每20轮会自动触发")
        except Exception as e:
            print(f"[反思] 失败: {e}")
        return True
    elif cmd in ["preferences", "偏好"]:
        try:
            from brain.persona import get_preferences
            prefs = get_preferences()
            print("\n=== 当前偏好 ===")
            sorted_p = sorted(prefs.items(), key=lambda x: -x[1])
            for k, v in sorted_p:
                bar = "█" * int(abs(v) * 10)
                sign = "👍" if v > 0 else ("👎" if v < 0 else "  ")
                print(f"  {sign} {k:<10} {v:+.2f}  {bar}")
            print("====================\n")
        except Exception as e:
            print(f"[偏好] 读取失败: {e}")
        return True
    elif cmd in ["persona", "人格"]:
        try:
            from brain.persona import get_current_state
            st = get_current_state()
            print("\n=== 当前人格状态 ===")
            for k, v in st["traits"].items():
                print(f"  {k}: {v:.2f}")
            print(f"  情绪：{st['emotion']['label']} (v={st['emotion']['valence']:.2f}, a={st['emotion']['arousal']:.2f})")
            print("====================\n")
        except Exception as e:
            print(f"[人格] 读取失败: {e}")
        return True
    elif cmd in ["inner", "内在", "在想什么"]:
        try:
            from brain.inner_life import get_inner_log
            logs = get_inner_log(limit=10)
            if not logs:
                print("\n（还没有内在日志）\n")
                return True

            now = time.time()
            print(f"\n=== 最近的内在活动（当前 {time.strftime('%H:%M:%S')}）===")

            for e in logs:
                ts = e.get("ts", 0)
                t = time.strftime("%m-%d %H:%M", time.localtime(ts))
                # 距今
                delta_min = int((now - ts) / 60)
                if delta_min < 1:
                    ago = "刚刚"
                elif delta_min < 60:
                    ago = f"{delta_min}分钟前"
                else:
                    h = delta_min // 60
                    m = delta_min % 60
                    ago = f"{h}小时{m}分钟前" if m else f"{h}小时前"

                content = e.get("content", "") or ""
                reason = e.get("reason", "") or ""
                next_wake = e.get("next_wake")
                wake_reason = e.get("wake_reason", "") or ""

                line = f"  [{t}] ({ago}) {e['intent']:8s}"
                if content:
                    line += f" {content[:40]}"
                print(line)
                if reason:
                    print(f"         → {reason}")
                if next_wake:
                    wake_at = ts + next_wake
                    wake_str = time.strftime("%H:%M", time.localtime(wake_at))
                    m = next_wake // 60
                    w = f"{m}分钟" if m < 60 else f"{m // 60}小时"
                    print(f"         ⏰ 下次 {wake_str}（{w}后）({wake_reason})")

            # 下一轮预计唤醒（绝对时间）
            last = logs[-1]
            last_next = last.get("next_wake", 0)
            if last_next:
                wake_at = last.get("ts", 0) + last_next
                wake_str = time.strftime("%H:%M", time.localtime(wake_at))
                remain_min = int((wake_at - now) / 60)
                if remain_min < 0:
                    print(f"\n下一轮：应该醒了（{wake_str} 已过）")
                else:
                    print(f"\n下一轮预计唤醒：{wake_str}（距今 {remain_min} 分钟）")

            print("========================\n")
        except Exception as e:
            print(f"[内在] 读取失败: {e}")
        return True

    elif cmd in ["l0", "自我", "核心自我"]:
        try:
            from brain.l0_model import load_self_model
            m = load_self_model()
            print("\n=== L0 核心自我 ===")
            si = m.get("self_identity", {})
            who = si.get("who_i_am", {})
            print(f"  我是谁：{who.get('statement', '')}")
            print(f"  价值观：{', '.join(si.get('core_values', []))}")
            print(f"  边界：{', '.join(si.get('boundaries', []))}")
            ops = m.get("opinions", {})
            print(f"\n  观点（{len(ops)} 个）：")
            for topic, op in ops.items():
                conf = op.get("confidence", 0)
                it = op.get("iteration_count", 0)
                print(f"    · {topic}（conf={conf:.2f}, iter={it}）")
                print(f"      {op.get('my_view', '')[:60]}")
            oqs = m.get("open_questions", [])
            print(f"\n  未解问题：{len(oqs)} 个")
            print("===================\n")
        except Exception as e:
            print(f"[L0] 读取失败: {e}")
        return True

    elif cmd in ["learning", "学习任务", "tasks"]:
        try:
            from brain.l0_model import list_learning_tasks
            tasks = list_learning_tasks("all")
            pending = [t for t in tasks if t.get("status") == "pending"]
            done = [t for t in tasks if t.get("status") == "completed"]
            print(f"\n=== 学习任务（pending {len(pending)} / done {len(done)}）===")
            if pending:
                print("  【待执行】")
                for t in pending:
                    print(f"    · {t.get('topic', '')}（p={t.get('priority', 0):.2f}）")
                    print(f"      {t.get('reason', '')[:60]}")
            if done:
                print("  【已完成】")
                for t in done[-5:]:
                    print(f"    · {t.get('topic', '')}")
            print("========================\n")
        except Exception as e:
            print(f"[learning] 读取失败: {e}")
        return True

    elif cmd.startswith("l0-history "):
        try:
            topic = ui[1:].strip()[len("l0-history "):].strip()
            from brain.l0_model import get_history
            hist = get_history(topic)
            if not hist:
                print(f"\n（没有 {topic} 的观点历史）\n")
                return True
            print(f"\n=== {topic} 的观点演变（{len(hist)} 版）===")
            for h in hist:
                v = h.get("version", "?")
                ts = h.get("ts", "")
                conf = h.get("confidence", 0)
                print(f"  v{v} [{ts}] conf={conf:.2f}")
                print(f"    {h.get('view', '')}")
            print("========================\n")
        except Exception as e:
            print(f"[l0-history] 失败: {e}")
        return True
    
    elif cmd in ["episodes", "情景", "记忆片段"]:
        try:
            from brain.episodic import retrieve_episodes
            eps = retrieve_episodes(query=None, top_k=10, days_back=7, min_importance=0.0)
            if not eps:
                print("\n（最近 7 天没有情景记忆）\n")
                return True
            print(f"\n=== 最近 7 天的情景记忆（{len(eps)} 条）===")
            for e in eps:
                day = e.get("day", "")
                content = e.get("content", "")
                etype = e.get("event_type", "")
                imp = e.get("importance", 0)
                self_emo = e.get("self_emotion", {}).get("label", "")
                print(f"  [{day}] {etype:12s} imp={imp:.2f}")
                print(f"    {content[:60]}")
                if self_emo and self_emo != "平静":
                    print(f"    （我当时{self_emo}）")
            print("====================================\n")
        except Exception as e:
            print(f"[情景] 读取失败: {e}")
        return True
    elif cmd in ("explore", "学习", "探索") or cmd.startswith(("explore ", "学习 ", "探索 ")):
        try:
            parts = ui[1:].split(maxsplit=1)
            if len(parts) < 2:
                print("[探索] 用法：/explore 关键词")
                return True
            keyword = parts[1].strip()
            from core import api_server as _api
            from brain.inner_life import _do_explore
            if not _api._client or not _api._provider:
                print("[探索] 大脑未就绪")
                return True
            threading.Thread(
                target=_do_explore,
                args=(keyword, _api._client, _api._provider),
                daemon=True
            ).start()
            print(f"[探索] 已启动：{keyword}")
        except Exception as e:
            print(f"[探索] 失败: {e}")
        return True
    elif cmd in ["help", "帮助"]:
        print("\n=== 可用命令 ===")
        print("  /mute        - 静音")
        print("  /unmute      - 取消静音")
        print("  /voice       - 切换语音输入")
        print("  /text        - 切换文本输入")
        print("  /proactive_on  - 开启主动关心")
        print("  /proactive_off - 关闭主动关心")
        print("  /quiet       - 安静 1 小时")
        print("  /interval N  - 设置主动间隔（如 /interval 30s）")
        print("  /speakers    - 列出声纹库")
        print("  /mode        - 查看/切换模式")
        print("  /memory      - 查看记忆槽位")
        print("  /persona     - 查看人格状态")
        print("  /preferences - 查看她的偏好")
        print("  /reflect     - 手动触发反思")
        print("  /inner       - 查看她最近在想什么")
        print("  /episodes    - 查看最近 7 天的情景记忆")
        print("  /l0          - 查看 L0 核心自我")
        print("  /learning    - 查看学习任务")
        print("  /l0-history <topic> - 查看观点演变")
        print("  /explore KW  - 手动触发探索（测试用）")
        print("  /quit        - 退出程序")
        print("  \"\"\"          - 多行输入")
        print("================\n")
        return True
    else:
        print(f"[未知命令] /{cmd}，输入 /help 查看帮助")
        return True


def main():
    s = state.get_state()

    if "--text" in sys.argv or "-t" in sys.argv:
        s.text_mode = True
        print("[启动] 文本模式")
    elif "--voice" in sys.argv or "-v" in sys.argv:
        s.text_mode = False

    last = check_last_crash()
    if last: print(f"⚠️ 上次异常退出：{last}\n")
    mark_running()
    cleanup_old_crash_reports(keep=10)
    logger.info(f"=== 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    if is_online(force_check=True): print("🌐 网络：在线")
    else: print("🌐 网络：离线")
    print("="*50); print("绯木启动中..."); print("="*50)

    try:
        from brain.persona import (set_mode as _set_mode,
            normalize_on_boot as _normalize)
        _set_mode("master")
        _normalize()
        print("[模式] 启动时切到主人模式")
    except Exception as e:
        print(f"[模式] 初始化失败: {e}")

    tray = None
    if _HAS_TRAY:
        try:
            tray = TrayManager()
            tray.start()
            s.tray = tray
        except Exception as e:
            print(f"[Tray] 启动失败: {e}")

    threading.Thread(target=init_whisper_bg, daemon=True).start()
    s.rag = RAGMemory()
    s.mood_mgr = MoodManager()
    cleanup_logs()

    for pat in ["reply_*.mp3", "reply_*.wav", "reply_stream_*.wav", "reply_stream_*.mp3"]:
        for old in glob.glob(os.path.join(SCRIPT_DIR, pat)):
            try: os.remove(old)
            except: pass
    for f in ["input.wav", "wake.wav", "warmup.wav"]:
        try: os.remove(os.path.join(SCRIPT_DIR, f))
        except: pass

    try: pygame.mixer.init(frequency=32000, size=-16, channels=2, buffer=1024)
    except:
        try: pygame.mixer.init(frequency=48000, size=-16, channels=2, buffer=1024)
        except: pygame.mixer.init()
    pygame.mixer.music.set_volume(PYGAME_VOLUME)

    vmc.init()
    s.life_sim = vmc.LifeSimulator()

    config = load_config()
    s.audio_device = choose_audio_device(config)
    save_config(config)

    saved = config.get("current_provider")
    pd = config.get("providers", {})
    if saved and saved in pd:
        pn = saved; provider = pd[saved]
        print(f"🧠 使用上次大脑：{provider.get('name', pn)} [{provider.get('model','?')}]")
    else:
        pn, provider = select_provider(config, "启动选择大脑")
    if not provider or not validate_provider(provider): sys.exit(1)
    client = build_client(provider)
    fb = config.get("providers", {}).get(FALLBACK_PROVIDER)
    fb_client = build_client(fb) if fb else None

    def delayed():
        time.sleep(30)
        print("\n[RAG] 开始加载嵌入模型...")
        s.rag.start_background_load()
        print("[RAG] 加载完成")
    threading.Thread(target=delayed, daemon=True).start()

    print("等待 Whisper...")
    s.whisper_ready.wait()
    warmup()

    if "ollama" in provider.get("base_url", "").lower() or provider.get("api_key") == "ollama":
        def _preheat():
            try:
                requests.post("http://localhost:11434/api/generate", json={
                    "model": provider["model"],
                    "prompt": "你好",
                    "stream": False,
                    "keep_alive": "30m",
                }, timeout=300)
                print("\n✅ 本地大脑已就绪")
            except Exception as e:
                print(f"\n⚠️ 预热失败：{e}")
        print("预热本地大脑（首次启动需 30~120 秒，请等待）...")
        _preheat()

    threading.Thread(target=reminders_loop, daemon=True).start()

    from brain.persona import decay_loop
    threading.Thread(target=decay_loop, daemon=True).start()

    # TTS 自动拉起
    if USE_GPT_SOVITS:
        def _ensure_tts_api():
            import subprocess
            try:
                r = requests.get(f"{GPT_SOVITS_API}/docs", timeout=2)
                if r.status_code == 200:
                    print("[TTS] API 已在运行 ✅")
                    return True
            except:
                pass

            bat_path = os.path.join(SCRIPT_DIR, "启动API.bat")
            if not os.path.exists(bat_path):
                print(f"[TTS] ⚠️ 找不到 {bat_path}")
                return False

            print(f"[TTS] API 未启动，正在拉起（约需 30~60 秒）...")
            try:
                subprocess.Popen(
                    ["cmd", "/c", "start", "GPT-SoVITS API", bat_path],
                    shell=False,
                    cwd=SCRIPT_DIR,
                )
            except Exception as e:
                print(f"[TTS] 启动失败: {e}")
                return False

            for i in range(90):
                time.sleep(1)
                try:
                    r = requests.get(f"{GPT_SOVITS_API}/docs", timeout=2)
                    if r.status_code == 200:
                        print(f"[TTS] ✅ API 已就绪（等待 {i+1} 秒）")
                        return True
                except:
                    pass
                if i % 10 == 9:
                    print(f"[TTS] 还在加载... ({i+1}s)")

            print("[TTS] ⚠️ 等待超时，请手动检查 API 窗口")
            return False

        _ensure_tts_api()

        def _warmup_tts():
            try:
                print("[TTS] 预热中（首次需 20~30 秒）...")
                t0 = time.time()
                from voice.tts import _build_gpt_sovits_payload
                payload = _build_gpt_sovits_payload("嗯", "zh", 0, streaming=False)
                r = requests.post(f"{GPT_SOVITS_API}/tts", json=payload, timeout=120)
                if r.status_code == 200:
                    print(f"[TTS] ✅ 预热完成（{time.time()-t0:.1f}s）")
                else:
                    print(f"[TTS] ⚠️ 预热失败 HTTP {r.status_code}: {r.text[:100]}")
            except Exception as e:
                print(f"[TTS] ⚠️ 预热异常：{type(e).__name__}: {e}")

        _warmup_tts()

    speaker_status = "未注册"
    try:
        from voice.speaker_id import is_enrolled, _load_meta
        if is_enrolled():
            meta = _load_meta()
            speaker_status = f"已注册 {len(meta)} 人（{', '.join(meta.keys())}）"
    except Exception as e:
        speaker_status = f"加载失败: {e}"

    print("\n" + "="*50)
    print(f"绯木已启动！")
    print(f"主大脑：{provider.get('name', pn)} [{provider['model']}]")
    if fb_client: print(f"备用大脑：{fb.get('name', FALLBACK_PROVIDER)}")
    print(f"声音：{'GPT-SoVITS 已启用' if USE_GPT_SOVITS else VOICE}")
    print(f"麦克风：{s.audio_device} | 短期记忆：{SHORT_TERM_TURNS} 轮")
    print(f"声纹库：{speaker_status}")
    print(f"主动关心：{'开启' if s.proactive_enabled else '关闭'}（间隔 {s.proactive_interval // 60} 分钟）")
    print(f"输入 /help 查看命令")
    print("="*50 + "\n")

    set_slot("owner")
    current_slot = "owner"
    history = load_history(slot="owner")
    if history: print(f"[已加载 {len(history)} 条 owner 槽短期记忆]\n")

    # ══════════════════════════════════════════════════════
    # B 阶段：对话内学习循环（闭包）
    # ══════════════════════════════════════════════════════
    def _do_feedback_capture(user_msg):
        """检测用户反馈并写回 L0

        topic 选择策略（按优先级）：
        1. last_turn_topics 里完整出现的 topic
        2. L0 里所有 topics 里跟 user_msg 最匹配的
        3. last_turn_topics[-1] 兜底
        """
        try:
            from brain.feedback_capture import detect_feedback, apply_feedback
            fb = detect_feedback(user_msg, getattr(s, "last_turn_reply", ""))
            if fb["type"] == "neutral":
                return None

            # ★ 不再只依赖 s.last_turn_topics
            topic = fb.get("topic")
            if not topic:
                topic = _pick_feedback_topic(user_msg)

            fb["topic"] = topic
            if not topic:
                print(f"[反馈] {fb['type']} 但无 topic，忽略")
                return None

            ok, info = apply_feedback(fb, topic=topic)
            if ok:
                print(
                    f"[反馈] {fb['type']} -> {topic} "
                    f"conf={info.get('new_confidence', 0):.2f}"
                )
                import time as _t
                fb["ts"] = _t.time()
                s.last_feedback = fb
                return fb
        except Exception as e:
            print(f"[反馈] 异常: {e}")
        return None

    def _pick_feedback_topic(user_msg):
        """挑反馈针对的 topic

        优先级：
        1. last_turn_topics 完整出现
        2. L0 里跟 user_msg 核心字最匹配的
        3. last_turn_topics[-1]
        """
        # 1. last_turn_topics 完整出现
        try:
            last_topics = getattr(s, "last_turn_topics", []) or []
            for t in last_topics:
                if t and t in user_msg:
                    return t
        except Exception:
            last_topics = []

        # 2. L0 里做核心字匹配
        try:
            from brain.l0_model import list_opinions, _topic_core_chars
            user_chars = set(user_msg or "")
            candidates = []
            for topic in list_opinions().keys():
                if not topic:
                    continue
                core = _topic_core_chars(topic)
                if not core:
                    continue
                # topic 核心字里有多少在 user_msg 里
                hit = len(core & user_chars)
                if hit >= 2:  # 至少 2 个字命中
                    candidates.append((topic, hit))
            if candidates:
                return max(candidates, key=lambda x: x[1])[0]
        except Exception as e:
            print(f"[反馈] L0 匹配失败: {e}")

        # 3. last_turn_topics[-1]
        if last_topics:
            return last_topics[-1]
        return None

    def _do_knowledge_gate(user_msg):
        """知识门控：判断该不该搜索"""
        if not getattr(s, "knowledge_gate_enabled", True):
            return None
        try:
            from brain.knowledge_gate import quick_check, judge_need_search
            verdict = quick_check(user_msg)
            print(f"[知识门控] {verdict['verdict']} | {verdict['reason']}")
            if verdict["verdict"] == "chitchat":
                return None
            if verdict["verdict"] == "skip":
                return {
                    "l0_topic": verdict["l0_topic"],
                    "l0_view": verdict["l0_view"],
                    "l0_confidence": verdict["l0_confidence"],
                    "need_search": False,
                }
            if verdict["verdict"] == "search":
                return {
                    "l0_topic": None,
                    "l0_view": "",
                    "l0_confidence": 0.0,
                    "need_search": True,
                    "search_query": user_msg[:40],
                }
            # verdict == "check"
            judge = judge_need_search(
                client, provider, user_msg,
                verdict["l0_topic"], verdict["l0_view"],
                verdict["l0_confidence"],
            )
            print(f"[知识门控] judge: {judge['reason']}")
            return {
                "l0_topic": verdict["l0_topic"],
                "l0_view": verdict["l0_view"],
                "l0_confidence": verdict["l0_confidence"],
                "need_search": judge["need_search"],
                "search_query": judge.get("search_query", user_msg[:40]),
            }
        except Exception as e:
            print(f"[知识门控] 异常: {e}")
            return None

    session_start = time.time()
    cooldown = 0; conv_until = 0

    # ══════════════════════════════════════════════════════
    # L2/L4 异步执行 —— 后台线程处理，不阻塞主循环
    # ══════════════════════════════════════════════════════
    _pending_reflections = queue.Queue()

    def _maybe_record_episode(user_msg, reply, src, client_ref, provider_ref):
        """用 LLM 判断这轮是否值得写入情景记忆"""
        if not user_msg or not reply:
            return

        try:
            from brain.episodic import judge_should_record, record_episode

            should, etype, content, imp_hint = judge_should_record(
                client_ref, provider_ref, user_msg, reply
            )
            if not should or not content:
                return

            from brain.persona import get_current_state
            st = get_current_state()

            record_episode(
                content=content,
                event_type=etype,
                entities=[],
                self_emotion=st.get("emotion"),
                self_intent="speak",
                self_role="responder",
                channel=src,
                raw_context=f"哥哥：{user_msg}\n我：{reply}",
                novelty=imp_hint,
            )
            print(f"[L2] LLM判定记录: type={etype} content={content[:40]}")
        except Exception as e:
            print(f"[L2] 判定异常: {e}")

    def _post_turn_async(client_ref, provider_ref, own_last, user_msg, src,
                          interrupted, ref_queue):
        """后台线程：L2 情绪影响 + L2 情景记忆 + L4 反思

        - self_mood：她自己的话反过来影响她的情绪
        - episodic：从这轮对话抽取情景记忆
        - L4：生成自我反思文本，放入 ref_queue
        - L4 不再 TTS 播放（自语是内心话，不该读出来）
        - L4 加编造检查：命中"看到/听到/在公园"等 → 丢弃
        """
        if interrupted or not own_last:
            return

        # ═══ 1. self_mood：她自己的话反过来影响她的情绪 ═══
        try:
            from brain.self_mood import apply_own_speech_impact
            apply_own_speech_impact(own_last, source=src)
        except Exception as e:
            print(f"[self_mood] 异常: {e}")

        # ═══ 2. episodic：情景记忆写入（LLM 判断）═══
        try:
            _maybe_record_episode(user_msg, own_last, src,
                                   client_ref, provider_ref)
        except Exception as e:
            print(f"[episodic] 异常: {e}")

        # ═══ 3. L4：自我反思 ═══
        try:
            from brain.self_reflect import reflect_on_own_speech
            from brain.l4_audit import is_fabricated, find_fabrication
            reflect_text = reflect_on_own_speech(
                client_ref, provider_ref, own_last, source=src
            )
            if reflect_text:
                if is_fabricated(reflect_text):
                    hits = find_fabrication(reflect_text)
                    print(f"[L4] 编造嫌疑 {hits}，丢弃: {reflect_text[:40]}")
                    return
                time.sleep(random.uniform(0.6, 1.5))
                print(f"\n绯木（自语）：{reflect_text}")
                ref_queue.put(reflect_text)
        except Exception as e:
            print(f"[L4] 异常: {e}")

    async def life_loop():
        while not s.shutdown_flag.is_set():
            if s.life_sim: s.life_sim.update()
            if s.mood_mgr: s.mood_mgr.tick()
            await asyncio.sleep(0.033)

    def run_life():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        loop.run_until_complete(life_loop())
    threading.Thread(target=run_life, daemon=True).start()

    # 内在生活循环
    from brain.inner_life import inner_life_loop
    threading.Thread(
        target=inner_life_loop,
        args=(client, provider),
        daemon=True
    ).start()
    print("[内在] 生活循环已启动")

    # 元认知观测器
    try:
        from brain.metacognition import metacognition_loop
        threading.Thread(
            target=metacognition_loop,
            args=(client, provider),
            daemon=True
        ).start()
        print("[元认知] 观测器已启动")
    except Exception as e:
        print(f"[元认知] 启动失败: {e}")

    # L2 → L1 巩固
    try:
        from brain.consolidate import consolidate_loop
        threading.Thread(
            target=consolidate_loop,
            args=(client, provider),
            daemon=True
        ).start()
        print("[巩固] L2→L1 巩固循环已启动")
    except Exception as e:
        print(f"[巩固] 启动失败: {e}")

    # 启动 HTTP 服务
    api_server.set_context(client, provider, history, session_start)
    api_server.start()
    print("[API] 已注册上下文")

    _input_queue = queue.Queue()
    _input_stop = threading.Event()
    def _input_worker():
        while not _input_stop.is_set():
            if not s.text_mode:
                time.sleep(0.3)
                continue
            try:
                line = input("你（文本）：")
                _input_queue.put(line)
            except (EOFError, KeyboardInterrupt):
                _input_stop.set()
                break
    threading.Thread(target=_input_worker, daemon=True).start()

    _MC_MY_NAME = "EZFM233"
    try:
        from tools.minecraft import set_event_callback as _mc_set_cb
        def _on_mc_event(data):
            try:
                ev = data.get("event")
                if ev == "chat":
                    user = data.get("username", "?")
                    msg = (data.get("message") or "").strip()
                    if msg:
                        print(f"\n[MC聊天] <{user}> {msg}")
                        _input_queue.put(f"[MC:{user}] {msg}")
                elif ev == "spawn":
                    print(f"\n[MC] {data.get('username')} 已进入世界")
                elif ev == "end":
                    print(f"\n[MC] bot 已断开连接")
                elif ev == "kicked":
                    print(f"\n[MC] 被踢出: {data.get('reason')}")
            except Exception as e:
                print(f"[MC事件] 处理异常: {e}")
        _mc_set_cb(_on_mc_event)
        print("[MC] ✅ 事件回调已注册\n")
    except Exception as e:
        print(f"[MC] ⚠️ 注册事件回调失败: {e}\n")

    END_CHAT_KW = [
        "再见", "拜拜", "bye", "先这样", "结束对话", "结束聊天",
        "不聊了", "先不聊", "回头聊", "下次聊", "先忙", "我先走",
    ]
    EXIT_VERBS = ["退出", "关闭", "关掉", "结束", "停止"]
    WAKE_VARIANTS = ["绯木", "飞木", "肥木", "菲木", "非木", "费木", "feimu", "Feimu"]

    VOICE_CMD_MUTE = ["静音", "安靜", "安静"]
    VOICE_CMD_UNMUTE = ["取消静音", "开启声音", "恢复声音"]
    VOICE_CMD_TEXT = ["切到文本", "切换到文本", "文本模式"]
    VOICE_CMD_VOICE = ["切到语音", "切换到语音", "语音模式"]

    MC_WAKE_NAMES = ["绯木", "feimu", "FeiMu", "小木", "木木"]

    speaker = "主人"
    relation = "主人"

    while not s.shutdown_flag.is_set():
        try:
            t_cycle = time.time()
            current_source = "owner"

            # 消费上一轮异步产生的自语（只写记忆，不播 TTS）
            try:
                while True:
                    _rtext = _pending_reflections.get_nowait()
                    if _rtext:
                        history.append({"role": "assistant", "content": _rtext})
                        save_history(history, slot=current_slot)
                        print(f"[L4] 自语已写入记忆（不播）")
            except queue.Empty:
                pass

            with s.proactive_queue_lock:
                if s.proactive_queue:
                    item = s.proactive_queue.pop(0)
                    pro_msg = item["msg"]
                    pro_emotion = item["emotion"]
                    print(f"\n绯木（主动）：{pro_msg}")
                    history.append({"role": "assistant", "content": pro_msg})
                    save_history(history, slot=current_slot)

                    # ══════════════════════════════════════════
                    # 判断微信端是否在线
                    # 90 秒内收到过微信端请求 → 在线 → 电脑端静默
                    # ══════════════════════════════════════════
                    _now = time.time()
                    _last_ping = getattr(s, "last_wechat_ping_time", 0)
                    _wechat_online = (_now - _last_ping) < 90

                    if _wechat_online:
                        print("[主动] 微信端在线，电脑端静默（已推微信）")
                    else:
                        print("[主动] 微信端离线，电脑端出声")
                        if tray: tray.set_status("active")
                        try:
                            asyncio.run(speak(pro_msg, pro_emotion))
                        except Exception as _e:
                            print(f"[主动] TTS 失败: {_e}")
                        if tray: tray.set_status("sleep")

                    conv_until = time.time() + CONVERSATION_KEEPALIVE
                    print("[主动] 进入对话模式，直接说话即可\n")
                    continue

            pend = pop_reminder()
            if pend:
                print(f"\n⏰ [提醒] {pend}")
                if tray: tray.set_status("active")
                asyncio.run(speak(f"该提醒你了：{pend}", "关切"))
                if tray: tray.set_status("sleep")
                continue

            if s.text_mode:
                speaker = "主人"
                relation = "主人"
                try:
                    ui = _input_queue.get_nowait().strip()
                except queue.Empty:
                    ui = None
                if ui is None:
                    time.sleep(0.1)
                    continue
                if not ui: continue

                if ui.startswith("[MC:"):
                    _m = _re.match(r'\[MC:([^\]]+)\]\s*(.*)', ui)
                    if not _m:
                        continue
                    mc_user = _m.group(1)
                    ui = _m.group(2).strip()
                    from_mc = True
                    from brain.persona import set_mode, get_mode
                    if mc_user == _MC_MY_NAME:
                        speaker = "主人"
                        relation = "主人"
                        current_source = "mc"
                        set_mode("master")
                    else:
                        speaker = mc_user
                        relation = "朋友"
                        current_source = "friend"
                    print(f"[MC输入] 来自 {mc_user}：{ui}")
                    if not ui: continue
                    _low = ui.lower()
                    _called = any(n.lower() in _low for n in MC_WAKE_NAMES)
                    if not _called:
                        print(f"[MC] 未呼名，忽略")
                        continue
                    for n in MC_WAKE_NAMES:
                        ui = ui.replace(n, "").strip("，,、。.!！?？ ")
                    if not ui:
                        ui = "嗯？"
                else:
                    from_mc = False

                if ui.startswith("/"):
                    handle_slash_command(ui, s)
                    if s.shutdown_flag.is_set(): break
                    continue
                if ui.startswith('"""'):
                    print("[多行模式] 输入内容，单独一行输入 \"\"\" 结束：")
                    lines = []
                    first_line = ui[3:].strip()
                    if first_line: lines.append(first_line)
                    while True:
                        try:
                            line = _input_queue.get(timeout=300)
                        except queue.Empty:
                            break
                        if line.strip() == '"""':
                            break
                        lines.append(line)
                    ui = "\n".join(lines).strip()
                    if not ui: continue
                    print(f"[多行输入] 已收集 {len(lines)} 行")
                conv_until = time.time() + CONVERSATION_KEEPALIVE

            else:
                from_mc = False

                if WAKE_WORD_MODE and time.time() > conv_until:
                    if tray: tray.set_status("sleep")
                    if not listen_wake(): continue
                    print("="*40); print("🎤 对话模式"); print("="*40)
                    if tray: tray.set_status("active")

                if not listen_record(): continue
                print()
                ui = speech_to_text()
                if not ui or len(ui) < 2: continue

                current_source = "owner"
                try:
                    from voice.speaker_id import identify, is_enrolled
                    if is_enrolled():
                        import os as _os
                        audio_path = _os.path.join(SCRIPT_DIR, "input.wav")
                        name, rel, score = identify(audio_path)
                        from brain.persona import set_mode
                        if name:
                            speaker = name
                            relation = rel
                            print(f"[声纹] ✅ 识别为 {name}（{rel}），相似度 {score:.2f}")
                            if rel == "主人":
                                set_mode("master")
                                current_source = "owner"
                            elif rel == "朋友":
                                current_source = "friend"
                            else:
                                set_mode("audience")
                                current_source = "audience"
                        else:
                            speaker = "陌生人"
                            relation = "陌生人"
                            print(f"[声纹] ⚠️ 未识别（相似度 {score:.2f}），视为陌生人")
                            set_mode("audience")
                            current_source = "audience"
                    else:
                        speaker = "主人"
                        relation = "主人"
                        current_source = "owner"
                except Exception as e:
                    print(f"[声纹] 识别异常：{e}")
                    speaker = "主人"
                    relation = "主人"
                    current_source = "owner"

                print(f"你：{ui}                     ")
                conv_until = time.time() + CONVERSATION_KEEPALIVE

                ui_c = ui.strip().rstrip("。！!，,、. ")
                if ui_c in VOICE_CMD_MUTE:
                    s.mute_mode = True
                    print("[模式切换] 静音（不发声）")
                    asyncio.run(speak("好的，我闭嘴啦。", "平静"))
                    continue
                elif ui_c in VOICE_CMD_UNMUTE:
                    s.mute_mode = False
                    print("[模式切换] 正常（发声）")
                    asyncio.run(speak("嗯，又能说话啦。", "开心"))
                    continue
                elif ui_c in VOICE_CMD_TEXT:
                    s.text_mode = True
                    print("[模式切换] 文本输入")
                    continue
                elif ui_c in VOICE_CMD_VOICE:
                    s.text_mode = False
                    print("[模式切换] 语音输入")
                    continue

            _now = time.time()
            _old = getattr(s, "last_interaction_time", 0)
            s.last_turn_gap = _now - _old if _old > 0 else 0
            s.last_interaction_time = _now
            s.proactive_missed = 0

            if current_source in ("owner", "mc"):
                new_slot = "owner"
            elif current_source == "friend":
                new_slot = "friend"
            else:
                new_slot = "audience"

            if new_slot != current_slot:
                save_history(history, slot=current_slot)
                history = load_history(slot=new_slot)
                if s.rag:
                    s.rag.switch_slot(new_slot)
                set_slot(new_slot)
                current_slot = new_slot
                print(f"[记忆] 切换槽位 → {new_slot}（加载 {len(history)} 条）")

            if s.learning_state["active"]:
                print(f"[状态机] stage={s.learning_state['stage']}")
                handle_learning(ui)
                continue

            if any(kw in ui for kw in ["切换模型", "换大脑", "换个模型", "换个大脑", "切换大脑", "换脑"]):
                nn, np_ = select_provider(config, "热切换")
                if np_ and validate_provider(np_):
                    pn = nn; provider = np_; client = build_client(provider)
                    asyncio.run(speak(f"已切换到{provider.get('name')}。", "开心"))
                continue

            if any(kw in ui for kw in ["切到耳机", "切换到耳机", "耳机模式", "用耳机"]):
                config["audio_mode"] = "headphone"; save_config(config)
                print("[音频模式] 已切到耳机")
                asyncio.run(speak("好的，切到耳机模式。", "开心")); continue
            if any(kw in ui for kw in ["切到音箱", "切换到音箱", "音箱模式", "用音箱", "外放"]):
                config["audio_mode"] = "speaker"; save_config(config)
                print("[音频模式] 已切到音箱")
                asyncio.run(speak("好的，切到音箱模式。", "开心")); continue

            ui_clean = ui.strip().rstrip("。！!，,、. ")
            if any(kw in ui_clean for kw in END_CHAT_KW):
                print(f"[结束对话] 回到待机")
                asyncio.run(speak("好的，我先回待机啦，下次叫我就好哦~", "开心"))
                conv_until = 0
                if tray: tray.set_status("sleep")
                continue

            hit_verb = any(v in ui_clean for v in EXIT_VERBS)
            hit_wake = any(w in ui_clean for w in WAKE_VARIANTS)
            if ui_clean in ["退出", "quit", "exit"] or (hit_verb and hit_wake):
                asyncio.run(speak("好的，下次见哦~", "开心")); break

            ac = client; ap = provider; using_fb = False
            if not is_online():
                if fb_client and fb:
                    ac = fb_client; ap = fb; using_fb = True
                    print(f"[网络] 离线，用本地大脑")
            elif cooldown > 0:
                if fb_client:
                    ac = fb_client; ap = fb; using_fb = True
                    cooldown -= 1
            if not using_fb:
                sens, reason = is_sensitive(ui)
                if sens and fb_client:
                    ac = fb_client; ap = fb; using_fb = True
                    cooldown = SENSITIVE_COOLDOWN_TURNS
                    print(f"[敏感-{reason}] 切本地")

            s.mood_mgr.update_from_message(ui)
            print("绯木：思考中...", end="\r")
            if tray: tray.set_status("think")

            # ══════════════════════════════════════════════
            # B 阶段：反馈捕获
            # ══════════════════════════════════════════════
            _fb = _do_feedback_capture(ui)
            _need_research = bool(_fb and _fb.get("type") == "negative")

            # ══════════════════════════════════════════════
            # B 阶段：知识门控
            # 反馈消息本身不再跑门控（它已经处理完了）
            # ══════════════════════════════════════════════
            _gate = None

            if _fb and _fb.get("type") != "neutral":
                # 这是反馈消息 → 只做重搜，不跑门控
                if _need_research and _fb.get("topic"):
                    print(f"[反馈] 否定触发重搜: {_fb['topic']}")
                    try:
                        from brain.inner_life import _do_explore
                        _do_explore(_fb["topic"], ac, ap, force=True)
                        # 重搜后重新读取 L0 状态（不再跑 quick_check）
                        try:
                            from brain.l0_model import get_opinion
                            _op = get_opinion(_fb["topic"])
                            if _op:
                                _gate = {
                                    "l0_topic": _fb["topic"],
                                    "l0_view": _op.get("my_view", ""),
                                    "l0_confidence": _op.get("confidence", 0.5),
                                    "need_search": False,
                                }
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"[反馈] 重搜失败: {e}")
            else:
                # 正常消息 → 走门控
                _gate = _do_knowledge_gate(ui)

                if _gate and _gate.get("need_search"):
                    _sq_search = _gate.get("search_query") or ui[:40]
                    print(f"[知识门控] 触发搜索: {_sq_search}")
                    try:
                        from brain.inner_life import _do_explore
                        _do_explore(_sq_search, ac, ap, force=True)
                        # 重新读一遍 L0
                        try:
                            from brain.l0_model import get_opinion
                            _op = get_opinion(_sq_search)
                            if _op:
                                _gate = {
                                    "l0_topic": _sq_search,
                                    "l0_view": _op.get("my_view", ""),
                                    "l0_confidence": _op.get("confidence", 0.5),
                                    "need_search": False,
                                }
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"[知识门控] 搜索失败: {e}")

            # L0 认知注入已经在 streaming.py / ask_ai 里做了
            # 这里只保留 history 引用
            _history_with_gate = history

            # 流式对话
            _sq = queue.Queue(maxsize=3)

            def _producer():
                try:
                    from brain.streaming import ask_ai_streaming
                    ask_ai_streaming(
                        ac, ap, _history_with_gate, ui, session_start,
                        speaker=speaker, relation=relation,
                        source=current_source, out_queue=_sq
                    )
                except Exception as e:
                    print(f"[流式生产者] 异常: {e}")
                    try: _sq.put(None)
                    except: pass

            _producer_thread = threading.Thread(target=_producer, daemon=True)
            _producer_thread.start()

            _first_sentence = [None]
            def _peek_first():
                try:
                    item = _sq.get(timeout=30)
                    _first_sentence[0] = item
                    _sq.put(item)
                except Exception:
                    pass

            _peek_thread = threading.Thread(target=_peek_first, daemon=True)
            _peek_thread.start()
            _peek_thread.join(timeout=32)

            if _first_sentence[0] is None:
                print("[流式] 无回复\n")
                if tray: tray.set_status("sleep")
                _producer_thread.join(timeout=2)
                continue

            print(f"绯木：（流式输出中）")

            try:
                from voice.player import speak_stream
                was_interrupted = asyncio.run(
                    speak_stream(_sq, vmc.detect_emotion(_first_sentence[0] or ""))
                )
            except Exception as e:
                print(f"[流式播放] 异常: {e}")
                was_interrupted = False

            _producer_thread.join(timeout=5)

            # 完整对话历史
            try:
                from brain.full_history import append as _fh_append
                _rep = ""
                for _m in reversed(history):
                    if _m.get("role") == "assistant" and _m.get("content"):
                        _rep = _m["content"].strip()
                        break
                _fh_append(ui, _rep, source=current_source)
            except Exception as e:
                print(f"[full_history] 异常: {e}")

            # ═══ B 阶段：记录本轮 reply 和 topics ═══
            try:
                _my_reply = ""
                for _m in reversed(history):
                    if _m.get("role") == "assistant" and _m.get("content"):
                        _my_reply = _m["content"].strip()
                        break
                s.last_turn_reply = _my_reply

                try:
                    from brain.l0_model import list_opinions
                    _ops = list_opinions()
                    _matched = []
                    _haystack = (ui or "") + " " + (_my_reply or "")
                    for _t in _ops.keys():
                        if _t and _t in _haystack:
                            _matched.append(_t)
                    s.last_turn_topics = _matched
                except Exception:
                    s.last_turn_topics = []
            except Exception as e:
                print(f"[反馈] 记录轮次信息失败: {e}")

            # MC 打字
            if from_mc:
                try:
                    from tools.minecraft import mc_say
                    from brain.llm import _split_for_mc
                    if len(history) >= 2 and history[-1].get("role") == "assistant":
                        rep = history[-1].get("content", "")
                        parts = _split_for_mc(rep, max_chars=60, max_parts=2)
                        for i, p in enumerate(parts):
                            if i > 0:
                                time.sleep(0.4)
                            mc_say(p)
                            print(f"[MC打字 {i+1}/{len(parts)}] {p[:40]}")
                except Exception as e:
                    print(f"[MC打字] 失败: {e}")

            if was_interrupted:
                s.last_interrupted = True
                print("[打断理解] 已记录，下轮对话她会知道")

            # L2 + L4 异步执行（不阻塞主循环）
            _self_last = ""
            for _m in reversed(history):
                if _m.get("role") == "assistant" and _m.get("content"):
                    _self_last = _m["content"].strip()
                    break
            threading.Thread(
                target=_post_turn_async,
                args=(ac, ap, _self_last, ui, current_source, was_interrupted,
                      _pending_reflections),
                daemon=True
            ).start()

            # 反思触发
            try:
                from brain.persona import needs_reflection
                from brain.reflect import reflect_async
                if needs_reflection(threshold=20):
                    print("[人格] 累计20轮，触发反思...")
                    reflect_async(ac, ap, history, reason="turns")
            except Exception as e:
                print(f"[人格] 反思触发异常: {e}")

            print(f"   [计时] 本轮 {time.time()-t_cycle:.2f}s\n")
            if tray: tray.set_status("active")

        except KeyboardInterrupt:
            print("\n\n收到 Ctrl+C"); s.shutdown_flag.set(); break
        except Exception as e:
            logger.error(f"主循环出错：{e}")
            save_crash_report(sys.exc_info())
            try: asyncio.run(speak("出了点小问题，继续陪你。", "难过"))
            except: pass
            time.sleep(2); continue

    _input_stop.set()

    if tray:
        try: tray.icon.stop()
        except: pass

    mark_clean_exit()
    print("\n绯木已安全退出~")


if __name__ == "__main__":
    def _onexit(): mark_clean_exit()
    atexit.register(_onexit)
    def _sig(s, f):
        print("\n[收到退出信号]")
        st = state.get_state()
        st.shutdown_flag.set()
        st.interrupt_listen.set()
    try:
        _signal.signal(_signal.SIGINT, _sig)
        _signal.signal(_signal.SIGTERM, _sig)
    except: pass
    main()