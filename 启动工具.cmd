@echo off
setlocal
if exist "%~dp0runtime\pythonw.exe" (
    set "TCL_LIBRARY=%~dp0runtime\tcl\tcl8.6"
    set "TK_LIBRARY=%~dp0runtime\tcl\tk8.6"
    start "" "%~dp0runtime\pythonw.exe" -B "%~dp0app.py"
    exit /b
)
where pythonw.exe >nul 2>nul
if errorlevel 1 (
    echo Python with Tkinter is required when running from source.
    echo Use the Windows portable ZIP if Python is not installed.
    pause
    exit /b 1
)
start "" pythonw.exe -B "%~dp0app.py"
