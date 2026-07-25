@echo off
chcp 65001 >nul
title ADX 情绪花 - 传感器信号源
cd /d "%~dp0"

set PY=C:\Python313\python.exe
if not exist "%PY%" set PY=python

echo ============================================
echo   ADX 情绪花 · 原始传感器信号源
echo ============================================
echo.
echo   正在持续写入:
echo   %~dp0live\sensor_live.csv
echo.
echo   TouchDesigner 会自动尾随读取这个文件。
echo   关掉这个窗口就停止送信号（花会退回演示循环）。
echo.
echo   想回放你自己导出的真实 CSV，改成:
echo   python stream_raw_sensor.py --replay "你的文件.csv"
echo.

"%PY%" "E:\AdventureX\touchdesigner\stream_raw_sensor.py" --out "%~dp0live\sensor_live.csv"
pause
