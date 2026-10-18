"""播放 + 打断 + 流式播放"""
import os
import json
import time
import math
import re
import asyncio
import threading
import pygame
import queue
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
            # ★ 删除了旧的正弦波，现在头部由 vmc.py 的 start_mouth_sync 接管
            s.life_sim.update()
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
# 流式播放：从队列取句子，逐句合成、播放
# ══════════════════════════════════════════════════════════════
async def speak_stream(sentence_queue, emotion="平静"):
    """从队列逐句消费。使用双缓冲队列实现边播边合成，消除卡顿。"""
    import queue
    import asyncio

    s = state.get_state()

    if s.mute_mode:
        print(f"   [静音模式] 跳过 TTS")
        try: vmc.set_expression(emotion)
        except: pass
        while True:
            try:
                item = sentence_queue.get(timeout=60)
            except queue.Empty:
                break
            if item is None:
                break
        return False

    text_mode = s.text_mode
    t_start = time.time()
    
    mode = _get_mode()
    if mode == "headphone":
        _c.ECHO_SUPPRESS_FACTOR = 1.8
    else:
        _c.ECHO_SUPPRESS_FACTOR = 3.5
    et = INTERRUPT_THRESHOLD * _c.ECHO_SUPPRESS_FACTOR

    print(f"   [流式播放] {'🎧 耳机' if mode=='headphone' else '🔊 音箱'}")
    print(f"表情：{emotion}")
    try: vmc.set_expression(emotion)
    except: pass

    was_int = False
    s.is_speaking = True
    iflag = [False]

    # ★ 双缓冲核心：音频预合成队列
    audio_queue = asyncio.Queue(maxsize=3)

    async def synth_producer():
        """生产者：不断从文本队列拿文字，合成音频，塞进音频队列"""
        idx = 0
        buffer_text = ""
        try:
            while True:
                try:
                    item = sentence_queue.get(timeout=1.0)
                except queue.Empty:
                    continue 
                except Exception:
                    break
                    
                if item is None:
                    if not buffer_text:
                        break
                    else:
                        item = buffer_text
                        buffer_text = ""
                else:
                    buffer_text += item
                    clean_len = len(re.sub(r'[^\w\u4e00-\u9fff]', '', buffer_text))
                    if clean_len < 2:  # ★ 允许极短的思考声立刻发出来
                        continue
                    item = buffer_text
                    buffer_text = ""
                
                print(f"绯木：{item}", flush=True)

                sub_sentences = split_by_lang(item)
                for lang, text in sub_sentences:
                    if not text.strip():
                        continue
                    if iflag[0]:
                        break
                        
                    idx += 1
                    audio_path = await synth_one(text, lang, idx, t_start)
                    if audio_path:
                        await audio_queue.put(audio_path)
                
                if iflag[0]:
                    break
        finally:
            await audio_queue.put(None) # 结束标志

    # 启动后台合成任务
    prod_task = asyncio.create_task(synth_producer())

    try:
        while True:
            try:
                audio_path = await asyncio.wait_for(audio_queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                break
                
            if audio_path is None:
                break # 全部合成且播放完毕

            # 开始播放
            if text_mode:
                await _play_no_mic_simple(audio_path)
            else:
                if mode == "headphone":
                    _is_int = await _play_full_duplex(audio_path, iflag, t_start, et)
                else:
                    _is_int = await _play_no_mic(audio_path)
                    
                if _is_int:
                    was_int = True
                    break

    except Exception as e:
        print(f"[流式播放] 异常: {e}")
    finally:
        iflag[0] = True
        s.is_speaking = False
        if not prod_task.done():
            prod_task.cancel()
        
        # 清空残留
        while not sentence_queue.empty():
            try: sentence_queue.get_nowait()
            except queue.Empty: break
        while not audio_queue.empty():
            p = audio_queue.get_nowait()
            if p:
                try: os.remove(p)
                except: pass

    if was_int:
        print("\n[用户插嘴，打断绯木]")
        try: vmc.set_expression("关切")
        except: pass

    return was_int