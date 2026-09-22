@echo off
chcp 65001 >nul
cd /d "%~dp0"
where uv >nul 2>nul
if not errorlevel 1 (
    if not exist .venv\Scripts\python.exe uv venv --python 3.12 --cache-dir .cache\uv .venv
    uv pip install --python .venv\Scripts\python.exe --cache-dir .cache\uv -r requirements.lock
) else (
    if not exist .venv\Scripts\python.exe py -3.12 -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.lock
)
pause
