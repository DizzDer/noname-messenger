@echo off
chcp 65001 >nul
cd /d "%~dp0"
call run_python.bat demo.py
if errorlevel 1 pause

