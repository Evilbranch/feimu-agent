"""Whisper + 唤醒词 + 录音 + 语音识别"""
import os
import re
import time
import numpy as np
import sounddevice as sd
import webrtcvad
from scipy.io.wavfile import write
from core.constants import (WHISPER_MODEL_PATH, WHISPER_MODEL_PATH_WAKE,
    WHISPER_BEAM_SIZE, WHISPER_BEAM_SIZE_WAKE, WHISPER_TEMPERATURE,
    WHISPER_VAD_FILTER, WHISPER_CONDITION_PREV, WAKE_WORDS, WAKE_LISTEN_TIMEOUT,
    WAKE_VAD_THRESHOLD, SILENCE_THRESHOLD, SILENCE_DURATION, MAX_RECORD_TIME,
    HALLUCINATION_BLACKLIST, SCRIPT_DIR)
from core import state

DIALOG_INITIAL_PROMPT = (
    "和绯木对话。常用词："
    "打开、关闭、启动、关掉、运行、退出、"
    "提醒、重写、删掉、删除、日记、天气、总结、搜索、"
    "OpenAI、DeepSeek、GPT、Python、VSCode、CMD、Chrome、"
    "微信、浏览器、记事本、计算器、任务管理器、画图。"
    "开、关、是、不、对、错。"
)

# ★ 新增：全局音频特征存储
_last_audio_stats = {"max_vol": 0.0, "avg_vol": 0.0}
def get_last_audio_stats():
    return _last_audio_stats

def _check_hallucination(text):
    if not text: return True, "空文本"
    text = text.strip()
    if len(text) < 2: return True, "太短"
    if len(text) > 40: return True, "文本过长，疑似背景音幻觉"
    if text in ["字幕", "字幕由", "请不吝点赞", "明镜与点点", "谢谢观看"]:
        return True, "视频黑名单"
    for bad in HALLUCINATION_BLACKLIST:
        if bad in text: return True, f"黑名单【{bad}】"
    letters = len(re.findall(r'[\u4e00-\u9fff a-zA-Z]', text))
    if letters / len(text) < 0.5: return True, "符号占比过高"
    if re.search(r'(.)\1{3,}', text): return True, "单字重复"
    return False, ""

def _check_hallucination_wake(text):
    bad, reason = _check_hallucination(text)
    if bad: return True, reason
    wake_any = ["绯木", "緋木", "飞木", "飛木", "肥木", "菲木", "非木", "费木", "飛慕", "飞慕", "绯慕", "被摸"]
    if any(w in text for w in wake_any): return False, ""
    if len(text) > 15: return True, "唤醒文本过长"
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
        s.whisper_model = WhisperModel(WHISPER_MODEL_PATH, device="cuda", compute_type="float16")
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
        try: os.remove(wp)
        except: pass
    except Exception as e: print(f"⚠️ 预热失败：{e}")

def listen_wake(filename="wake.wav", fs=16000, timeout=WAKE_LISTEN_TIMEOUT):
    s = state.get_state()
    rec = []; sil = 0; cs = 480; mv = 0.0
    def cb(indata, frames, ti, status):
        nonlocal sil, mv
        v = np.mean(np.abs(indata)); mv = max(mv, v)
        if v > WAKE_VAD_THRESHOLD: sil = 0; rec.append(indata.copy())
        else:
            if len(rec) > 0: sil += 1; rec.append(indata.copy())
    try:
        with sd.InputStream(samplerate=fs, channels=1, callback=cb, blocksize=cs, device=s.audio_device):
            start = time.time()
            while not s.shutdown_flag.is_set():
                time.sleep(0.1)
                if s.interrupt_listen.is_set():
                    s.interrupt_listen.clear()
                    return False
                if len(rec) > 0 and sil > (0.4 * 1000 / 30): break
                if time.time() - start > timeout: break
    except Exception as e:
        return False

    if s.shutdown_flag.is_set() or not rec or mv < 0.08: return False
    al = len(rec) * 480 / 16000
    if al < 0.3 or al > 2.0: return False

    try:
        ad = np.concatenate(rec, axis=0); write(filename, fs, ad)
        we = s.whisper_model_wake if s.whisper_model_wake else s.whisper_model
        segs, _ = we.transcribe(filename, beam_size=WHISPER_BEAM_SIZE_WAKE, language="zh", vad_filter=False, temperature=0.0, condition_on_previous_text=False, initial_prompt="绯木")
        text = "".join(seg.text for seg in segs).strip()
        try: os.remove(filename)
        except: pass
        if not text: return False
        bad, reason = _check_hallucination_wake(text)
        if bad: return False
        try:
            from pypinyin import lazy_pinyin
            tp = "".join(lazy_pinyin(text)).lower()
            if "feimu" in tp or "fei mu" in tp: return True
            if "fei" in tp and "mu" in tp: return True
        except: pass
        tc = text.replace(" ", "").replace("，", "").replace(",", "").replace("。", "").lower()
        for ww in WAKE_WORDS:
            if ww in tc: return True
        for nw in ["被摸", "飞陌", "飞魔", "肥猫", "绯某", "费某", "非木", "菲木", "绯慕", "妃木", "飞慕", "非慕"]:
            if nw in tc: return True
        return False
    except Exception as e:
        return False

