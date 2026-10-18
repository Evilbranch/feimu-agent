@echo off
chcp 65001 >nul
title GPT-SoVITS API

:: ==========================================
:: ★ 请在此处修改为你的 GPT-SoVITS 目录路径
set "TTS_PATH=F:\GPT-SoVITS-v2-240821"
:: ==========================================

cd /d %TTS_PATH%
if errorlevel 1 (
    echo [Error] TTS Path not found. Please edit the TTS_PATH in this file.
    pause
    exit /b
)

.\runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS\configs\tts_infer.yaml
pause