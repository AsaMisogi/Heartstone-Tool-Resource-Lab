@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
where uv >nul 2>nul
if errorlevel 1 goto python
uv venv --python 3.12 --cache-dir .cache\uv .venv
if errorlevel 1 goto failed
uv pip install --python .venv\Scripts\python.exe --cache-dir .cache\uv -r requirements.lock
if errorlevel 1 goto failed
goto run
:python
py -3.12 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.lock
if errorlevel 1 goto failed
:run
".venv\Scripts\python.exe" -X utf8 -u -m pengpeng %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo [ERROR] 启动失败，请查看上方错误信息。依赖问题可运行 Install-Dependencies.bat 修复。
pause
exit /b 1
