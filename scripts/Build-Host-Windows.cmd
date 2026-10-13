@echo off
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Build-Host.ps1
if errorlevel 1 echo Build failed. See docs\BUILDING.md for prerequisites and the error above.
pause
