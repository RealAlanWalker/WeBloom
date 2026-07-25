@echo off
chcp 65001 >nul
title ADX 情绪花 - 真实硬件链路
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "E:\AdventureX\hardware\desktop\start_flower_link.ps1"
echo.
echo 启动器已结束；采集器和 TouchDesigner 会继续在后台运行。
pause
