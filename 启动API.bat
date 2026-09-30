@echo off
chcp 65001 >nul
title GPT-SoVITS API
cd /d F:\GPT-SoVITS-v2-240821
.\runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS\configs\tts_infer.yaml
pause