@echo off
title Web Extractor
cd /d "%~dp0"
setlocal

REM 虚拟环境放在项目内，不依赖任何机器的绝对路径
set "VENV=%~dp0.venv"
set "PY=%VENV%\Scripts\python.exe"

if exist "%PY%" goto deps_ok

echo [1/2] Creating local virtual environment in .venv ...
py -3 -m venv "%VENV%" >nul 2>&1
if not exist "%PY%" python -m venv "%VENV%" >nul 2>&1
if not exist "%PY%" (
  echo Python 3 was not found. Install Python 3.9+ from python.org and retry.
  pause
  exit /b 1
)

echo [2/2] Installing dependencies from requirements.txt ...
"%PY%" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed. Check your network and retry.
  pause
  exit /b 1
)
:deps_ok

REM 端口被占用时只复用已有实例，绝不强杀进程（可能属于其它程序）
netstat -aon 2>nul | findstr ":8770" | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo Port 8770 is already in use - opening the running instance.
  echo No process was terminated. If this is an unknown service, close it manually.
  start "" http://127.0.0.1:8770
  timeout /t 2 >nul
  exit /b 0
)

start "" "%PY%" app.py

set /a tries=0
:wait
"%PY%" -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8770/',timeout=1); sys.exit(0)" >nul 2>&1
if not errorlevel 1 goto open
set /a tries=tries+1
if %tries% geq 40 (
  echo Server failed to start. Check app.py and dependencies.
  pause
  exit /b 1
)
timeout /t 1 >nul
goto wait

:open
start "" http://127.0.0.1:8770
echo Server started. Close the server window to stop.
timeout /t 3 >nul
