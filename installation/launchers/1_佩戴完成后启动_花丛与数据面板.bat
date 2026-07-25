@echo off
chcp 65001 >nul
title AdventureX 双人实时花丛

echo [AdventureX] 正在连接网关、启动实时采集并将植物归零...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "E:\AdventureX\hardware\desktop\start_flower_link.ps1"
if errorlevel 1 (
  echo.
  echo 启动失败。请检查网关 USB 连接，并查看 live 文件夹中的 error.log。
  pause
  exit /b 1
)

echo.
echo 启动成功。采集器和 TouchDesigner 已在后台持续运行。
echo 请用浏览器打开： http://127.0.0.1:9987/
echo 页面会同时显示双人数据面板和实时粒子花丛。
echo.
pause
