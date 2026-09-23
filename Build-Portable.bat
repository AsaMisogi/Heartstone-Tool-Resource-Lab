@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 请先运行 Start-PengPengWorkbench.bat 或 Install-Dependencies.bat 安装运行环境。
    pause
    exit /b 1
)
where uv >nul 2>nul
if not errorlevel 1 (
    uv pip install --python .venv\Scripts\python.exe --cache-dir .cache\uv -r requirements.lock pyinstaller==6.19.0
) else (
    .venv\Scripts\python.exe -m pip install -r requirements.lock pyinstaller==6.19.0
)
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm PengPengWorkbench.spec
if errorlevel 1 goto :failed
.venv\Scripts\python.exe tools\prepare_release.py
if errorlevel 1 goto :failed
echo 构建输出：dist\砰砰解析台\砰砰解析台.exe
echo 请携带整个 砰砰解析台 文件夹，不要只复制 EXE。
echo 分发前请完成 docs\PACKAGING.md 中的验收与依赖许可检查。
pause
exit /b 0
:failed
echo 构建失败，请查看上方日志。
pause
exit /b 1
