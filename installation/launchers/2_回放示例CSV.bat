@echo off
chcp 65001 >nul
title ADX 情绪花 - 回放示例 CSV
cd /d "%~dp0"

set "PY=C:\Python313\python.exe"
if not exist "%PY%" set "PY=python"
set "SOURCE=D:\常用软件\xwechat_files\wxid_p06lmvotchxu12_27c6\msg\file\2026-07\sensor_data_20260725_003324.csv"
set "OUTPUT=%~dp0live\sensor_live.csv"

echo 正在循环回放示例 CSV，并持续驱动萌芽、长枝、开花、盛放和呼吸。
echo 输出：%OUTPUT%
echo 关闭本窗口可停止回放。
echo.

"%PY%" "E:\AdventureX\touchdesigner\stream_raw_sensor.py" --replay "%SOURCE%" --out "%OUTPUT%" --speed 1.0
pause
