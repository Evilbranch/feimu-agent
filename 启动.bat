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
echo   5. Start TTS API (skip if running)
echo   6. Start WeChat
echo   7. Stop all
echo   8. Full stack - Text (TTS + Text + WeChat)
echo   9. Full stack - Voice (TTS + Voice + WeChat)
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
if "%choice%"=="8" goto full_text
if "%choice%"=="9" goto full_voice
if "%choice%"=="0" exit
goto menu

:text_mode
call :_kill_main
timeout /t 2 /nobreak >nul
cd /d F:\Ollama
start "Feimu-Text" cmd /k "chcp 65001 >nul && py my_ai.py --text"
goto end

:voice_mode
call :_kill_main
timeout /t 2 /nobreak >nul
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
call :_kill_mc_bot
call :_kill_main
timeout /t 2 /nobreak >nul
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
call :_kill_mc_bot
call :_kill_main
timeout /t 2 /nobreak >nul
cd /d F:\Ollama\minecraft
start "MC-Bot" cmd /k "chcp 65001 >nul && set MC_PORT=%PORT%&& set MC_USERNAME=Feimu&& set MC_VERSION=1.21.1&& set MC_HOST=localhost&& node bot.js"
echo Waiting 10s for bot to connect...
timeout /t 10 /nobreak >nul
cd /d F:\Ollama
start "Feimu-Voice" cmd /k "chcp 65001 >nul && py my_ai.py --voice"
goto end

:start_api
call :_ensure_tts
goto end

:start_wechat
call :_kill_wechat
timeout /t 2 /nobreak >nul
cd /d F:\Ollama
start "Feimu-WeChat" cmd /k "chcp 65001 >nul && py wechat_bot.py"
goto end

:full_text
cls
echo === Full stack: TTS + Text + WeChat ===
echo.
call :_ensure_tts

echo [2/3] Restarting Feimu...
call :_kill_main
call :_kill_wechat
timeout /t 2 /nobreak >nul
cd /d F:\Ollama
start "Feimu-Text" cmd /k "chcp 65001 >nul && py my_ai.py --text"
echo     Waiting 5s...
timeout /t 5 /nobreak >nul

echo [3/3] Starting WeChat...
start "Feimu-WeChat" cmd /k "chcp 65001 >nul && py wechat_bot.py"

echo.
echo ========================================
echo   All launched!
echo ========================================
echo.
echo Closing in 3 seconds...
timeout /t 3 /nobreak >nul
exit

:full_voice
cls
echo === Full stack: TTS + Voice + WeChat ===
echo.
call :_ensure_tts

echo [2/3] Restarting Feimu...
call :_kill_main
call :_kill_wechat
timeout /t 2 /nobreak >nul
cd /d F:\Ollama
start "Feimu-Voice" cmd /k "chcp 65001 >nul && py my_ai.py --voice"
echo     Waiting 5s...
timeout /t 5 /nobreak >nul

echo [3/3] Starting WeChat...
start "Feimu-WeChat" cmd /k "chcp 65001 >nul && py wechat_bot.py"

echo.
echo ========================================
echo   All launched!
echo ========================================
echo.
echo Closing in 3 seconds...
timeout /t 3 /nobreak >nul
exit

:_ensure_tts
echo [1/3] Checking TTS API...
curl -s -o nul -w "%%{http_code}" --max-time 2 http://127.0.0.1:9880/docs > "%TEMP%\_feimu_tts.txt" 2>nul
set /p TTS_CODE=<"%TEMP%\_feimu_tts.txt"
del "%TEMP%\_feimu_tts.txt" >nul 2>&1

if "%TTS_CODE%"=="200" (
    echo     TTS already running - skip.
    exit /b
)

echo     TTS not running - starting...
cd /d F:\GPT-SoVITS-v2-240821
start "TTS API" cmd /k "chcp 65001 >nul && .\runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS\configs\tts_infer.yaml"
echo     Waiting 8s for TTS to load...
timeout /t 8 /nobreak >nul
exit /b

:stop_all
call :_kill_mc_bot
call :_kill_main
call :_kill_wechat
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM node.exe >nul 2>&1
echo.
echo All stopped
timeout /t 2 /nobreak >nul
goto menu

:_kill_main
echo   [kill] Feimu main process...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*my_ai.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
exit /b

:_kill_wechat
echo   [kill] WeChat process...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*wechat_bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
exit /b

:_kill_mc_bot
echo   [kill] MC bot process...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bot.js*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
exit /b

:end
echo.
echo Launched. This window can be closed.
timeout /t 3 /nobreak >nul
exit