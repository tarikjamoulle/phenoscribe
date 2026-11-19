@echo off
rem Start Phenoscribe. Double-click this file.
cd /d "%~dp0"
rem Windows knows the Documents folder; OneDrive backup can move it.
set "DOCS="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('MyDocuments')"`) do set "DOCS=%%D"
if not defined DOCS set "DOCS=%USERPROFILE%\Documents"
set "PHENOSCRIBE_OUTPUT_DIR=%DOCS%\Phenoscribe results"

set "UV=uv"
where uv >nul 2>nul
if errorlevel 1 set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" if not "%UV%"=="uv" (
    echo Installing uv ^(one time only^)...
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/0.6.17/install.ps1 | iex" || goto :error
)
"%UV%" run --frozen --no-dev phenoscribe setup || goto :error
"%UV%" run --frozen --no-dev phenoscribe-gui || goto :error
exit /b 0

:error
echo.
echo Phenoscribe stopped with an error. Read the message above.
pause
exit /b 1
