@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Орнату аяқталмады. Python 3.10 немесе жаңарақ нұсқасын және интернетті тексеріңіз.
  pause
  exit /b 1
)
echo Дайын. Бір компьютердегі көрсетілім үшін start_demo.bat файлын ашыңыз.
pause
