@echo off
chcp 65001 >nul
title НЕКТО — анонимный чат server
cd /d "%~dp0"
echo Запускаю анонимный чат...
echo Сайт: http://localhost:8080
echo Остановить: закройте это окно
python server.py
pause