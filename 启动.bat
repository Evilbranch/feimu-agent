@echo off
chcp 65001 >nul
title 绯木启动器
color 0B

:menu
cls
echo ========================================
echo   绯木启动器
echo ========================================
echo.
echo   [1] 绯木 - 文本模式（推荐调试）
echo   [2] 绯木 - 语音模式
echo   [3] 绯木 - 文本模式 + Minecraft
echo   [4] 绯木 - 语音模式 + Minecraft
echo   [5] 启动 GPT-SoVITS API（启动API.bat）
echo   [6] 停止全部
echo   [0] 退出
echo.
echo ========================================
set /p choice=请输入数字:

if "%choice%"=="1" goto text_mode
if "%choice%"=="2" goto voice_mode
if "%choice%"=="3" goto text_mc
if "%choice%"=="4" goto voice_mc
if "%choice%"=="5" goto start_api
if "%choice%"=="6" goto stop_all
if "%choice%"=="0" exit
goto menu

:: ==================== 纯绯木 ====================
:text_mode
cd /d F:\Ollama
start "绯木-文本" cmd /k "chcp 65001 >nul && py my_ai.py --text"
goto end

:voice_mode
cd /d F:\Ollama
start "绯木-语音" cmd /k "chcp 65001 >nul && py my_ai.py --voice"
goto end

:: ==================== 绯木 + MC ====================
:text_mc
cls
echo.
echo 请先在 Minecraft 里开局域网（ESC - 对局域网开放）
echo.
set /p PORT=端口号: 
if "%PORT%"=="" goto menu
call :_clean_node
cd /d F:\Ollama\minecraft
start "绯木MC-Bot" cmd /k "chcp 65001 >nul && set MC_PORT=%PORT% && node bot.js"
echo 等待 10 秒让 bot 连上 MC...
timeout /t 10 /nobreak >nul
cd /d F:\Ollama
start "绯木-文本" cmd /k "chcp 65001 >nul && py my_ai.py --text"
goto end

:voice_mc
cls
echo.
echo 请先在 Minecraft 里开局域网（ESC - 对局域网开放）
echo.
set /p PORT=端口号: 
if "%PORT%"=="" goto menu
call :_clean_node
cd /d F:\Ollama\minecraft
start "绯木MC-Bot" cmd /k "chcp 65001 >nul && set MC_PORT=%PORT% && node bot.js"
echo 等待 10 秒让 bot 连上 MC...
timeout /t 10 /nobreak >nul
cd /d F:\Ollama
start "绯木-语音" cmd /k "chcp 65001 >nul && py my_ai.py --voice"
goto end

:: ==================== 其他 ====================
:start_api
cd /d F:\Ollama
start "GPT-SoVITS API" cmd /k "启动API.bat"
goto end

:stop_all
call :_clean_node
taskkill /F /IM python.exe >nul 2>&1
echo.
echo ✅ 全部已停止
timeout /t 2 /nobreak >nul
goto menu

:_clean_node
taskkill /F /IM node.exe >nul 2>&1
exit /b

:end
echo.
echo 启动命令已发出，本窗口可以关了
timeout /t 3 /nobreak >nul
exit