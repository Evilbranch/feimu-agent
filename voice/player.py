"""播放 + 打断 + 流式播放"""
import os
import json
import time
import math
import asyncio
import threading
import pygame
import sounddevice as sd
import numpy as np
from core.constants import (INTERRUPT_THRESHOLD, INTERRUPT_REQUIRED_FRAMES,
    POST_PLAYBACK_SILENCE, PYGAME_VOLUME, STREAM_QUEUE_SIZE, CONFIG_FILE)
from core import state
from core.device import detect_audio_mode
from voice import vmc
from voice.tts import split_sentences, split_by_lang, synth_one
import core.constants as _c


def _get_mode():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return detect_audio_mode(cfg)
    except:
        return "speaker"


def _sample_volume(duration=0.15, device=None):
    samples = []
    def cb(indata, frames, ti, status):
        samples.append(float(np.mean(np.abs(indata))))
    try:
        with sd.InputStream(samplerate=16000, channels=1, callback=cb,
                            blocksize=800, device=device):
            time.sleep(duration)
    except:
        return 0.0
    return max(samples) if samples else 0.0


# ══════════════════════════════════════════════════════════════
# 文本模式：纯播放，不监听打断
# ══════════════════════════════════════════════════════════════
async def _play_no_mic_simple(path):
    s = state.get_state()
    try:
        pygame.mixer.music.set_volume(PYGAME_VOLUME)
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        vmc.start_mouth_sync(path)
    except Exception as e:
        print(f"   [播放失败] {e}")
        try: os.remove(path)
        except: pass
        return

    fc = 0
    while pygame.mixer.music.get_busy():
        if s.shutdown_flag.is_set():
            vmc.stop_mouth_sync()
            try: pygame.mixer.music.stop(); pygame.mixer.music.unload()
            except: pass
            try: os.remove(path)
            except: pass
            return
        fc += 1
        if fc % 3 == 0 and s.life_sim:
            s.life_sim.update()
        await asyncio.sleep(0.033)

    vmc.stop_mouth_sync()
    try: pygame.mixer.music.stop(); pygame.mixer.music.unload()
    except: pass
    try: os.remove(path)
    except: pass


# ══════════════════════════════════════════════════════════════
# 音箱模式：暂停-确认法智能打断
# ══════════════════════════════════════════════════════════════
async def _play_no_mic(path):
    s = state.get_state()
    try:
        pygame.mixer.music.set_volume(PYGAME_VOLUME)
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        vmc.start_mouth_sync(path)
    except Exception as e:
        print(f"   [播放失败] {e}")
        try: os.remove(path)
        except: pass
        return False

    time.sleep(0.5)
    baseline = _sample_volume(0.3, s.audio_device)
    detect_threshold = max(baseline * 2.0, 0.18)
    print(f"   [音箱检测] 自身基线 {int(baseline*1000)}，触发阈值 {int(detect_threshold*1000)}")

    interrupt = [False]
    stop_check = threading.Event()

    def check_loop():
        while not stop_check.is_set():
            if not pygame.mixer.music.get_busy():
                return
            v = _sample_volume(0.15, s.audio_device)
            if v > detect_threshold:
                try: pygame.mixer.music.pause()
                except: pass
                time.sleep(0.8)
                v2 = _sample_volume(0.4, s.audio_device)
                if v2 > 0.10:
                    print(f"\n🔊 [音箱打断] 确认用户说话 (音量 {int(v2*1000)} > 100)")
                    interrupt[0] = True
                    stop_check.set()
                    return
                else:
                    try: pygame.mixer.music.unpause()
                    except: pass
            time.sleep(0.05)

    threading.Thread(target=check_loop, daemon=True).start()

    fc = 0
    while pygame.mixer.music.get_busy():
        if s.shutdown_flag.is_set():
            stop_check.set()
            vmc.stop_mouth_sync()
            try: pygame.mixer.music.stop(); pygame.mixer.music.unload()
            except: pass
            try: os.remove(path)
            except: pass
            return False
        if interrupt[0]:
            stop_check.set()
            vmc.stop_mouth_sync()
            try: pygame.mixer.music.stop(); pygame.mixer.music.unload()
            except: pass
            try: os.remove(path)
            except: pass
            return True
        fc += 1
        if fc % 3 == 0 and s.life_sim:
            s.life_sim.update()
        await asyncio.sleep(0.033)

    stop_check.set()
    vmc.stop_mouth_sync()
    try: pygame.mixer.music.stop(); pygame.mixer.music.unload()
    except: pass
    try: os.remove(path)
    except: pass
    return False


