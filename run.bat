@echo off
cd /d "%~dp0"
start "Image Crawler Server" cmd /k "venv\Scripts\python.exe app.py"
ping -n 3 127.0.0.1 >nul
start "" http://127.0.0.1:5000
