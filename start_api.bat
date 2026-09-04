@echo off
rem Windows wrapper for start_api.sh — double-click or run: start_api.bat [port]
rem Usage examples:
rem   start_api.bat            start API on default port 8000
rem   start_api.bat 8080       start API on port 8080
cd /d "%~dp0"
if "%~1"=="" (
    set PORT=8000
) else (
    set PORT=%~1
)
internalenv\Scripts\python.exe -m uvicorn api.app:app --host 0.0.0.0 --port "%PORT%"
pause
