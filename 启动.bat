@echo off
chcp 65001 >nul
title Feimu Launcher
color 0B

:menu
cls
echo ========================================
echo   Feimu Launcher
echo ========================================
echo.
echo   1. Text mode
echo   2. Voice mode
echo   3. Text mode + Minecraft
echo   4. Voice mode + Minecraft
echo   5. Start TTS API
echo   6. Start WeChat
echo   7. Stop all
echo   0. Exit
echo.
echo ========================================
set /p choice=Enter number: 

if "%choice%"=="1" goto text_mode
if "%choice%"=="2" goto voice_mode
if "%choice%"=="3" goto text_mc
if "%choice%"=="4" goto voice_mc
if "%choice%"=="5" goto start_api
if "%choice%"=="6" goto start_wechat
if "%choice%"=="7" goto stop_all
if "%choice%"=="0" exit
goto menu

:text_mode
cd /d F:\Ollama
start "Feimu-Text" cmd /k "chcp 65001 >nul && py my_ai.py --text"
goto end

:voice_mode
cd /d F:\Ollama
start "Feimu-Voice" cmd /k "chcp 65001 >nul && py my_ai.py --voice"
goto end

:text_mc
cls
echo.
echo Open LAN world in Minecraft first
echo.
set /p PORT=Port: 
if "%PORT%"=="" goto menu
call :_clean_node
cd /d F:\Ollama\minecraft
start "MC-Bot" cmd /k "chcp 65001 >nul && set MC_PORT=%PORT%&& set MC_USERNAME=Feimu&& set MC_VERSION=1.21.1&& set MC_HOST=localhost&& node bot.js"
echo Waiting 10s for bot to connect...
timeout /t 10 /nobreak >nul
cd /d F:\Ollama
start "Feimu-Text" cmd /k "chcp 65001 >nul && py my_ai.py --text"
goto end

:voice_mc
cls
echo.
echo Open LAN world in Minecraft first
echo.
set /p PORT=Port: 
if "%PORT%"=="" goto menu
call :_clean_node
cd /d F:\Ollama\minecraft
start "MC-Bot" cmd /k "chcp 65001 >nul && set MC_PORT=%PORT%&& set MC_USERNAME=Feimu&& set MC_VERSION=1.21.1&& set MC_HOST=localhost&& node bot.js"
echo Waiting 10s for bot to connect...
timeout /t 10 /nobreak >nul
cd /d F:\Ollama
start "Feimu-Voice" cmd /k "chcp 65001 >nul && py my_ai.py --voice"
goto end

:start_api
cd /d F:\GPT-SoVITS-v2-240821
start "TTS API" cmd /k "chcp 65001 >nul && .\runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS\configs\tts_infer.yaml"
goto end

:start_wechat
cd /d F:\Ollama
start "Feimu-WeChat" cmd /k "chcp 65001 >nul && py wechat_bot.py"
goto end

:stop_all
call :_clean_node
taskkill /F /IM python.exe >nul 2>&1
echo.
echo All stopped
timeout /t 2 /nobreak >nul
goto menu

:_clean_node
taskkill /F /IM node.exe >nul 2>&1
exit /b

:end
echo.
echo Launched. This window can be closed.
timeout /t 3 /nobreak >nul
exit