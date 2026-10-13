@echo off
cd /d "%~dp0.."
python launcher\app.py --role host
if errorlevel 1 pause
