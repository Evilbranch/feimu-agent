"""麦克风设备选择 + 自动识别耳机/音箱"""
import sounddevice as sd


def _list():
    ds = sd.query_devices()
    return [{"index": i, "name": d["name"]} for i, d in enumerate(ds)
            if d.get("max_input_channels", 0) > 0]


def choose_audio_device(config):
    saved = config.get("audio_input_device")
    if saved is not None:
        try:
            for d in _list():
                if d["index"] == saved:
                    print(f"🎙️ 麦克风：{d['name']}（{saved}）")
                    return saved
        except: pass
    ds = _list()
    if not ds: print("[错误] 没找到录音设备"); return None
    print("\n" + "=" * 60); print("🎙️ 选择麦克风："); print("=" * 60)
    for d in ds: print(f"  [{d['index']:>2}] {d['name']}")
    print("=" * 60)
    try: c = input("序号（回车跳过）：").strip()
    except: c = ""
    if c == "": return None
    try:
        idx = int(c)
        if not any(d["index"] == idx for d in ds): return None
        config["audio_input_device"] = idx
        return idx
    except: return None


def get_default_output_index():
    """返回当前 Windows 默认播放设备的 index"""
    try:
        d = sd.query_devices(kind='output')
        all_d = sd.query_devices()
        for i, dev in enumerate(all_d):
            if dev['name'] == d['name'] and dev['max_output_channels'] > 0:
                return i
    except: pass
    return -1


def detect_audio_mode(config):
    """自动检测：外放设备名单 → speaker，其他 → headphone"""
    SPEAKER_KW = [
        # ===== 通用外放词 =====
        "扬声器", "speaker", "音箱", "喇叭",

        # ===== 主板/显卡音频（通常是机箱外放） =====
        "realtek", "nvidia", "amd hd audio", "intel audio",

        # ===== HDMI / DisplayPort（显示器/电视音箱） =====
        "hdmi", "displayport", "display port",

        # ===== 显示器 / 电视 =====
        "monitor", "display", "显示器", "电视", "television",

        # ===== 常见显示器型号（你电脑上有这两个） =====
        "q24t09", "erazer",

        # ===== 内置扬声器 =====
        "内置", "built-in", "internal",

        # ===== 投影 =====
        "projector", "投影",

        # ===== 常见音箱品牌（有风险，见下方提醒） =====
        "jbl", "marshall", "sonos", "harman", "harman/kardon",
        "soundbar", "回音壁", "漫步者", "edifier",
        "小爱", "天猫精灵", "echo dot", "google home", "homepod",
    ]

    VIRTUAL_KW = ["cable", "vb-audio", "virtual"]

    try:
        d = sd.query_devices(kind='output')
        name = d['name'].lower()

        # 虚拟声卡 → 当耳机模式（用 VSeeFace 时一般戴耳机）
        for kw in VIRTUAL_KW:
            if kw in name:
                return "headphone"

        # 外放关键词
        for kw in SPEAKER_KW:
            if kw in name:
                return "speaker"

        # 其他所有名字（品牌耳机）→ headphone
        return "headphone"
    except:
        return "speaker"