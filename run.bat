@echo off
cd /d "%~dp0"
start "Image Crawler Server" cmd /k "venv\Scripts\python.exe app.py"
start "Extractor Worker" cmd /k "venv\Scripts\python.exe extractor_worker.py > extractor_worker.log 2>&1"
start "Download Worker" cmd /k "venv\Scripts\python.exe download_worker.py > download_worker.log 2>&1"
ping -n 3 127.0.0.1 >nul
start "" http://127.0.0.1:5000
