# 构建与发布

## 源码与仓库范围

Windows x64，Python 3.12–3.13；依赖版本锁定在 requirements.lock。
源码、tests、构建入口、必要的 tools 与功能和发布文档提交 Git。
tests 是离线回归测试，tools 保留构建与可重复验收入口；不放进免安装包。
历史设计/验收记录及一次性调试脚本仅保留本地并由 .gitignore 排除。
首页正在使用的截图保留；workspace、模型、游戏资源、缓存、build、dist、ZIP 均不提交源码仓库。

## 构建

1. 运行 Install-Dependencies.bat 准备 .venv。
2. 运行 Build-Portable.bat。脚本安装锁定依赖与 PyInstaller 6.19.0，下载固定的中英文 Vosk 模型，逐个实际加载验证，然后打包。
3. 下载进度显示已完成 MiB。下载失败会停止构建；再次运行重试。正式程序不会自动联网下载模型。
4. 输出 dist/砰砰解析台/，包含 EXE 与 _internal。必须分发整个目录，不用 onefile；保留控制台和 Qt 动态运行库。
5. tools/prepare_release.py 补齐使用说明、Python/项目许可、第三方声明、依赖锁、版本和逐文件 SHA256SUMS.txt。

手动构建依次执行：

    .venv/Scripts/python.exe -X utf8 tools/prepare_models.py
    .venv/Scripts/python.exe -m PyInstaller --clean --noconfirm PengPengWorkbench.spec
    .venv/Scripts/python.exe -X utf8 tools/prepare_release.py

模型放在 pengpeng/models，spec 将其复制到 _internal/pengpeng/models。
缺模型时 spec 立即失败；不要跳过构建脚本的真实加载检查。
许可证在 licenses/VOSK_MODELS_APACHE-2.0.txt，随包复制到 _internal/licenses。

## 发布验收

    .venv/Scripts/python.exe -m pytest -q
    node --check pengpeng/web/app.js
    node --check pengpeng/web/interaction.js
    node --test tests/interaction.test.cjs tests/scrolling.test.cjs
    .venv/Scripts/python.exe -X utf8 tools/gui_experience.py
    .venv/Scripts/python.exe -X utf8 tools/gui_experience.py --interactions
    .venv/Scripts/python.exe -X utf8 tools/gui_experience.py --upgrade
    .venv/Scripts/python.exe -X utf8 tools/verify_08.py
    .venv/Scripts/python.exe -X utf8 tools/verify_10.py
    .venv/Scripts/python.exe -X utf8 tools/gui_scroll11.py
    .venv/Scripts/python.exe -X utf8 tools/gui_first_run.py
    .venv/Scripts/python.exe -X utf8 tools/gui_speech_settings.py

使用独立工作区验证 EXE 首次启动、模型状态检查、中文路径和空格路径、真实语音识别、关闭进程清理。测试工作区、导出及日志须留在 .cache，不能混入发行目录。
`verify_08.py` 与 GUI 回归默认使用 `.cache/qa-05-workspace`；需本机已安装炉石，
该工作区需有相应测试缓存。`--upgrade` 覆盖原生滚轮动画、快速页签切换、旧错误隔离、
语音归属、重播、英雄配乐、战棋与衍生卡集合。更新弹窗采用受控响应，快照差分由
离线测试验证。新旧完整索引不足时不应通过伪造 NEW 来让验收“通过”。
可使用 --workspace、--quit-after、--screenshot 参数进行启动检查。
只清空开发工具 PATH 不等于全新 Windows 虚拟机验收，发布说明须准确描述已做的验证。

版本同步更新 pyproject.toml、pengpeng/__init__.py 和 pengpeng/web 中的界面版本文本。
提交并推送发布源码，完成正式构建与验收后，将完整目录压缩为
PengPengWorkbench-v<版本>-windows-x64.zip，并生成 ZIP 的 SHA-256 附件。
GitHub Release 标签采用 v<版本>，正文仅使用 README 中去重后的本版更新日志；ZIP 上传到 Release，不提交 Git。

## 运行库许可

项目 MIT 不覆盖第三方二进制与游戏素材。保留 THIRD_PARTY_NOTICES.md、依赖元数据和许可。
Qt/PySide6 以动态库形式分发；FMOD 适用其自身许可。内置 Vosk 模型为 Apache 2.0。
压缩包不包含炉石资源、API 密钥或用户工作区。

0.11 的系统滚轮适配、未知时间叠放约定、分类修正及本机测量见 [SCROLLING_AND_AUDIO.md](SCROLLING_AND_AUDIO.md)。包内保留更新说明与这份技术说明。

0.11.1 内容重绘对照使用 tools/profile_scroll_paint.py；仅测试时启用本机临时调试端口，
启动步骤与 Paint 指标解释见 SCROLLING_AND_AUDIO.md，正式包不配置调试端口。

## 0.12 原生浏览器验收

默认主界面使用系统已安装的 Microsoft Edge WebView2 Runtime，Qt 官方插件随包。
Runtime 不复制进 ZIP；未安装时自动使用 Qt 兼容引擎。spec 显式收集 QtWebView
QML 模块、Qt6WebView/Quick DLL 和 Windows 插件，避免依赖开发机插件目录。

    .venv/Scripts/python.exe -X utf8 tools/gui_native12.py
    node --test tests/host.test.cjs tests/interaction.test.cjs tests/scrolling.test.cjs

旧 Qt 滚轮、绘制工具需在独立 PowerShell 中先设置 `$env:PENGPENG_RENDERER='qt'`。
原生工具会短暂在测试窗口内发送鼠标输入，退出恢复光标。新增原生 HWND 后，
必须检查实际卡图页和音频播放，不能只以进程启动成功作为包内插件验收。
