"""GPT-SoVITS TTS 合成 - 细粒度切句 + 流式 + 中文参考统一音色"""
import os
import re
import time
import asyncio
import unicodedata
import requests
from core.constants import (USE_GPT_SOVITS, GPT_SOVITS_API,
    GPT_SOVITS_REF_AUDIO_ZH, GPT_SOVITS_REF_TEXT_ZH,
    GPT_SOVITS_PARAMS_ZH, VOICE, SCRIPT_DIR, STREAM_SPLIT_MAX_LEN)
from core import state

try:
    import edge_tts
    _HAS_EDGE = True
except ImportError:
    _HAS_EDGE = False

# ★ 黑话词典：必须放全，避免 GPT-SoVITS 崩音
_REPLACEMENTS = {
    r'(?i)AI': "人工智能",
    r'(?i)APP': "应用",
    r'(?i)OK': "好的",
    r'(?i)Bug': "漏洞",
    r'(?i)emo': "抑郁",
    r'(?i)Python': "拍森",
    r'(?i)GPT': "人工智能",
    r'(?i)Live2D': "虚拟形象",
    r'(?i)MC': "我的世界",
    r'(?i)up主': "阿婆主",
    r'(?i)CP': "西皮",
    r'(?i)NPC': "恩皮西",
    r'唔': "嗯",       # ★ 加上这行
    r'呃': "那个",     # ★ 加上这行
}

def _clean_text(text):
    """移除拼音声调字符，替换容易读错的词汇，过滤无意义标点"""
    if not text:
        return text
        
    # 1. 替换英文缩写和易错词
    for k, v in _REPLACEMENTS.items():
        text = re.sub(k, v, text)
        
    # 2. 过滤掉纯标点或纯空格
    text = re.sub(r'^[\s，,。.！!？?、~～…]+$', '', text)
    
    # 3. 移除拼音声调
    text = ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )
    
    return text.strip()


# ==================== 细粒度切句 ====================
def split_sentences(text, max_len=None, min_len=5):
    """
    按标点细切，每段 <= max_len 字。
    首段优先短（更快出声），后续段可以略长。
    """
    if not text:
        return []
    if max_len is None:
        max_len = STREAM_SPLIT_MAX_LEN  # 默认从 constants 读，建议 30

    # 按标点切分，标点跟随前句
    parts = re.split(r'([。！？!?\n~～]+)', text)
    sentences = []
    buf = ""
    for p in parts:
        if not p:
            continue
        buf += p
        if re.search(r'[。！？!?\n~～]+$', buf):
            s = buf.strip()
            if s:
                sentences.append(s)
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())

    # 首段优先短：只要第一句遇到第一个逗号，且长度超过 8，立刻切出来
    refined = []
    for si, s in enumerate(sentences):
        if si == 0 and len(s) > 8:
            comma = re.search(r'[，,]', s)
            if comma and comma.start() >= 2:
                head = s[:comma.start() + 1].strip()
                tail = s[comma.start() + 1:].strip()
                if head:
                    refined.append(head)
                if tail:
                    sentences[si] = tail
                    s = tail

        if len(s) <= max_len:
            refined.append(s)
            continue
        # 按逗号切
        sub = re.split(r'([，,、；;])', s)
        cur = ""
        for x in sub:
            if not x:
                continue
            cur += x
            if len(cur) >= max_len or re.search(r'[，,、；;]$', cur):
                if cur.strip():
                    refined.append(cur.strip())
                cur = ""
        if cur.strip():
            refined.append(cur.strip())

    # 合并过短的段（< min_len）
    merged = []
    cur = ""
    for s in refined:
        if not cur:
            cur = s
        elif len(cur) < min_len:
            cur += s
        else:
            merged.append(cur)
            cur = s
    if cur:
        if merged and len(cur) < min_len:
            merged[-1] += cur
        else:
            merged.append(cur)

    # 首句（第一段）如果不足 3 个字，强行和下一句合并
    if len(merged) >= 2 and len(merged[0]) < 3:
        merged[0] = merged[0] + merged[1]
        merged.pop(1)

    return merged


def split_by_lang(sent):
    if not sent or not sent.strip():
        return []
    return [("auto", sent.strip())]


def _build_gpt_sovits_payload(text, lang, idx, streaming=True):
    params = GPT_SOVITS_PARAMS_ZH.copy()
    clean = _clean_text(text)
    if not clean: return None
    text_lang = "auto" if lang == "auto" else lang
    payload = {
        "text": clean,
        "text_lang": text_lang,
        "ref_audio_path": GPT_SOVITS_REF_AUDIO_ZH,
        "prompt_text": GPT_SOVITS_REF_TEXT_ZH,
        "prompt_lang": "zh",
        "text_split_method": "cut0",
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
        **params,
    }
    return payload


async def _synth_gpt_sovits(text, lang, idx):
    out_path = os.path.join(SCRIPT_DIR, f"reply_stream_{int(time.time()*1000)}_{idx}.wav")
    payload = _build_gpt_sovits_payload(text, lang, idx, streaming=False)
    if not payload: return None

    def _do():
        return requests.post(f"{GPT_SOVITS_API}/tts", json=payload, timeout=60)

    try:
        loop = asyncio.get_event_loop()
        r = await loop.run_in_executor(None, _do)
        if r.status_code != 200:
            print(f"[TTS-GPT失败] HTTP {r.status_code}: {r.text[:120]}")
            return None
        with open(out_path, "wb") as f:
            f.write(r.content)
        return out_path
    except Exception as e:
        print(f"[TTS-GPT异常] {type(e).__name__}: {e}")
        return None


async def _synth_edge_tts(text, lang, idx):
    if not _HAS_EDGE:
        print("[TTS] 未安装 edge-tts，无法降级")
        return None
    out_path = os.path.join(SCRIPT_DIR, f"reply_stream_{int(time.time()*1000)}_{idx}.mp3")
    clean = _clean_text(text)
    if not clean: return None
    try:
        zh_count = len(re.findall(r'[\u4e00-\u9fff]', clean))
        voice = "zh-CN-XiaoxiaoNeural" if zh_count >= 3 else "en-US-AriaNeural"
        comm = edge_tts.Communicate(clean, voice)
        await comm.save(out_path)
        return out_path
    except Exception as e:
        print(f"[TTS-Edge异常] {e}")
        return None


async def synth_one(text, lang, idx, t_start):
    if not text or not text.strip():
        return None
    if USE_GPT_SOVITS:
        path = await _synth_gpt_sovits(text, lang, idx)
        if path:
            return path
        print("[TTS] GPT-SoVITS 失败，降级 Edge-TTS")
    return await _synth_edge_tts(text, lang, idx)