"""声纹识别 - 支持多用户注册与识别"""
import os
import json

SPEAKER_BOOK = r"F:\Ollama\data\speakers"
SPEAKER_META = r"F:\Ollama\data\speakers_meta.json"

_recognizer = None


def _get_recognizer():
    global _recognizer
    if _recognizer is None:
        from voicefingerprint import VoiceRecognizer
        _recognizer = VoiceRecognizer()
        if os.path.exists(SPEAKER_BOOK):
            try:
                _recognizer.load(SPEAKER_BOOK)
                print(f"[声纹] 已加载声纹库")
            except Exception as e:
                print(f"[声纹] 加载失败：{e}")
    return _recognizer


def _load_meta():
    if not os.path.exists(SPEAKER_META):
        return {}
    try:
        with open(SPEAKER_META, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def _save_meta(meta):
    os.makedirs(os.path.dirname(SPEAKER_META), exist_ok=True)
    with open(SPEAKER_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def enroll(name, audio_paths, relation="主人"):
    """注册声纹"""
    r = _get_recognizer()
    r.enroll(name, audio_paths)
    r.save(SPEAKER_BOOK)
    meta = _load_meta()
    meta[name] = relation
    _save_meta(meta)
    print(f"[声纹] ✅ 已注册 {name}（关系：{relation}），{len(audio_paths)} 条样本")


def identify(audio_path):
    """识别音频属于谁。返回 (说话人名, 关系类型, 相似度)"""
    r = _get_recognizer()
    if not os.path.exists(SPEAKER_BOOK):
        return None, None, 0.0
    try:
        results = r.identify(audio_path)
        if results and len(results) > 0:
            best = results[0]
            name = best.speaker
            score = best.score
            if score > 0.55:
                meta = _load_meta()
                relation = meta.get(name, "朋友")
                return name, relation, score
            else:
                return None, None, score
        return None, None, 0.0
    except Exception as e:
        print(f"[声纹] 识别异常：{e}")
        return None, None, 0.0


def is_enrolled():
    return os.path.exists(SPEAKER_BOOK)


def list_speakers():
    meta = _load_meta()
    if not meta:
        return "还没有注册任何声纹。"
    lines = [f"· {name}（{rel}）" for name, rel in meta.items()]
    return "已注册说话人：\n" + "\n".join(lines)