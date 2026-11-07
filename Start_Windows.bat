@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 app.py
if errorlevel 1 pause
exit /b
:use_python
where python >nul 2>nul
if errorlevel 1 goto missing
python app.py
if errorlevel 1 pause
exit /b
:missing
echo Install Python 3.11 or newer from https://www.python.org/downloads/windows/
echo Enable Python Launcher and Tcl/Tk during installation.
pause
