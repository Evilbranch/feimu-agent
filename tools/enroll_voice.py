"""声纹注册工具 - 录制并注册你的声纹"""
import os
import sys
import time
import sounddevice as sd
import numpy as np
from scipy.io.wavfile import write

sys.path.insert(0, r"F:\Ollama")
from voice.speaker_id import enroll

SAMPLE_DIR = r"F:\Ollama\data\voice_samples"
os.makedirs(SAMPLE_DIR, exist_ok=True)

SAMPLE_COUNT = 3
SAMPLE_DURATION = 6  # 秒


def record_sample(index):
    """录制一条声纹样本"""
    print(f"\n=== 第 {index}/{SAMPLE_COUNT} 条样本 ===")
    print(f"请用正常音量说一段话（{SAMPLE_DURATION} 秒），比如：")
    print("  '绯木，今天天气怎么样，帮我看看窗外有没有下雨。'")
    print("  '你好绯木，我现在有点累，想聊聊天。'")
    print("  '绯木，帮我打开微信，然后记一下明天要开会。'")
    print("\n准备开始...")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    print("🎤 开始录音！")

    fs = 16000
    recording = sd.rec(int(SAMPLE_DURATION * fs), samplerate=fs,
                       channels=1, dtype='int16')
    sd.wait()
    print("✅ 录音完成")

    path = os.path.join(SAMPLE_DIR, f"sample_{index}.wav")
    write(path, fs, recording)
    return path


def main():
    print("=" * 50)
    print("绯木声纹注册")
    print("=" * 50)
    print(f"\n将录制 {SAMPLE_COUNT} 条样本，每条 {SAMPLE_DURATION} 秒。")
    print("请确保环境安静，用你平时说话的音量和语气。\n")

    input("按回车开始...")

    paths = []
    for i in range(1, SAMPLE_COUNT + 1):
        path = record_sample(i)
        paths.append(path)

    print("\n正在提取声纹...")
    try:
        enroll("主人", paths)
        print("\n🎉 声纹注册成功！")
        print(f"样本保存在：{SAMPLE_DIR}")
        print(f"声纹库保存在：F:\\Ollama\\data\\speakers")
    except Exception as e:
        print(f"\n❌ 注册失败：{e}")


if __name__ == "__main__":
    main()