async def _play_full_duplex(path, iflag, base_time, et):
    s = state.get_state()
    if iflag[0]:
        try: os.remove(path)
        except: pass
        return True
    try:
        pygame.mixer.music.set_volume(PYGAME_VOLUME)
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        vmc.start_mouth_sync(path)
    except Exception as e:
        print(f"   [播放失败] {e}")
        try: os.remove(path)
        except: pass
        return False

    ic = [0]
    def icb(indata, frames, ti, status):
        v = np.mean(np.abs(indata))
        print(f"🎤 音量 {int(v*1000)} / 阈值 {int(et*1000)}   ", end="\r")
        if v > et:
            ic[0] += 1
            if ic[0] >= INTERRUPT_REQUIRED_FRAMES:
                iflag[0] = True
                print(f"\n🔊 [打断] {int(v*1000)} > {int(et*1000)}")
        else: ic[0] = 0

    sc = None
    try:
        sc = sd.InputStream(samplerate=16000, channels=1, callback=icb,
                            blocksize=800, device=s.audio_device)
        sc.__enter__()
    except Exception as e:
        print(f"   [打断监听失败] {e}"); sc = None

    fc = 0
    while pygame.mixer.music.get_busy():
        if s.shutdown_flag.is_set():
            vmc.stop_mouth_sync()
            try: pygame.mixer.music.stop(); pygame.mixer.music.unload()
            except: pass
            if sc:
                try: sc.__exit__(None, None, None)
                except: pass
            try: os.remove(path)
            except: pass
            return True
        fc += 1
        if fc % 3 == 0 and s.life_sim:
            s.life_sim.update()
            if s.vmc_client:
                el = time.time() - base_time
                tn = math.sin(el*4)*0.04; ty = math.sin(el*2.3)*0.03
                vmc._head(s.life_sim.head_pitch + tn, s.life_sim.head_yaw + ty, 0.0)
        if iflag[0]:
            vmc.stop_mouth_sync()
            try: pygame.mixer.music.stop(); pygame.mixer.music.unload()
            except: pass
            if sc:
                try: sc.__exit__(None, None, None)
                except: pass
            try: os.remove(path)
            except: pass
            return True
        await asyncio.sleep(0.033)

    vmc.stop_mouth_sync()
    if sc:
        try: sc.__exit__(None, None, None)
        except: pass
    try: pygame.mixer.music.stop(); pygame.mixer.music.unload()
    except: pass
    try: os.remove(path)
    except: pass
    return False


# ══════════════════════════════════════════════════════════════
# 主入口：整段 speak
# ══════════════════════════════════════════════════════════════
async def speak(text, emotion="平静"):
    s = state.get_state()

    if s.mute_mode:
        print(f"   [静音模式] 跳过 TTS")
        try: vmc.set_expression(emotion)
        except: pass
        return False

    text_mode = s.text_mode
    t_start = time.time()

    mode = _get_mode()
    if mode == "headphone":
        _c.ECHO_SUPPRESS_FACTOR = 1.2
    else:
        _c.ECHO_SUPPRESS_FACTOR = 3.5
    et = INTERRUPT_THRESHOLD * _c.ECHO_SUPPRESS_FACTOR

    sents = split_sentences(text)
    final = []
    for sent in sents:
        for lg, pc in split_by_lang(sent):
            if pc.strip(): final.append((lg, pc.strip()))
    if not final: return False

    print(f"   [音频模式] {'🎧 耳机' if mode=='headphone' else '🔊 音箱'}"
          f"{'（文本模式，无打断）' if text_mode else '（阈值 '+str(int(et*1000))+'）'}")
    print(f"   [流式] 切 {len(final)} 段")
    for i, (lg, pc) in enumerate(final): print(f"      {i+1}. ({lg}) {pc}")
    print(f"表情：{emotion}")
    vmc.set_expression(emotion)

    iflag = [False]
    q = asyncio.Queue(maxsize=STREAM_QUEUE_SIZE + 2)

    async def producer():
        for idx, (lg, pc) in enumerate(final):
            if iflag[0] or s.shutdown_flag.is_set():
                break
            t1 = time.time()
            p = await synth_one(pc, lg, idx, t_start)
            if p:
                print(f"   [流式] 合成 {idx+1}/{len(final)}（{lg}）耗时 {time.time()-t1:.2f}s")
                try:
                    await asyncio.wait_for(q.put(p), timeout=120)
                except:
                    break
        try:
            await q.put(None)
        except:
            pass

    prod = asyncio.create_task(producer())
    was_int = False

    try:
        first = False
        while True:
            if s.shutdown_flag.is_set():
                print("\n[退出信号] 停止播放")
                was_int = True
                break
            try: p = await asyncio.wait_for(q.get(), timeout=90)
            except: break
            if p is None: break
            if not first:
                print(f"   [流式] ✅ 首句就绪 {time.time()-t_start:.2f}s")
                first = True

            if text_mode:
                await _play_no_mic_simple(p)
                if s.shutdown_flag.is_set():
                    was_int = True
                    break
                continue

            if mode == "headphone":
                wi = await _play_full_duplex(p, iflag, t_start, et)
                if wi:
                    if not s.shutdown_flag.is_set():
                        print("\n[用户插嘴，打断绯木]")
                    was_int = True
                    break
            else:
                wi = await _play_no_mic(p)
                if wi:
                    print("\n[用户插嘴，打断绯木]")
                    was_int = True
                    break
    finally:
        iflag[0] = True
        vmc.stop_mouth_sync()
        if prod and not prod.done(): prod.cancel()
        while not q.empty():
            try:
                p = q.get_nowait()
                if p:
                    try: os.remove(p)
                    except: pass
            except: pass
        try: pygame.mixer.music.stop(); pygame.mixer.music.unload()
        except: pass
        try: pygame.mixer.stop()
        except: pass

    await asyncio.sleep(POST_PLAYBACK_SILENCE)
    await asyncio.sleep(1.0)
    vmc.set_expression("平静")
    return was_int


