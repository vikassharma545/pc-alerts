@echo off
set "PIDFILE=%~dp0marketalert.pid"
if not exist "%PIDFILE%" (
    echo MarketAlert is not running.
    pause
    exit /b
)
set /p PID=<"%PIDFILE%"
taskkill /PID %PID% /F >nul 2>&1
del "%PIDFILE%" >nul 2>&1
echo MarketAlert stopped.
pause
