@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -m mepiti
) else (
  py -3 -m mepiti
)
pause
