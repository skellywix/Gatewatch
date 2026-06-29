@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
echo Starting Gatewatch deployment...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%Deploy-Gatewatch.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
  echo Gatewatch deployment exited with code %EXIT_CODE%.
) else (
  echo Gatewatch deployment command completed.
)
echo.
pause
exit /b %EXIT_CODE%
