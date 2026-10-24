@echo off
cd /d %~dp0
if not exist logs mkdir logs
echo [%date% %time%] kontrol basladi >> logs\check.log
python check_signals.py >> logs\check.log 2>&1
echo [%date% %time%] bitti (kod %errorlevel%) >> logs\check.log
