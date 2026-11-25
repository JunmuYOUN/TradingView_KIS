@echo off
REM Windows launcher: creates .venv on first run, installs packages, starts the server.
cd /d "%~dp0"
set "PY="
if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY (where py >nul 2>nul && set "PY=py -3")
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo [setup] creating virtual environment .venv ...
  "%PY%" -m venv .venv 2>nul || %PY% -m venv .venv
  if errorlevel 1 ( pause & exit /b 1 )
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 ( pause & exit /b 1 )
set OPEN_BROWSER=1
".venv\Scripts\python.exe" -m server.main
pause
