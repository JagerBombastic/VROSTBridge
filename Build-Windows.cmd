@echo off
rem Builds and runs VROSTBridge's contract tests natively on Windows with Visual Studio's CMake.
rem The native Windows client itself (VROSTBridgeClient.exe) is cross-built on Linux with
rem scripts\Build-Client-Windows.py (docs\BUILDING.md); the historical diagnostic program is no longer built.
setlocal
cd /d "%~dp0"
set "VROST_CMAKE=cmake"
set "VROST_CTEST=ctest"
where cmake >nul 2>nul
if not errorlevel 1 goto configure
set "VROST_VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VROST_VSWHERE%" goto missing
for /f "usebackq delims=" %%I in (`"%VROST_VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VROST_VS=%%I"
set "VROST_CMAKE=%VROST_VS%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
set "VROST_CTEST=%VROST_VS%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\ctest.exe"
if not exist "%VROST_CMAKE%" goto missing
:configure
"%VROST_CMAKE%" -S . -B build -A x64 -DVROST_BUILD_WINDOWS_CLIENT=OFF
if errorlevel 1 goto failed
"%VROST_CMAKE%" --build build --config Release
if errorlevel 1 goto failed
"%VROST_CTEST%" --test-dir build -C Release --output-on-failure
if errorlevel 1 goto failed
echo Build and contract tests passed.
pause
exit /b 0
:missing
echo Install Visual Studio 2022 with Desktop development with C++ and CMake tools.
pause
exit /b 1
:failed
echo Build or tests failed. Keep the output above for diagnosis.
pause
exit /b 1
