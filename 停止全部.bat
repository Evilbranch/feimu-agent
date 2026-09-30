@echo off
chcp 65001 >nul
title 绯木 停止全部
color 0C

echo ========================================
echo   绯木 Minecraft 停止全部
echo ========================================
echo.

echo [1/2] 停止 node (bot.js)...
taskkill /F /IM node.exe >nul 2>&1
if %errorlevel%==0 (
    echo   已停止 bot.js
) else (
    echo   没有 bot.js 在跑
)

echo [2/2] 停止 python (绯木)...
taskkill /F /IM python.exe >nul 2>&1
if %errorlevel%==0 (
    echo   已停止绯木
) else (
    echo   没有绯木在跑
)

echo.
echo ✅ 全部停止
timeout /t 2 /nobreak >nul