@echo off
rem Start Phenoscribe. Double-click this file.
cd /d "%~dp0"
set "PHENOSCRIBE_OUTPUT_DIR=%USERPROFILE%\Documents\Phenoscribe results"

set "UV=uv"
where uv >nul 2>nul
if errorlevel 1 set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" if not "%UV%"=="uv" (
    echo Installing uv ^(one time only^)...
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex" || goto :error
)
"%UV%" run --frozen phenoscribe setup || goto :error
"%UV%" run --frozen phenoscribe-gui || goto :error
exit /b 0

:error
echo.
echo Phenoscribe stopped with an error. Read the message above.
pause
exit /b 1
