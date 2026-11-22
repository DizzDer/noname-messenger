@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p messenger_address="Сервер компьютерінің IPv4 мекенжайын енгізіңіз (мысалы 192.168.1.20): "
if not defined messenger_address exit /b 1
call run_python.bat lan_server.py --address "%messenger_address%"
if errorlevel 1 pause
