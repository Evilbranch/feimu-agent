@echo off
chcp 65001 >nul

echo 正在启动 GPT-SoVITS API...
start "GPT-SoVITS API" cmd /k "cd /d F:\GPT-SoVITS-v2-240821 && .\runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS\configs\tts_infer.yaml"

echo 等待 API 加载...
timeout /t 8 /nobreak >nul

echo 正在启动绯木主程序...
start "绯木" cmd /k "cd /d F:\Ollama && C:\Users\29493\AppData\Local\Programs\Python\Python313\python.exe my_ai.py"

echo 完成！两个窗口已启动。
timeout /t 3 /nobreak >nul
exit