# ══════════════════════════════════════════════════════════════
# 🆕 流式播放：从队列取句子，逐句合成、播放
# ══════════════════════════════════════════════════════════════
async def speak_stream(sentence_queue, emotion="平静"):
    """从队列逐句消费。队列里放句子字符串，None 表示结束。"""
    from voice.tts import synth_one

    s = state.get_state()

    if s.mute_mode:
        print(f"   [静音模式] 跳过 TTS")
        try: vmc.set_expression(emotion)
        except: pass
        while True:
            try:
                item = sentence_queue.get(timeout=60)
            except:
                break
            if item is None:
                break
        return False

    text_mode = s.text_mode
    t_start = time.time()

    mode = _get_mode()
    if mode == "headphone":
        _c.ECHO_SUPPRESS_FACTOR = 1.2
    else:
        _c.ECHO_SUPPRESS_FACTOR = 3.5
    et = INTERRUPT_THRESHOLD * _c.ECHO_SUPPRESS_FACTOR

    print(f"   [流式播放] {'🎧 耳机' if mode=='headphone' else '🔊 音箱'}"
          f"{'（文本模式）' if text_mode else ''}")
    print(f"表情：{emotion}")
    try: vmc.set_expression(emotion)
    except: pass

    iflag = [False]
    was_int = False
    idx = 0

    while True:
        if s.shutdown_flag.is_set():
            was_int = True
            break

        try:
            sentence = await asyncio.get_event_loop().run_in_executor(
                None, lambda: sentence_queue.get(timeout=120)
            )
        except Exception:
            break

        if sentence is None:
            break

        if s.shutdown_flag.is_set():
            was_int = True
            break

        t1 = time.time()
        try:
            path = await synth_one(sentence, "auto", idx, t_start)
        except Exception as e:
            print(f"   [流式] 合成异常: {e}")
            idx += 1
            continue

        if not path:
            idx += 1
            continue

        print(f"   [流式] 合成 {idx+1} 耗时 {time.time()-t1:.2f}s: {sentence[:30]}")

        if text_mode:
            await _play_no_mic_simple(path)
        elif mode == "headphone":
            wi = await _play_full_duplex(path, iflag, t_start, et)
            if wi:
                if not s.shutdown_flag.is_set():
                    print("\n[用户插嘴，打断绯木]")
                was_int = True
                break
        else:
            wi = await _play_no_mic(path)
            if wi:
                print("\n[用户插嘴，打断绯木]")
                was_int = True
                break

        idx += 1

    while not sentence_queue.empty():
        try:
            p = sentence_queue.get_nowait()
            if p is None:
                break
        except:
            break

    await asyncio.sleep(0.3)
    try: vmc.set_expression("平静")
    except: pass
    return was_int