@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py dashboard --open-browser
if errorlevel 1 pause
