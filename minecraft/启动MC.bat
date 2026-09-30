@echo off
chcp 65001 >nul
cd /d F:\Ollama\minecraft

echo ========================================
echo   绯木 Minecraft Bot
echo ========================================
echo.
echo 在 Minecraft 里：ESC - 对局域网开放
echo 然后把显示的端口号填到下面
echo.
set /p PORT=局域网端口: 

if "%PORT%"=="" (
    echo [错误] 端口不能为空
    pause
    exit /b
)

set MC_PORT=%PORT%
set MC_USERNAME=Feimu
set MC_VERSION=1.21.1
set MC_HOST=localhost

echo.
echo 连接中... 端口 %MC_PORT% 用户名 %MC_USERNAME%
echo.
node bot.js

pause