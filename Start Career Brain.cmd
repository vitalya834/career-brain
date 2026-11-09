@echo off
cd /d "%~dp0"
echo Open http://127.0.0.1:8765 in your browser. Keep this window open.
set PYTHONDONTWRITEBYTECODE=1
python -X utf8 career.py serve
if errorlevel 1 pause
