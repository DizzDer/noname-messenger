@echo off
chcp 65001 >nul
cd /d "%~dp0"
call run_python.bat -m unittest discover -v
if errorlevel 1 (
  echo Сынақтарда қате анықталды.
  pause
  exit /b 1
)
echo Барлық автоматтандырылған сынақ өтті.
pause
