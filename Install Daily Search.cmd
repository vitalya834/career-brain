@echo off
rem Career Brain: turn on the daily automatic search (08:30 and 14:30).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_watch.ps1"
echo.
echo First run starts now...
call "%~dp0Run Watch.cmd"
