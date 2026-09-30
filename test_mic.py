"""实时显示麦克风音量，用来调阈值"""
import sounddevice as sd
import numpy as np
import time

# 从你的 config.json 读音频设备号
import json
with open(r"F:\Ollama\data\config.json", "r", encoding="utf-8") as f:
    cfg = json.load(f)
device_id = cfg.get("audio_input_device", None)
print(f"使用麦克风设备号: {device_id}")
print("=" * 60)
print("对着麦克风做以下动作，观察音量数字：")
print("  1. 安静不说话（环境噪音）")
print("  2. 正常说话")
print("  3. 说'绯木'（唤醒词）")
print("按 Ctrl+C 退出")
print("=" * 60)

def callback(indata, frames, time_info, status):
    vol = np.mean(np.abs(indata))
    num = int(vol * 1000)
    bar = "█" * min(num // 10, 60)
    print(f"音量: {num:4d}  {bar}   ", end="\r")

try:
    with sd.InputStream(samplerate=16000, channels=1, callback=callback,
                        blocksize=480, device=device_id):
        while True:
            time.sleep(0.1)
except KeyboardInterrupt:
    print("\n\n已退出")
except Exception as e:
    print(f"\n[错误] {e}")