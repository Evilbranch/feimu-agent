"""Whisper + 唤醒词 + 录音 + 语音识别"""
import os
import re
import time
import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write
from core.constants import (WHISPER_MODEL_PATH, WHISPER_MODEL_PATH_WAKE,
    WHISPER_BEAM_SIZE, WHISPER_BEAM_SIZE_WAKE, WHISPER_TEMPERATURE,
    WHISPER_VAD_FILTER, WHISPER_CONDITION_PREV, WAKE_WORDS, WAKE_LISTEN_TIMEOUT,
    WAKE_VAD_THRESHOLD, SILENCE_THRESHOLD, SILENCE_DURATION, MAX_RECORD_TIME,
    HALLUCINATION_BLACKLIST, SCRIPT_DIR)
from core import state


# 🆕 对话阶段的 initial_prompt（帮助 Whisper 识别常用词/专有名词）
DIALOG_INITIAL_PROMPT = (
    "和绯木对话。常用词："
    "打开、关闭、启动、关掉、运行、退出、"
    "提醒、重写、删掉、删除、日记、天气、总结、搜索、"
    "OpenAI、DeepSeek、GPT、Python、VSCode、CMD、Chrome、"
    "微信、浏览器、记事本、计算器、任务管理器、画图。"
    "开、关、是、不、对、错。"
)


# ==================== 幻觉检测工具箱 ====================
def _check_hallucination(text):
    if not text:
        return True, "空文本"
    text = text.strip()
    if len(text) < 2:
        return True, "太短"
    for bad in HALLUCINATION_BLACKLIST:
        if bad in text:
            return True, f"黑名单【{bad}】"
    letters = len(re.findall(r'[\u4e00-\u9fff a-zA-Z]', text))
    if letters / len(text) < 0.5:
        return True, "符号占比过高"
    if re.search(r'(.)\1{3,}', text):
        return True, "单字重复"
    for chunk_size in [2, 3]:
        if len(text) >= chunk_size * 4:
            chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
            if len(chunks) >= 4:
                first = chunks[0]
                same_count = sum(1 for c in chunks if c == first)
                if same_count >= 4:
                    return True, f"'{first}'重复{same_count}次"
    if len(text) >= 6:
        half = len(text) // 2
        if text[:half] == text[half:half*2]:
            return True, "前后半段重复"
    return False, ""


def _check_hallucination_wake(text):
    bad, reason = _check_hallucination(text)
    if bad:
        return True, reason
    # 🆕 只要出现"绯木"就允许唤醒
    wake_any = ["绯木", "緋木", "飞木", "飛木", "肥木", "菲木", "非木",
                "费木", "飛慕", "飞慕", "绯慕", "被摸"]
    if any(w in text for w in wake_any):
        return False, ""
    if len(text) > 15:
        return True, "唤醒文本过长"
    return False, ""


def init_whisper_bg():
    s = state.get_state()
    try:
        from faster_whisper import WhisperModel
        print("加载语音识别模型...")
        try:
            s.whisper_model_wake = WhisperModel(WHISPER_MODEL_PATH_WAKE, device="cpu", compute_type="int8")
            print("✅ 唤醒模型加载完成")
        except Exception as e:
            print(f"⚠️ 唤醒模型失败：{e}"); s.whisper_model_wake = None
        s.whisper_model = WhisperModel(WHISPER_MODEL_PATH, device="cpu", compute_type="int8")
        print("✅ 对话模型加载完成")
    except Exception as e:
        print(f"语音识别加载失败：{e}")
    finally: s.whisper_ready.set()


def warmup():
    s = state.get_state()
    try:
        wp = os.path.join(SCRIPT_DIR, "warmup.wav")
        write(wp, 16000, np.zeros(16000, dtype=np.int16))
        if s.whisper_model:
            t0 = time.time()
            segs, _ = s.whisper_model.transcribe(wp, beam_size=1, language="zh", vad_filter=False, temperature=0.0)
            _ = list(segs); print(f"✅ 对话模型预热 {time.time()-t0:.2f}s")
        if s.whisper_model_wake and s.whisper_model_wake is not s.whisper_model:
            t0 = time.time()
            segs2, _ = s.whisper_model_wake.transcribe(wp, beam_size=1, language="zh", vad_filter=False, temperature=0.0)
            _ = list(segs2); print(f"✅ 唤醒模型预热 {time.time()-t0:.2f}s")
        try: os.remove(wp)
        except: pass
    except Exception as e: print(f"⚠️ 预热失败：{e}")


