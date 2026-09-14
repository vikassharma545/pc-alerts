@echo off
set "PIDFILE=%~dp0netalert.pid"
if not exist "%PIDFILE%" (
    echo NetAlert is not running.
    pause
    exit /b
)
set /p PID=<"%PIDFILE%"
taskkill /PID %PID% /F >nul 2>&1
del "%PIDFILE%" >nul 2>&1
echo NetAlert stopped.
pause