# 在文件顶部加一个全局变量
_global_pre_buffer = []

def clear_pre_roll():
    global _global_pre_buffer
    _global_pre_buffer.clear()

def listen_record(filename="input.wav", fs=16000):
    global _global_pre_buffer
    s = state.get_state()
    _global_pre_buffer.clear()
    
    vad = webrtcvad.Vad(3)
    frame_duration_ms = 30
    cs = int(fs * frame_duration_ms / 1000)
    
    rec = []
    silence_frames = 0
    trigger_frames = 0
    has_started = False
    
    TRIGGER_FRAMES_NEEDED = 5
    SILENCE_FRAMES_LIMIT = 15
    MAX_PRE_BUFFER_FRAMES = 15

    def cb(indata, frames, time_info, status):
        nonlocal silence_frames, trigger_frames, has_started, rec
        audio_int16 = (indata.flatten() * 32768).astype(np.int16)
        audio_bytes = audio_int16.tobytes()
        is_speech = vad.is_speech(audio_bytes, fs)

        if not has_started:
            _global_pre_buffer.append(indata.copy())
            if len(_global_pre_buffer) > MAX_PRE_BUFFER_FRAMES:
                _global_pre_buffer.pop(0)
            if is_speech:
                trigger_frames += 1
                if trigger_frames >= TRIGGER_FRAMES_NEEDED:
                    has_started = True
                    silence_frames = 0
                    rec.extend(_global_pre_buffer)
                    rec.append(indata.copy())
            else:
                trigger_frames = 0
            return

        if has_started:
            if is_speech:
                silence_frames = 0
                rec.append(indata.copy())
            else:
                silence_frames += 1
                rec.append(indata.copy())

    try:
        with sd.InputStream(samplerate=fs, channels=1, callback=cb, blocksize=cs, device=s.audio_device):
            start_time = time.time()
            while not s.shutdown_flag.is_set():
                time.sleep(0.05)
                if s.interrupt_listen.is_set():
                    s.interrupt_listen.clear()
                    return False
                if has_started and silence_frames > SILENCE_FRAMES_LIMIT:
                    break
                if time.time() - start_time > MAX_RECORD_TIME:
                    break
    except Exception as e:
        return False
        
    if s.shutdown_flag.is_set() or not has_started:
        return False
        
    if len(rec) > 0:
        audio_length = len(rec) * cs / fs
        if audio_length < 0.3: return False
            
        ad = np.concatenate(rec, axis=0)
        
        # ★ 计算音量特征
        global _last_audio_stats
        _max = float(np.max(np.abs(ad)))
        _avg = float(np.mean(np.abs(ad)))
        _last_audio_stats = {"max_vol": _max, "avg_vol": _avg}
        
        write(filename, fs, ad)
        return True
    return False

def speech_to_text(filename="input.wav"):
    s = state.get_state()
    t0 = time.time()
    try:
        segs, _ = s.whisper_model.transcribe(
            filename, beam_size=WHISPER_BEAM_SIZE, language="zh", 
            vad_filter=WHISPER_VAD_FILTER, temperature=WHISPER_TEMPERATURE,
            condition_on_previous_text=WHISPER_CONDITION_PREV, no_speech_threshold=0.6, 
            initial_prompt=DIALOG_INITIAL_PROMPT,
        )
        text = "".join(seg.text for seg in segs).strip()
        try: os.remove(filename)
        except: pass
        bad, reason = _check_hallucination(text)
        if bad: return ""
        print(f"   [ASR 耗时] {time.time()-t0:.2f}s")
        return text
    except Exception as e:
        return ""