def listen_wake(filename="wake.wav", fs=16000, timeout=WAKE_LISTEN_TIMEOUT):
    s = state.get_state()
    if not hasattr(listen_wake, "_p"):
        print(f"💤 待机中...（说『绯木』唤醒）")
        listen_wake._p = True
    rec = []; sil = 0; cs = 480; mv = 0.0

    def cb(indata, frames, ti, status):
        nonlocal sil, mv
        v = np.mean(np.abs(indata)); mv = max(mv, v)
        if v > WAKE_VAD_THRESHOLD: sil = 0; rec.append(indata.copy())
        else:
            if len(rec) > 0: sil += 1; rec.append(indata.copy())

    try:
        with sd.InputStream(samplerate=fs, channels=1, callback=cb,
                            blocksize=cs, device=s.audio_device):
            start = time.time()
            while not s.shutdown_flag.is_set():
                time.sleep(0.1)
                # 🆕 检查中断信号（F2/F3 按下时）
                if s.interrupt_listen.is_set():
                    s.interrupt_listen.clear()
                    return False
                if len(rec) > 0 and sil > (0.6 * 1000 / 30): break
                if time.time() - start > timeout: break
    except Exception as e:
        print(f"[录音出错] {e}"); return False

    if s.shutdown_flag.is_set(): return False
    if not rec: return False
    if mv < 0.15: return False
    al = len(rec) * 480 / 16000
    if al < 0.3 or al > 2.0: return False

    try:
        ad = np.concatenate(rec, axis=0); write(filename, fs, ad)
        we = s.whisper_model_wake if s.whisper_model_wake else s.whisper_model
        t0 = time.time()
        segs, _ = we.transcribe(filename, beam_size=WHISPER_BEAM_SIZE_WAKE,
            language="zh", vad_filter=False, temperature=0.0,
            condition_on_previous_text=False, initial_prompt="绯木")
        text = "".join(seg.text for seg in segs).strip()
        print(f"   [诊断] 音量 {int(mv*1000)}，听到：『{text[:50]}』（{time.time()-t0:.2f}s）")

        # 🆕 转写完立刻删除录音
        try: os.remove(filename)
        except: pass

        if not text: return False
        bad, reason = _check_hallucination_wake(text)
        if bad:
            print(f"   [唤醒过滤-{reason}]")
            return False
        try:
            from pypinyin import lazy_pinyin
            tp = "".join(lazy_pinyin(text)).lower()
            if "feimu" in tp or "fei mu" in tp: print(f"✨ 唤醒（拼音）"); return True
            if "fei" in tp and "mu" in tp: print(f"✨ 唤醒（拼音）"); return True
        except: pass
        tc = text.replace(" ", "").replace("，", "").replace(",", "").replace("。", "").lower()
        for ww in WAKE_WORDS:
            if ww in tc: print(f"✨ 唤醒（{ww}）"); return True
        for nw in ["被摸", "飞陌", "飞魔", "肥猫", "绯某", "费某", "非木", "菲木",
                   "绯慕", "妃木", "飞慕", "非慕"]:
            if nw in tc: print(f"✨ 唤醒（谐音 {nw}）"); return True
        return False
    except Exception as e:
        print(f"[唤醒失败] {e}"); return False


def listen_record(filename="input.wav", fs=16000):
    s = state.get_state()
    print("🎤 正在听你说话...")
    rec = []; sil = 0; cs = 480

    def cb(indata, frames, ti, status):
        nonlocal sil
        v = np.mean(np.abs(indata))
        print(f"音量 {int(v*1000)} / 阈值 {int(SILENCE_THRESHOLD*1000)}   ", end="\r")
        if v > SILENCE_THRESHOLD: sil = 0; rec.append(indata.copy())
        else:
            if len(rec) > 0: sil += 1; rec.append(indata.copy())

    try:
        with sd.InputStream(samplerate=fs, channels=1, callback=cb,
                            blocksize=cs, device=s.audio_device):
            start = time.time()
            while not s.shutdown_flag.is_set():
                time.sleep(0.1)
                # 🆕 检查中断信号（F2/F3 按下时）
                if s.interrupt_listen.is_set():
                    s.interrupt_listen.clear()
                    return False
                if len(rec) > 0 and sil > (SILENCE_DURATION * 1000 / 30): break
                if time.time() - start > MAX_RECORD_TIME: break
    except Exception as e:
        print(f"[录音出错] {e}"); return False
    if s.shutdown_flag.is_set(): return False
    if len(rec) > 0:
        ad = np.concatenate(rec, axis=0); write(filename, fs, ad); return True
    return False


def speech_to_text(filename="input.wav"):
    s = state.get_state()
    t0 = time.time()
    try:
        segs, _ = s.whisper_model.transcribe(
            filename, beam_size=WHISPER_BEAM_SIZE,
            language="zh", vad_filter=WHISPER_VAD_FILTER,
            temperature=WHISPER_TEMPERATURE,
            condition_on_previous_text=WHISPER_CONDITION_PREV,
            no_speech_threshold=0.6,
            initial_prompt=DIALOG_INITIAL_PROMPT,
        )
        text = "".join(seg.text for seg in segs).strip()
        print(f"   [计时] 转写 {time.time()-t0:.2f}s   ")

        # 🆕 转写完立刻删除录音
        try: os.remove(filename)
        except: pass

        bad, reason = _check_hallucination(text)
        if bad:
            print(f"[过滤幻觉-{reason}]")
            return ""

        return text
    except Exception as e:
        print(f"[语音识别出错] {e}"); return ""