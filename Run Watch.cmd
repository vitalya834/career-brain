@echo off
rem Career Brain: one search run. Double-click to run now; the scheduler calls it with "quiet".
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
set PY=
python -c "pass" >nul 2>nul && set PY=python
if not defined PY py -3 -c "pass" >nul 2>nul && set PY=py -3
if not defined PY if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set PY="%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not defined PY (
  echo %date% %time% FAIL Python not found>>watch.log
  echo Python not found. Install Python 3.11+ from python.org and run again.
  if /i not "%~1"=="quiet" pause
  exit /b 9
)
%PY% -X utf8 career.py watch
set RESULT=%errorlevel%
if /i not "%~1"=="quiet" pause
exit /b %RESULT%
